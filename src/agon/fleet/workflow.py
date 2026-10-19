"""W-fulltime as one Microsoft Agent Framework 1.22.0 `WorkflowBuilder` graph (spec 3.3, 3.5, 4).

Shape: a sequential spine; one bounded retry edge from `factgate` back to `writer`; a concurrent
fan-out from `desk_editor` to `localise_el`, `localise_es`, `localise_fr` and `easy_read` with a
fan-in barrier at `factgate_lang`; and a human-in-the-loop pause at `approval`, which calls
`ctx.request_info(ApprovalRequest)` and whose response handler delegates to the one approval
verifier, `agon.governance.verifier.verify`.

How the routing works: Agent Framework delivers a message along an edge only if the target has a
handler for its type (`_edge_runner.py`, `SingleEdgeRunner.send_message`, `_can_handle`). So
`factgate` sends a `WriterRetry` (only `writer` handles it) or a `Checked` (only `desk_editor`
handles it), and the retry is bounded by `MAX_WRITER_ATTEMPTS` inside `steps.factgate`.

Why the graph never changes with configuration: the checkpoint's graph signature hashes every
executor id and class. A switched-off Desk Editor still runs and adds a "not run" flag, so a
checkpoint taken under one configuration resumes under another.

Why every executor class is defined at module level in this module: process A and process B
must compute the same graph signature (`_workflow.py` `_compute_graph_signature`), which reads
each executor's ``__module__`` and class name.

Why no ``from __future__ import annotations``: Agent Framework reads the handler annotations at
runtime to infer message and output types, and the upstream samples keep them real.
"""

from typing import Any

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler, response_handler
from typing_extensions import Never

from agon.fleet import steps
from agon.fleet.contracts import (
    TRANSLATED_LANGUAGES,
    WORKFLOW_NAME,
    Analysed,
    ApprovalRequest,
    Checked,
    DecisionEnvelope,
    Edited,
    Ingested,
    Interpreted,
    LanguageChecked,
    Localised,
    PublishRequest,
    RightsChecked,
    RunInput,
    RunOutcome,
    SealedDraft,
    Triaged,
    Voiced,
    WriterRetry,
    Written,
)
from agon.fleet.deps import FleetDeps

STATE_SEALED_MANIFEST = "agon_sealed_manifest_json"
"""Workflow state key holding the exact sealed manifest, so the response handler re-checks the bytes."""


class _FleetExecutor(Executor):
    """Common constructor: a fixed id and the injected dependencies (never checkpointed)."""

    def __init__(self, deps: FleetDeps, executor_id: str) -> None:
        super().__init__(id=executor_id)
        self._deps = deps

    def _trace(self, step: str = "handler") -> None:
        self._deps.trace(self.id, step)


class IngestExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "ingest")

    @handler
    async def on_run(self, run: RunInput, ctx: WorkflowContext[Ingested, RunOutcome]) -> None:
        self._trace()
        result = await steps.ingest(self._deps, run)
        if isinstance(result, RunOutcome):
            await ctx.yield_output(result)
            return
        await ctx.send_message(Ingested(result))


class InterpretExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "interpret")

    @handler
    async def on_ingested(self, msg: Ingested, ctx: WorkflowContext[Interpreted]) -> None:
        self._trace()
        await ctx.send_message(Interpreted(await steps.interpret(self._deps, msg.draft)))


class AnalystExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "analyst")

    @handler
    async def on_interpreted(self, msg: Interpreted, ctx: WorkflowContext[Analysed]) -> None:
        self._trace()
        await ctx.send_message(Analysed(await steps.analyst(self._deps, msg.draft)))


class WriterExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "writer")

    @handler
    async def on_analysed(self, msg: Analysed, ctx: WorkflowContext[Written]) -> None:
        self._trace("first")
        await ctx.send_message(Written(await steps.writer(self._deps, msg.draft)))

    @handler
    async def on_retry(self, msg: WriterRetry, ctx: WorkflowContext[Written]) -> None:
        self._trace("retry")
        await ctx.send_message(Written(await steps.writer(self._deps, msg.draft, retry=True)))


class TriageExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "triage")

    @handler
    async def on_written(self, msg: Written, ctx: WorkflowContext[Triaged]) -> None:
        self._trace()
        await ctx.send_message(Triaged(await steps.triage(self._deps, msg.draft)))


class FactGateExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "factgate")

    @handler
    async def on_triaged(self, msg: Triaged, ctx: WorkflowContext[WriterRetry | Checked]) -> None:
        self._trace()
        kind, draft = await steps.factgate(self._deps, msg.draft)
        await ctx.send_message(WriterRetry(draft) if kind == "retry" else Checked(draft))


class DeskEditorExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "desk_editor")

    @handler
    async def on_checked(self, msg: Checked, ctx: WorkflowContext[Edited]) -> None:
        self._trace()
        await ctx.send_message(Edited(await steps.desk_editor(self._deps, msg.draft)))


