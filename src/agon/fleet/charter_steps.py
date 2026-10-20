"""What each W-charter executor does, as plain async functions over the injected ports.

`agon.fleet.charter_workflow` wraps them in Agent Framework executors with fixed ids; the approval
and publish executors are W-fulltime's own (`steps.prepare_approval`, `steps.decide`,
`steps.enqueue_publish`), because a charter is approved through the same nonce, the same Teams
flow, the same intake and the one verifier as a report, only with the purpose "charter".

No language model is called anywhere here: the charter is built by code from the fictional
fixture (agon.charter.build_charter), checked by code (agon.charter.charter_check: the charter
rules and the Rights Guard) and sealed as a bundle of kind "charter" with one artifact,
charter.json, under the hash of the empty event log, because it is approved before kickoff.

Re-entry follows `steps.ingest`: the runs queue delivers at least once, so a run already SEALED
or later redoes nothing and never gets a second nonce, and a run id that belongs to another match
or another kind is refused.
"""

from __future__ import annotations

import copy
import json

from agon.charter import ARTIFACT_NAME, EMPTY_EVENT_LOG_SHA256, LANGUAGES, MEDIA_TYPE, SCHEMA, build_charter, charter_bytes, charter_check
from agon.fleet import stores
from agon.fleet.contracts import REFUSED_AT_INGEST, Draft, RunInput, RunOutcome, SealedDraft
from agon.fleet.deps import FleetDeps
from agon.fleet.kinds import CHARTER, FULLTIME
from agon.fleet.sources import MatchRecord, MatchSourceError
from agon.fleet.steps import _already, add_flag
from agon.governance import verifier
from agon.governance.seal import CHARTER_ROLE, Artifact, seal, sha256_hex


def _voices(deps: FleetDeps) -> dict[str, str] | None:
    """The deployment's voices when its Speech port names one per charter language, else the defaults."""
    voices = getattr(deps.speech, "voices", None)
    if isinstance(voices, dict) and set(voices) >= set(LANGUAGES):
        return {lang: str(voices[lang]) for lang in LANGUAGES}
    return None


def _record(deps: FleetDeps, draft: Draft) -> MatchRecord:
    """The fixture the charter was built for; refused unless it is the same seed and match."""
    record = deps.matches.load(draft.match_ref)
    if record.match_id != draft.match_id or record.seed_sha256 != draft.seed_sha256:
        raise MatchSourceError(f"match {record.match_id} is not the fixture this charter was built for ({draft.match_id})")
    return record


def _charter_bytes(deps: FleetDeps, draft: Draft) -> bytes:
    entry = draft.artifacts.get(ARTIFACT_NAME)
    data = None if entry is None else deps.artifacts.get(entry["sha256"])
    if data is None or sha256_hex(data) != entry["sha256"]:
        raise RuntimeError(f"{ARTIFACT_NAME} is missing from the artifact store or does not hash to its name")
    return data


async def charter_build(deps: FleetDeps, run: RunInput) -> Draft | RunOutcome:
    """Build the charter for the run's fixture and put its canonical bytes in the artifact store."""

    def refuse(code: str, detail: str) -> RunOutcome:
        return RunOutcome(run_id=run.run_id, status=REFUSED_AT_INGEST, bundle_sha256=None, code=code, detail=detail)

    kind = getattr(run, "kind", FULLTIME)
    if kind != CHARTER:
        return refuse("wrong_workflow", f"W-charter runs kind {CHARTER!r}, not {kind!r}")
    if run.target not in verifier.TARGETS:
        return refuse("unknown_target", f"target {run.target!r} is not one of {list(verifier.TARGETS)}")
    try:
        record = deps.matches.load(run.match_ref)
    except MatchSourceError as exc:
        return refuse("match_not_found", str(exc))
    data = charter_bytes(build_charter(record, _voices(deps)))
    draft = Draft(
        run_id=run.run_id,
        target=run.target,
        match_ref=dict(run.match_ref),
        match_id=record.match_id,
        home_id=record.meta["home"],
        away_id=record.meta["away"],
        event_log_sha256=EMPTY_EVENT_LOG_SHA256,
        seed_sha256=record.seed_sha256,
        artifacts={ARTIFACT_NAME: {"sha256": deps.artifacts.put(data), "media_type": MEDIA_TYPE, "role": CHARTER_ROLE}},
    )
    known = deps.ledger.record(run.run_id)
    if known is not None:
        if known.get("match_id") != record.match_id or known.get("kind", FULLTIME) != CHARTER:
            return refuse(
                "run_id_reused",
                f"run {run.run_id} belongs to {known.get('kind', FULLTIME)} {known.get('match_id')}, not charter {record.match_id}",
            )
        status = known.get("status")
        if status in (stores.DRAFT, stores.CHECKED):
            return draft
        return _already(run, str(status), known)
    deps.ledger.advance(run.run_id, stores.DRAFT, f"charter built for {record.match_id}", match_id=record.match_id, kind=CHARTER)
    return draft


async def charter_check_step(deps: FleetDeps, draft: Draft) -> Draft:
    """The two deterministic gates: the charter rules (charter_check) and the Rights Guard (rights)."""
    d = copy.deepcopy(draft)
    record = _record(deps, d)
    charter = json.loads(_charter_bytes(deps, d).decode("utf-8"))
    verdict = charter_check(charter, record.roster)
    d.gate_verdicts["charter_check"] = verdict.charter_check
    d.gate_verdicts["rights"] = verdict.rights
    d.gate_reasons["charter_check"] = list(verdict.charter_reasons)
    d.gate_reasons["rights"] = list(verdict.rights_reasons)
    for reason in verdict.charter_reasons:
        add_flag(d, "charter_check_refusal", reason, "charter_check")
    if verdict.charter_check == "pass":
        for reason in verdict.rights_reasons:
            add_flag(d, "rights_refusal", reason, "rights")
    # CHECKED means both gates ran and their verdicts are recorded, not that they passed: a refused
    # charter is still sealed so the reason is on record, and prepare_approval issues no nonce.
    deps.ledger.advance(d.run_id, stores.CHECKED, f"charter_check {verdict.charter_check}, rights {verdict.rights}")
    return d


async def charter_seal(deps: FleetDeps, draft: Draft) -> SealedDraft:
    """Seal the charter as a bundle of kind "charter": charter.json only, no destination."""
    d = copy.deepcopy(draft)
    data = _charter_bytes(deps, d)
    deployment_ids = {"charter": SCHEMA}
    if "ports" in deps.deployment_ids:
        deployment_ids["ports"] = deps.deployment_ids["ports"]
    sealed = seal(
        kind=CHARTER,
        match_id=d.match_id,
        seed_hash=d.seed_sha256,
        event_log_hash=EMPTY_EVENT_LOG_SHA256,
        charter_hash=None,
        artifacts=[Artifact(ARTIFACT_NAME, data, MEDIA_TYPE, CHARTER_ROLE)],
        gate_verdicts=d.gate_verdicts,
        author_tags={},
        attested=[ARTIFACT_NAME],
        gate_cleared=[],
        deployment_ids=deployment_ids,
    )
    deps.ledger.advance(d.run_id, stores.SEALED, "charter sealed", bundle_sha256=sealed.bundle_sha256)
    return SealedDraft(draft=d, bundle_sha256=sealed.bundle_sha256, manifest_json=sealed.manifest_bytes.decode("utf-8"))
