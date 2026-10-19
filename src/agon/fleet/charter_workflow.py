"""W-charter as one Microsoft Agent Framework 1.22.0 `WorkflowBuilder` graph (spec 3.3).

Shape: a sequential spine with fixed executor ids, `charter_build` then `charter_check` then
`seal` then `approval` then `publish_request`. The approval and publish executors are W-fulltime's
own classes (agon.fleet.workflow): `approval` calls `ctx.request_info(ApprovalRequest)` and its
response handler delegates to the one approval verifier, so a charter waits in a durable
checkpoint exactly as a full-time report does and resumes in a fresh process after a verified
decision. The graph has its own workflow name, so its checkpoints never mix with W-fulltime's.

Why every new executor class is defined at module level here, and why there is no
``from __future__ import annotations``: see agon.fleet.workflow (graph signature and runtime
handler annotations).
"""

from typing import Any

from agent_framework import Workflow, WorkflowBuilder, WorkflowContext, handler

from agon.fleet import charter_steps
from agon.fleet.charter_contracts import WORKFLOW_NAME, CharterBuilt, CharterChecked
from agon.fleet.contracts import RunInput, RunOutcome, SealedDraft
from agon.fleet.deps import FleetDeps
from agon.fleet.workflow import ApprovalExecutor, PublishRequestExecutor, _FleetExecutor


class CharterBuildExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "charter_build")

    @handler
    async def on_run(self, run: RunInput, ctx: WorkflowContext[CharterBuilt, RunOutcome]) -> None:
        self._trace()
        result = await charter_steps.charter_build(self._deps, run)
        if isinstance(result, RunOutcome):
            await ctx.yield_output(result)
            return
        await ctx.send_message(CharterBuilt(result))


class CharterCheckExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "charter_check")

    @handler
    async def on_built(self, msg: CharterBuilt, ctx: WorkflowContext[CharterChecked]) -> None:
        self._trace()
        await ctx.send_message(CharterChecked(await charter_steps.charter_check_step(self._deps, msg.draft)))


class CharterSealExecutor(_FleetExecutor):
    def __init__(self, deps: FleetDeps) -> None:
        super().__init__(deps, "seal")

    @handler
    async def on_checked(self, msg: CharterChecked, ctx: WorkflowContext[SealedDraft]) -> None:
        self._trace()
        await ctx.send_message(await charter_steps.charter_seal(self._deps, msg.draft))


def build_charter_workflow(deps: FleetDeps, storage: Any) -> Workflow:
    """The same W-charter graph in every process. `storage` is any Agent Framework CheckpointStorage."""
    build = CharterBuildExecutor(deps)
    check = CharterCheckExecutor(deps)
    seal = CharterSealExecutor(deps)
    approval = ApprovalExecutor(deps)
    publish = PublishRequestExecutor(deps)
    return (
        WorkflowBuilder(
            name=WORKFLOW_NAME,
            start_executor=build,
            checkpoint_storage=storage,
            output_from=[build, approval, publish],
        )
        .add_edge(build, check)
        .add_edge(check, seal)
        .add_edge(seal, approval)
        .add_edge(approval, publish)
        .build()
    )