class LocaliseExecutor(_FleetExecutor):
    """One class, three instances (el, es, fr), each with its own fixed id."""

    def __init__(self, deps: FleetDeps, language: str) -> None:
        super().__init__(deps, f"localise_{language}")
        self._language = language

    @handler
    async def on_edited(self, msg: Edited, ctx: WorkflowContext[Localised]) -> None:
        self._trace()
        await ctx.send_message(await steps.localise(self._deps, msg.draft, self._language))


class EasyReadExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "easy_read")

    @handler
    async def on_edited(self, msg: Edited, ctx: WorkflowContext[Localised]) -> None:
        self._trace()
        await ctx.send_message(await steps.easy_read(self._deps, msg.draft))


class FactGateLangExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "factgate_lang")

    @handler
    async def on_editions(self, msgs: list[Localised], ctx: WorkflowContext[LanguageChecked]) -> None:
        self._trace()
        await ctx.send_message(LanguageChecked(await steps.factgate_lang(self._deps, list(msgs))))


class RightsExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "rights")

    @handler
    async def on_checked(self, msg: LanguageChecked, ctx: WorkflowContext[RightsChecked]) -> None:
        self._trace()
        await ctx.send_message(RightsChecked(await steps.rights(self._deps, msg.draft)))


class AudioExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "audio")

    @handler
    async def on_rights(self, msg: RightsChecked, ctx: WorkflowContext[Voiced]) -> None:
        self._trace()
        await ctx.send_message(Voiced(await steps.audio(self._deps, msg.draft)))


class SealExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "seal")

    @handler
    async def on_voiced(self, msg: Voiced, ctx: WorkflowContext[SealedDraft]) -> None:
        self._trace()
        await ctx.send_message(await steps.seal_step(self._deps, msg.draft))


class ApprovalExecutor(_FleetExecutor):
    """Pauses on `request_info`; the decision may arrive in a different process, hours later."""

    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "approval")

    @handler
    async def on_sealed(self, msg: SealedDraft, ctx: WorkflowContext[Never, RunOutcome]) -> None:
        self._trace("request")
        prepared = await steps.prepare_approval(self._deps, msg)
        if isinstance(prepared, RunOutcome):
            await ctx.yield_output(prepared)
            return
        # Committed at the superstep boundary, before the pause checkpoint is written, so the
        # response handler in a fresh process recomputes the hash from these exact bytes.
        ctx.set_state(STATE_SEALED_MANIFEST, msg.manifest_json)
        await ctx.request_info(request_data=prepared, response_type=DecisionEnvelope)

    @response_handler
    async def on_decision(
        self,
        original_request: ApprovalRequest,
        decision: DecisionEnvelope,
        ctx: WorkflowContext[PublishRequest, RunOutcome],
    ) -> None:
        self._trace("response")
        result = await steps.decide(self._deps, original_request, decision, ctx.get_state(STATE_SEALED_MANIFEST))
        if isinstance(result, PublishRequest):
            await ctx.send_message(result)
        else:
            await ctx.yield_output(result)


class PublishRequestExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "publish_request")

    @handler
    async def on_request(self, msg: PublishRequest, ctx: WorkflowContext[Never, RunOutcome]) -> None:
        self._trace()
        await ctx.yield_output(await steps.enqueue_publish(self._deps, msg))


def build_workflow(deps: FleetDeps, storage: Any) -> Workflow:
    """The same graph in every process. `storage` is any Agent Framework CheckpointStorage."""
    ingest = IngestExecutor(deps)
    interpret = InterpretExecutor(deps)
    analyst = AnalystExecutor(deps)
    writer = WriterExecutor(deps)
    triage = TriageExecutor(deps)
    factgate = FactGateExecutor(deps)
    desk_editor = DeskEditorExecutor(deps)
    branches: list[Executor] = [LocaliseExecutor(deps, lang) for lang in TRANSLATED_LANGUAGES]
    branches.append(EasyReadExecutor(deps))
    factgate_lang = FactGateLangExecutor(deps)
    rights = RightsExecutor(deps)
    audio = AudioExecutor(deps)
    seal = SealExecutor(deps)
    approval = ApprovalExecutor(deps)
    publish = PublishRequestExecutor(deps)
    return (
        WorkflowBuilder(
            name=WORKFLOW_NAME,
            start_executor=ingest,
            checkpoint_storage=storage,
            output_from=[ingest, approval, publish],
        )
        .add_edge(ingest, interpret)
        .add_edge(interpret, analyst)
        .add_edge(analyst, writer)
        .add_edge(writer, triage)
        .add_edge(triage, factgate)
        .add_edge(factgate, writer)
        .add_edge(factgate, desk_editor)
        .add_fan_out_edges(desk_editor, branches)
        .add_fan_in_edges(branches, factgate_lang)
        .add_edge(factgate_lang, rights)
        .add_edge(rights, audio)
        .add_edge(audio, seal)
        .add_edge(seal, approval)
        .add_edge(approval, publish)
        .build()
    )
