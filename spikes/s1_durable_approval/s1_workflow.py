"""Spike S1 workflow: draft, seal, approval, built with Microsoft Agent Framework 1.22.0.

Why one module for every executor and the builder: the checkpoint's graph signature hashes each
executor's ``__module__`` and class name (``_workflow.py`` line 1331 at tag python-1.22.0). Process
A and process B both import this module under the same name, so the signature matches and the
restore is accepted. Defining an executor in an entry script would make it ``__main__`` in one
process and something else in the other.

Why no ``from __future__ import annotations`` here: the framework reads handler annotations to
infer message and output types, and the upstream samples use real annotations. Keeping them real
removes one way for the build to differ from the samples.
"""

from pathlib import Path

from agent_framework import (
    Executor,
    FileCheckpointStorage,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from typing_extensions import Never

from s1_bundle import (
    ALLOWED_CHECKPOINT_TYPES,
    PUBLISHED,
    REFUSED,
    REFUSED_ALREADY_PUBLISHED,
    STATE_KEY_CANONICAL,
    WORKFLOW_NAME,
    ApprovalDecision,
    ApprovalRequest,
    DraftBundle,
    PublishOutcome,
    SealedBundle,
    append_trace,
    make_draft,
    publish_create_only,
    seal,
    verify_decision,
)

PUBLISH_FILE = "publish.json"


class DraftExecutor(Executor):
    """Turns a match id into a draft bundle. Stands in for the model-backed writer."""

    def __init__(self, run_dir: Path) -> None:
        super().__init__(id="draft")
        self._run_dir = run_dir

    @handler
    async def on_match(self, match_id: str, ctx: WorkflowContext[DraftBundle]) -> None:
        append_trace(self._run_dir, self.id, "handler")
        await ctx.send_message(make_draft(match_id))


class SealExecutor(Executor):
    """Computes the canonical bytes and their sha256, and keeps the bytes in workflow state."""

    def __init__(self, run_dir: Path) -> None:
        super().__init__(id="seal")
        self._run_dir = run_dir

    @handler
    async def on_draft(self, draft: DraftBundle, ctx: WorkflowContext[SealedBundle]) -> None:
        append_trace(self._run_dir, self.id, "handler")
        sealed = seal(draft)
        # Workflow state is committed at the superstep boundary and written into every later
        # checkpoint, so process B can recompute the hash from these exact bytes.
        ctx.set_state(STATE_KEY_CANONICAL, sealed.canonical_json)
        await ctx.send_message(sealed)


class ApprovalExecutor(Executor):
    """Asks a human through request_info, then publishes once if the answer checks out."""

    def __init__(self, run_dir: Path, nonce: str) -> None:
        super().__init__(id="approval")
        self._run_dir = run_dir
        self._nonce = nonce

    @handler
    async def on_sealed(self, sealed: SealedBundle, ctx: WorkflowContext) -> None:
        append_trace(self._run_dir, self.id, "request")
        await ctx.request_info(
            request_data=ApprovalRequest(bundle_sha256=sealed.bundle_sha256, nonce=self._nonce),
            response_type=ApprovalDecision,
        )

    @response_handler
    async def on_decision(
        self,
        original_request: ApprovalRequest,
        decision: ApprovalDecision,
        ctx: WorkflowContext[Never, PublishOutcome],
    ) -> None:
        append_trace(self._run_dir, self.id, "response")
        ok, reason = verify_decision(original_request, decision, ctx.get_state(STATE_KEY_CANONICAL))
        if not ok:
            await ctx.yield_output(PublishOutcome(REFUSED, original_request.bundle_sha256, reason))
            return
        payload = {
            "bundle_sha256": original_request.bundle_sha256,
            "canonical_json": ctx.get_state(STATE_KEY_CANONICAL),
            "nonce": original_request.nonce,
        }
        try:
            publish_create_only(self._run_dir / PUBLISH_FILE, payload)
        except FileExistsError:
            await ctx.yield_output(
                PublishOutcome(REFUSED_ALREADY_PUBLISHED, original_request.bundle_sha256, "publish.json exists")
            )
            return
        append_trace(self._run_dir, self.id, "publish")
        await ctx.yield_output(PublishOutcome(PUBLISHED, original_request.bundle_sha256, PUBLISH_FILE))


def make_storage(checkpoint_dir: Path) -> FileCheckpointStorage:
    """File checkpoint storage with the explicit allow-list both processes must share."""
    return FileCheckpointStorage(checkpoint_dir, allowed_checkpoint_types=list(ALLOWED_CHECKPOINT_TYPES))


def build_workflow(storage: FileCheckpointStorage, run_dir: Path, nonce: str = "") -> Workflow:
    """Assemble the same graph in both processes.

    The nonce is only used when ``approval`` issues the request, which happens in process A.
    Process B restores the request (with A's nonce) from the checkpoint, so it passes nothing.
    """
    draft = DraftExecutor(run_dir)
    sealer = SealExecutor(run_dir)
    approval = ApprovalExecutor(run_dir, nonce)
    return (
        WorkflowBuilder(
            name=WORKFLOW_NAME,
            start_executor=draft,
            checkpoint_storage=storage,
            output_from=[approval],
        )
        .add_edge(draft, sealer)
        .add_edge(sealer, approval)
        .build()
    )
