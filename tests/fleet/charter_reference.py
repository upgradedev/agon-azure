"""Test oracle for W-charter: its steps in plain sequence, with no Agent Framework.

Like tests/fleet/fleet_reference.py it is test code only, never a runtime fallback: the offline
tests exercise the domain logic on any machine, and the fleet CI job asserts that the Agent
Framework graph seals the same charter bundle hash as this sequence.
"""

from __future__ import annotations

from dataclasses import dataclass

from agon.fleet import charter_steps, steps
from agon.fleet.contracts import ApprovalRequest, RunInput, RunOutcome, SealedDraft
from agon.fleet.deps import FleetDeps


@dataclass
class CharterRun:
    request: ApprovalRequest | None
    outcome: RunOutcome | None
    sealed: SealedDraft | None


async def charter_to_request(deps: FleetDeps, run: RunInput) -> CharterRun:
    first = await charter_steps.charter_build(deps, run)
    if isinstance(first, RunOutcome):
        return CharterRun(None, first, None)
    checked = await charter_steps.charter_check_step(deps, first)
    sealed = await charter_steps.charter_seal(deps, checked)
    prepared = await steps.prepare_approval(deps, sealed)
    if isinstance(prepared, RunOutcome):
        return CharterRun(None, prepared, sealed)
    return CharterRun(prepared, None, sealed)
