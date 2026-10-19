"""A published bundle in the pipeline's shape, built from the real seal, verifier and Publisher.

Used by tests/test_webrecord.py and by the console workflow, which exports it with the real
exporter command to prove the site and the live gate stay green once a recorded run is the default.

Shape, as agon.fleet.steps.seal_step writes it: report-<edition>.json (agon.edition/1) for English,
Greek and easy-read, card.json (agon.card/1, triage and Confirm rows), and the payload of all six
destinations rendered by agon.publish.render. Sentence ids follow the fleet: s1, s1-el, s1-easy.
The officer approves on the Teams card and ticks four destinations, so YouTube and the journalist
digest stay offered and unticked, and the Publisher withholds their payloads.

Everything is synthetic (seed 1) and deterministic except the nonce, which never reaches an export.

    PYTHONPATH="src:tests" python tests/recorded_kit.py <out_dir>   # writes the container copy

To regenerate tests/fixtures/recorded-run/runs after a change to the kit, the seal or the renderers
(tests/test_webrecord.py holds the export to it byte for byte), from the repository root:

    rm -rf tests/fixtures/recorded-run/runs
    SHA=$(PYTHONPATH="src:tests" python tests/recorded_kit.py <out_dir>)
    PYTHONPATH=src python -m agon.webrecord --local <out_dir> --bundle "$SHA" \\
        --commit 0123456789abcdef0123456789abcdef01234567 --approver-role approver --channel teams \\
        --out tests/fixtures/recorded-run/runs
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agon.governance import verifier
from agon.governance.channels import teams
from agon.governance.claims import Claim, Sentence
from agon.governance.publisher import InMemorySink, Publisher
from agon.governance.recompute import MatchFacts, Roster, events_of_kind, score_after
from agon.governance.seal import Artifact, SealedBundle, canonical_utf8, event_log_sha256, seal, seed_sha256
from agon.governance.store import InMemoryStore
from agon.publish.adapters import default_adapters
from agon.publish.render import ReportView, render_all
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.world import build_world

SEED = 1
T0 = datetime(2026, 10, 21, 17, 50, 0, tzinfo=timezone.utc)
APPROVED_AT = T0 + timedelta(minutes=15)
PUBLISHED_AT = APPROVED_AT + timedelta(minutes=1)
OFFICER = "officer@media.example"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
TICKED = ("club_site", "feeds", "instagram", "newsroom_teams")
PASS = {"factgate": "pass", "factgate_lang": "pass", "rights": "pass"}
LABEL = "AI-drafted from a synthetic match. Numbers and names checked by code."


@dataclass
class Published:
    bundle: SealedBundle
    files: dict[str, bytes]
    sink: InMemorySink
    approval_id: str


def _facts() -> tuple[Any, MatchFacts, str]:
    league = build_world(SEED)
    result = simulate_match(league, 0)
    facts = MatchFacts(events=tuple(result.events), home_id=result.meta["home"], away_id=result.meta["away"], roster=Roster.from_league(league))
    return result, facts, result.meta["match_id"]


def _sentences(facts: MatchFacts) -> dict[str, list[Sentence]]:
    final = score_after(facts, None)
    home, away = facts.roster.clubs[facts.home_id], facts.roster.clubs[facts.away_id]
    card = events_of_kind(facts.events, "yellow_card")[0]
    booked = facts.roster.players[card.player]
    score = (
        Claim("score", {"home": int(final["home"]), "away": int(final["away"])}),
        Claim("name", home, subject=facts.home_id),
        Claim("name", away, subject=facts.away_id),
    )
    booking = (Claim("event_ref", "yellow_card", event_ids=(card.event_id,)), Claim("name", booked, (card.event_id,), subject=card.player))
    h, a = int(final["home"]), int(final["away"])
    texts = {
        "en": [f"{home} and {away} finished {h}-{a}.", f"{booked} was booked.", "The home supporters sang to the end."],
        "el": [f"{home} και {away} τελείωσαν {h}-{a}.", f"Ο {booked} πήρε κίτρινη κάρτα.", "Οι φίλαθλοι τραγουδούσαν ως το τέλος."],
        "easy": [f"{home} played {away}. It ended {h}-{a}.", f"{booked} got a yellow card.", "The fans sang."],
    }
    claims = [score, booking, ()]
    language = {"en": "en", "el": "el", "easy": "en"}
    out: dict[str, list[Sentence]] = {}
    for edition, lines in texts.items():
        suffix = "" if edition == "en" else f"-{edition}"
        out[edition] = [
            Sentence(f"s{i + 1}{suffix}", text, language[edition], claims[i], "model" if i < 2 else "template")
            for i, text in enumerate(lines)
        ]
    return out


def sealed_bundle() -> tuple[SealedBundle, dict[str, bytes]]:
    result, facts, match_id = _facts()
    editions = _sentences(facts)
    files: dict[str, bytes] = {}
    artifacts: list[Artifact] = []
    for edition, rows in editions.items():
        language = "el" if edition == "el" else "en"
        report = {
            "schema": "agon.edition/1",
            "run_id": "run-recorded-kit",
            "match_id": match_id,
            "edition": edition,
            "language": language,
            "label": LABEL,
            "sentences": [s.to_dict() for s in rows],
        }
        data = canonical_utf8(report)
        files[f"report-{edition}.json"] = data
        artifacts.append(Artifact(f"report-{edition}.json", data, "application/json", "edition", language))
    triage = {
        "s1": {"type": "match_stat", "model_type": "match_stat", "grounding_ids": [], "route": "factgate"},
        "s2": {"type": "disciplinary_referee", "model_type": None, "grounding_ids": [], "route": "confirm"},
        "s3": {"type": "neutral_colour", "model_type": "neutral_colour", "grounding_ids": [], "route": "none"},
    }
    card = {
        "schema": "agon.card/1",
        "run_id": "run-recorded-kit",
        "match_id": match_id,
        "target": "feed",
        "confirm_rows": ["s2"],
        "flags": [
            {"code": "easy_read_template_line", "detail": "The easy-read line was shortened by the rules.", "by": "easy_read", "sentence_id": "s3-easy"},
            {"code": "writer_note", "detail": "No sentence id on this flag, so the run cannot show it beside a sentence.", "by": "writer"},
        ],
        "gate_reasons": {},
        "triage": triage,
        "turning_points": [],
        "editions": list(editions),
        "interpret_source": None,
        "interpret_note": None,
    }
    data = canonical_utf8(card)
    files["card.json"] = data
    artifacts.append(Artifact("card.json", data, "application/json", "card", None))
    final = score_after(facts, None)
    view = ReportView(
        match_id=match_id,
        league=facts.roster.league_name,
        home=facts.roster.clubs[facts.home_id],
        away=facts.roster.clubs[facts.away_id],
        home_goals=int(final["home"]),
        away_goals=int(final["away"]),
        sentences=tuple(s.text for s in editions["en"]),
        edition={"schema": "agon.edition/1", "edition": "en", "match_id": match_id, "sentences": [s.to_dict() for s in editions["en"]]},
        disclosure=LABEL,
    )
    payloads = render_all(view)
    bundle = seal(
        kind="fulltime",
        match_id=match_id,
        seed_hash=seed_sha256(SEED, 0, GENERATOR_VERSION),
        event_log_hash=event_log_sha256(result.events),
        charter_hash=None,
        artifacts=artifacts,
        gate_verdicts=PASS,
        author_tags={s.sentence_id: s.author for rows in editions.values() for s in rows},
        attested=["report-en.json", "s2"],
        gate_cleared=sorted(n for n in files if n != "report-en.json"),
        destinations=payloads,
    )
    from agon.governance import destinations as dest_mod

    for dest_id, payload in payloads.items():
        files[dest_mod.artifact_name(dest_id)] = payload
    return bundle, files


def publish() -> Published:
    """Approve on the Teams card (four destinations ticked) and publish with the real Publisher."""
    bundle, files = sealed_bundle()
    store = InMemoryStore()
    verifier.grant_role(store, OFFICER, [verifier.APPROVER_ROLE])
    verifier.register_sealed_bundle(store, bundle.manifest_bytes)
    nonce = verifier.issue_nonce(store, bundle.bundle_sha256, target="feed", now=T0, ttl_seconds=3600, confirm_rows=("s2",))
    data: dict[str, Any] = {"decision": "approve", "bundle_sha256": bundle.bundle_sha256, "nonce": nonce, "confirm:s2": "true"}
    for dest_id in ("club_site", "feeds", "instagram", "youtube", "newsroom_teams", "journalist_email"):
        data[f"dest:{dest_id}"] = "true" if dest_id in TICKED else "false"
    payload = {"responder": {"userPrincipalName": OFFICER, "objectId": "00000000-0000-0000-0000-000000000001"}, "data": data}
    result = teams.handle(payload, store, APPROVED_AT)
    if not result.approved or result.approval_id is None:
        raise RuntimeError(f"the kit's approval was refused: {result.code} {result.detail}")
    sink = InMemorySink()
    publisher = Publisher(store, sink, "feed", default_adapters(""))
    outcome = publisher.publish(
        bundle_sha256=bundle.bundle_sha256,
        approval_id=result.approval_id,
        manifest_bytes=bundle.manifest_bytes,
        artifacts=files,
        now=PUBLISHED_AT,
    )
    if not outcome.ok:
        raise RuntimeError(f"the kit's publish was refused: {outcome.code} {outcome.detail}")
    return Published(bundle, files, sink, result.approval_id)


def write_container(out_dir: Path, published: Published | None = None) -> str:
    """Write the feed container as published (without the container segment). Returns the bundle sha256."""
    published = published or publish()
    for path, (data, _) in published.sink.blobs.items():
        container, _, name = path.partition("/")
        if container != "feed":
            raise RuntimeError(f"{path} is outside the feed container")
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return published.bundle.bundle_sha256


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/recorded_kit.py <out_dir>")
    sha = write_container(Path(sys.argv[1]))
    sys.stdout.write(f"{sha}\n")
