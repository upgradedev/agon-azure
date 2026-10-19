"""Test oracle: the W-fulltime steps run in plain sequence, with no Agent Framework.

It exists so the graph's domain logic is exercised on any machine, and so CI can assert that the
Agent Framework run and this sequence seal the same bundle hash. It is test code on purpose: it is
never a runtime fallback, because the product's claim is that the approval cannot survive a
restart without the Agent Framework checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from agon.fleet import steps
from agon.fleet.contracts import (
    TRANSLATED_LANGUAGES,
    ApprovalRequest,
    Draft,
    RunInput,
    RunOutcome,
    SealedDraft,
)
from agon.fleet.deps import FleetDeps


@dataclass
class ReferenceRun:
    request: ApprovalRequest | None
    outcome: RunOutcome | None
    sealed: SealedDraft | None
    executed: list[str]
    writer_runs: int


async def run_to_request(deps: FleetDeps, run: RunInput) -> ReferenceRun:
    executed: list[str] = ["ingest"]
    first = await steps.ingest(deps, run)
    if isinstance(first, RunOutcome):
        return ReferenceRun(None, first, None, executed, 0)
    d: Draft = first
    d = await steps.interpret(deps, d)
    d = await steps.analyst(deps, d)
    d = await steps.writer(deps, d)
    executed += ["interpret", "analyst", "writer"]
    writer_runs = 1
    while True:
        d = await steps.triage(deps, d)
        kind, d = await steps.factgate(deps, d)
        executed += ["triage", "factgate"]
        if kind == "checked":
            break
        d = await steps.writer(deps, d, retry=True)
        executed.append("writer")
        writer_runs += 1
    d = await steps.desk_editor(deps, d)
    branches = [await steps.localise(deps, d, lang) for lang in TRANSLATED_LANGUAGES]
    branches.append(await steps.easy_read(deps, d))
    d = await steps.factgate_lang(deps, branches)
    d = await steps.rights(deps, d)
    d = await steps.audio(deps, d)
    sealed = await steps.seal_step(deps, d)
    executed += ["desk_editor", "localise_el", "localise_es", "localise_fr", "easy_read", "factgate_lang", "rights", "audio", "seal", "approval"]
    prepared = await steps.prepare_approval(deps, sealed)
    if isinstance(prepared, RunOutcome):
        return ReferenceRun(None, prepared, sealed, executed, writer_runs)
    return ReferenceRun(prepared, None, sealed, executed, writer_runs)
