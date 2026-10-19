"""Run files the web app reads for the Newsroom Wall, the stand-in approval card, the recap and Verify.

Usage:
    python -m agon.webrun --check web/runs            # validate every run and re-verify its bundle
    python -m agon.webrun --write-sample web/runs     # rewrite the one hand-written design preview

Why this module: a run is the trace of one full-time newsroom pass (desk lines, handoffs, the
bundle state, gate verdicts, Confirm rows) plus the sealed bundle it produced. The browser shows
the trace and recomputes the bundle hash itself, so the trace format and the bundle layout are
defined once here, and `validate_run` and `verify_run_dir` are the checkers the deploy step and
the tests run. The format is documented for humans in web/runs/README.txt.

Until the pipeline produces real runs, web/runs holds ONE sample run. Its sentences, desk lines and
Confirm rows were written by hand for a design preview; no agent ran. What is real in it:

* the names, minutes, counts and event ids come from synthetic seed 1 (agon.synth);
* the gate verdicts are computed by the real deterministic gates (agon.governance.factgate and
  agon.governance.rights) over those hand-written sentences, including the planted "2 shots on
  target" draft that the Fact Gate refuses;
* the bundle is sealed by agon.governance.seal, the same function the pipeline uses, so the Verify
  page recomputes a real bundle hash over real edition files.

Every such run carries provenance "design_preview" and the exact label DESIGN_PREVIEW_LABEL, which
validate_run, ci/web_check.py, the browser and the readiness gate each refuse to do without.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from agon.governance.claims import Claim, Sentence
from agon.governance.factgate import check_translation, run_factgate
from agon.governance.recompute import MatchFacts, Roster, display_minute, events_of_kind, score_after, stat_events
from agon.governance.rights import run_rights
from agon.governance.seal import (
    Artifact,
    canonical_utf8,
    event_log_sha256,
    is_sha256_hex,
    seal,
    seed_sha256,
    sha256_hex,
    verify_manifest,
)
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.world import build_world

RUN_FORMAT = "agon.web-run/1"
INDEX_FORMAT = "agon.web-runs/1"
INDEX_NAME = "index.json"
BUNDLE_MANIFEST = "manifest.json"
DESIGN_PREVIEW = "design_preview"
PIPELINE = "pipeline"
PROVENANCES = (DESIGN_PREVIEW, PIPELINE)
# The exact sentence every hand-written run carries. Pinned in ci/web_check.py, web/agon-core.js,
# ci/readiness.json and their tests; change one and the others fail.
DESIGN_PREVIEW_LABEL = "Design preview: sample run written by hand, not produced by the pipeline."

STATES: tuple[str, ...] = ("DRAFT", "CHECKED", "SEALED", "AWAITING_APPROVAL", "APPROVED", "PUBLISHED", "CHANGES_REQUESTED")
APPROVAL_STATUSES = ("awaiting", "approved", "changes_requested")
SENTENCE_TYPES = (
    "match_stat", "injury_medical", "disciplinary_referee", "quote_attribution", "transfer_contract", "neutral_colour",
)
# The triage routing table (spec section 4, executor 5): these types become Confirm rows that a
# person must tick; everything else is checked by the gates only.
CONFIRM_TYPES = {
    "injury_medical": "the injury sentence",
    "disciplinary_referee": "the referee sentence",
    "quote_attribution": "the quote sentence",
    "transfer_contract": "the transfer sentence",
}
GATE_VERDICTS = ("pass", "mismatch", "refused", "skipped")

RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
EDITION_FILE = re.compile(r"^edition-[a-z]{2,8}\.json$")
SENTENCE_ID = re.compile(r"^[a-z]{2,8}-[0-9]{2}$")
ISO_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?Z$")

RUN_KEYS = frozenset({
    "format", "run_id", "provenance", "label", "seed", "match_id", "generator", "home", "away", "score",
    "bundle", "state", "states", "desks", "handoffs", "gates", "editions", "sentence_types",
    "sentence_gates", "confirm_rows", "desk_editor_flags", "approval",
})
EDITION_KEYS = frozenset({"edition", "language", "name", "file"})
APPROVAL_KEYS = frozenset({"status", "approved_at", "approver_role", "channel"})

# ---------------------------------------------------------------- the hand-written sample

SAMPLE_RUN_ID = "sample-s1"
SAMPLE_SEED = 1
SAMPLE_EDITIONS: tuple[tuple[str, str, str], ...] = (
    ("en", "en", "English"),
    ("el", "el", "Greek"),
    ("es", "es", "Spanish"),
    ("fr", "fr", "French"),
    ("easy", "en", "Easy-read English"),
)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _match(seed: int) -> tuple[Any, Any, MatchFacts]:
    league = build_world(seed)
    result = simulate_match(league, 0)
    facts = MatchFacts(
        events=tuple(result.events),
        home_id=result.meta["home"],
        away_id=result.meta["away"],
        roster=Roster.from_league(league),
    )
    return league, result, facts


def _sample_texts(n: dict[str, Any]) -> dict[str, list[str]]:
    """The hand-written prose, one list per edition, in sentence order. Names and numbers are filled
    from the log so the sample stays true to seed 1; the wording is ours."""
    m1, m4, m5, m6 = n["m1"], n["m4"], n["m5"], n["m6"]
    return {
        "en": [
            f"{n['home']} drew {n['fh']}-{n['fa']} with {n['away']} in the {n['league']}.",
            f"{n['scorer1']} put {n['away']} ahead in the {_ordinal(int(m1))} minute.",
            f"{n['away']} had {n['sot']} shots on target.",
            f"{n['booked']} of {n['home']} was booked in the {_ordinal(int(m4))} minute.",
            f"{n['off']} went off in the {_ordinal(int(m5))} minute and will be assessed by the club's medical staff.",
            f"{n['scorer2']} equalised for {n['home']} in the {_ordinal(int(m6))} minute.",
        ],
        "el": [
            f"Η {n['home']} έφερε ισοπαλία {n['fh']}-{n['fa']} με την {n['away']} στη {n['league']}.",
            f"Ο {n['scorer1']} έδωσε προβάδισμα στην {n['away']} στο {m1}ο λεπτό.",
            f"Η {n['away']} είχε {n['sot']} τελικές στην εστία.",
            f"Ο {n['booked']} της {n['home']} δέχτηκε κίτρινη κάρτα στο {m4}ο λεπτό.",
            f"Ο {n['off']} αντικαταστάθηκε στο {m5}ο λεπτό και θα τον εξετάσει το ιατρικό τιμ του συλλόγου.",
            f"Ο {n['scorer2']} ισοφάρισε για την {n['home']} στο {m6}ο λεπτό.",
        ],
        "es": [
            f"El {n['home']} empató {n['fh']}-{n['fa']} con el {n['away']} en la {n['league']}.",
            f"{n['scorer1']} adelantó al {n['away']} en el minuto {m1}.",
            f"El {n['away']} tuvo {n['sot']} tiros a puerta.",
            f"{n['booked']}, del {n['home']}, vio la tarjeta amarilla en el minuto {m4}.",
            f"{n['off']} fue sustituido en el minuto {m5} y lo evaluarán los servicios médicos del club.",
            f"{n['scorer2']} empató para el {n['home']} en el minuto {m6}.",
        ],
        "fr": [
            f"{n['home']} a fait match nul {n['fh']}-{n['fa']} contre {n['away']} en {n['league']}.",
            f"{n['scorer1']} a donné l'avantage à {n['away']} à la {m1}e minute.",
            f"{n['away']} a cadré {n['sot']} tirs.",
            f"{n['booked']} ({n['home']}) a reçu un carton jaune à la {m4}e minute.",
            f"{n['off']} est sorti à la {m5}e minute et sera examiné par le staff médical du club.",
            f"{n['scorer2']} a égalisé pour {n['home']} à la {m6}e minute.",
        ],
        "easy": [
            f"{n['home']} and {n['away']} drew {n['fh']}-{n['fa']} in the {n['league']}.",
            f"{n['scorer1']} scored for {n['away']} in minute {m1}.",
            f"{n['away']} had {n['sot']} shots on target.",
            f"{n['booked']} from {n['home']} got a yellow card in minute {m4}.",
            f"{n['off']} came off in minute {m5}. The club doctors will check on this player.",
            f"{n['scorer2']} scored for {n['home']} in minute {m6}.",
        ],
    }


SAMPLE_TYPES = ("match_stat", "match_stat", "match_stat", "disciplinary_referee", "injury_medical", "match_stat")


def _sample_facts(seed: int) -> dict[str, Any]:
    league, result, facts = _match(seed)
    events = facts.events
    home, away = facts.home_id, facts.away_id
    goals = events_of_kind(events, "goal")
    yellows = events_of_kind(events, "yellow_card")
    if len(goals) != 2 or len(yellows) < 1:
        raise ValueError(f"the sample is written for seed {SAMPLE_SEED}: two goals and a booking")
    g1, g2 = goals
    booking = yellows[0]
    sub_off = [e for e in events_of_kind(events, "substitution") if e.player == booking.player]
    if len(sub_off) != 1:
        raise ValueError("the sample expects the booked player to be substituted once")
    sub = sub_off[0]
    sot = stat_events(facts, "shots_on_target", g1.team)
    final = score_after(facts, None)
    roster = facts.roster
    return {
        "league": league,
        "result": result,
        "facts": facts,
        "g1": g1, "g2": g2, "booking": booking, "sub": sub, "sot_events": sot,
        "names": {
            "home": roster.clubs[home], "away": roster.clubs[away], "league": roster.league_name,
            "scorer1": roster.players[g1.player], "scorer2": roster.players[g2.player],
            "booked": roster.players[booking.player], "off": roster.players[sub.player],
            "fh": final["home"], "fa": final["away"], "sot": len(sot),
            "m1": display_minute(g1.period, g1.minute), "m4": display_minute(booking.period, booking.minute),
            "m5": display_minute(sub.period, sub.minute), "m6": display_minute(g2.period, g2.minute),
            "scorer1_team": g1.team, "scorer2_team": g2.team,
        },
    }


def _sample_claims(f: dict[str, Any]) -> list[tuple[Claim, ...]]:
    facts: MatchFacts = f["facts"]
    n = f["names"]
    home, away = facts.home_id, facts.away_id
    g1, g2, booking, sub = f["g1"], f["g2"], f["booking"], f["sub"]
    league_claim = Claim(type="name", value=n["league"], subject="league")

    def name(entity: str, value: str, *event_ids: str) -> Claim:
        return Claim(type="name", value=value, subject=entity, event_ids=tuple(event_ids))

    def minute(e: Any) -> Claim:
        return Claim(type="minute", value=display_minute(e.period, e.minute), event_ids=(e.event_id,))

    def kind(value: str, e: Any) -> Claim:
        return Claim(type="event_ref", value=value, event_ids=(e.event_id,))

    return [
        (
            Claim(type="score", value={"home": n["fh"], "away": n["fa"]}),
            name(home, n["home"]), name(away, n["away"]), league_claim,
        ),
        (kind("goal", g1), minute(g1), name(g1.player, n["scorer1"], g1.event_id), name(g1.team, facts.roster.clubs[g1.team], g1.event_id)),
        (
            Claim(type="stat", value=len(f["sot_events"]), event_ids=tuple(e.event_id for e in f["sot_events"]),
                  stat="shots_on_target", subject=g1.team),
            name(g1.team, facts.roster.clubs[g1.team]),
        ),
        (kind("yellow_card", booking), minute(booking), name(booking.player, n["booked"], booking.event_id),
         name(booking.team, facts.roster.clubs[booking.team], booking.event_id)),
        (kind("substitution", sub), minute(sub), name(sub.player, n["off"], sub.event_id)),
        (kind("goal", g2), minute(g2), name(g2.player, n["scorer2"], g2.event_id), name(g2.team, facts.roster.clubs[g2.team], g2.event_id)),
    ]


def _planted_draft(f: dict[str, Any]) -> Sentence:
    """The Writer's first draft of sentence 3, with the planted wrong count the Fact Gate must strike."""
    n = f["names"]
    team = f["g1"].team
    wrong = 2 if len(f["sot_events"]) != 2 else 3
    return Sentence(
        sentence_id="en-03",
        text=f"{n['away']} had {wrong} shots on target.",
        language="en",
        claims=(
            Claim(type="stat", value=wrong, event_ids=tuple(e.event_id for e in f["sot_events"]),
                  stat="shots_on_target", subject=team),
            Claim(type="name", value=n["away"], subject=team),
        ),
    )


def build_sample_run(seed: int = SAMPLE_SEED) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return (run, {bundle file name: bytes}) for the hand-written design preview.

    Raises ValueError if any gate does not pass on the final sentences, so a sample that the real
    gates would refuse can never be written.
    """
    f = _sample_facts(seed)
    facts: MatchFacts = f["facts"]
    n = f["names"]
    texts = _sample_texts(n)
    claims = _sample_claims(f)
    sentences: dict[str, list[Sentence]] = {}
    for edition, language, _ in SAMPLE_EDITIONS:
        lines = texts[edition]
        if len(lines) != len(claims):
            raise ValueError(f"edition {edition} has {len(lines)} sentences, expected {len(claims)}")
        # Sentence.author must be a pipeline tag; the edition FILE records "hand" (see _edition_bytes).
        sentences[edition] = [
            Sentence(sentence_id=f"{edition}-{i + 1:02d}", text=text, language=language, claims=claims[i], author="template")
            for i, text in enumerate(lines)
        ]

    draft_report = run_factgate([_planted_draft(f)], facts)
    if draft_report.passed:
        raise ValueError("the planted draft must fail the Fact Gate, or the sample tells a false story")
    draft_reason = draft_report.verdicts[0].reasons[0]

    en_report = run_factgate(sentences["en"], facts)
    lang_verdicts = []
    for edition, _, _ in SAMPLE_EDITIONS[1:]:
        for source, translated in zip(sentences["en"], sentences[edition]):
            lang_verdicts.append(check_translation(source, translated, facts))
    all_sentences = [s for edition, _, _ in SAMPLE_EDITIONS for s in sentences[edition]]
    rights_report = run_rights(facts.roster, all_sentences)
    problems = [r for v in en_report.verdicts for r in v.reasons]
    problems += [r for v in lang_verdicts for r in v.reasons]
    problems += [f"{r.code} at {r.where}: {r.detail}" for r in rights_report.refusals]
    if problems:
        raise ValueError("the sample sentences fail a gate: " + "; ".join(problems))

    sentence_gates: dict[str, dict[str, str]] = {}
    for v in en_report.verdicts:
        sentence_gates[v.sentence_id] = {"factgate": v.status, "rights": "pass"}
    for v in lang_verdicts:
        sentence_gates[v.sentence_id] = {"factgate_lang": v.status, "rights": "pass"}
    sentence_types = {s.sentence_id: SAMPLE_TYPES[i] for i, s in enumerate(sentences["en"])}

    files: dict[str, bytes] = {}
    artifacts = []
    edition_rows = []
    for edition, language, name in SAMPLE_EDITIONS:
        data = _edition_bytes(edition, language, f["result"].meta["match_id"], sentences[edition])
        file_name = f"edition-{edition}.json"
        files[file_name] = data
        artifacts.append(Artifact(name=file_name, data=data, media_type="application/json", role="edition", language=language))
        edition_rows.append({"edition": edition, "language": language, "name": name, "file": file_name})
    gate_verdicts = {"factgate": en_report.status, "factgate_lang": "pass", "rights": rights_report.status}
    sealed = seal(
        kind="fulltime",
        match_id=f["result"].meta["match_id"],
        seed_hash=seed_sha256(seed, 0, GENERATOR_VERSION),
        event_log_hash=event_log_sha256(facts.events),
        charter_hash=None,
        artifacts=artifacts,
        gate_verdicts=gate_verdicts,
        author_tags={},
        attested=["edition-en.json"],
        gate_cleared=[row["file"] for row in edition_rows[1:]],
    )
    files[BUNDLE_MANIFEST] = sealed.manifest_bytes

    en_count = len(sentences["en"])
    translated = len(lang_verdicts)
    confirm_rows = [
        {"row_id": f"confirm-{sid}", "sentence_id": sid, "type": stype, "label": CONFIRM_TYPES[stype]}
        for sid, stype in sentence_types.items()
        if stype in CONFIRM_TYPES
    ]
    sub_minute = _ordinal(int(n["m5"]))
    run = {
        "format": RUN_FORMAT,
        "run_id": SAMPLE_RUN_ID,
        "provenance": DESIGN_PREVIEW,
        "label": DESIGN_PREVIEW_LABEL,
        "seed": seed,
        "match_id": f["result"].meta["match_id"],
        "generator": GENERATOR_VERSION,
        "home": {"club_id": facts.home_id, "name": n["home"]},
        "away": {"club_id": facts.away_id, "name": n["away"]},
        "score": {"home": n["fh"], "away": n["fa"]},
        "bundle": {"sha256": sealed.bundle_sha256, "dir": SAMPLE_RUN_ID, "manifest": BUNDLE_MANIFEST},
        "state": "AWAITING_APPROVAL",
        "states": [
            {"state": "DRAFT", "step": 4},
            {"state": "CHECKED", "step": 8},
            {"state": "SEALED", "step": 11},
            {"state": "AWAITING_APPROVAL", "step": 12},
        ],
        "desks": [
            {"desk": "whistle", "name": "Whistle", "lines": [
                {"step": 1, "text": f"Full time: {n['home']} {n['fh']}, {n['away']} {n['fa']}. The event log is frozen for the report."},
            ]},
            {"desk": "analyst", "name": "Analyst", "lines": [
                {"step": 2, "text": f"Reading minutes 1 to 10: {n['scorer1']} scores for {n['away']}."},
                {"step": 3, "text": f"Reading minutes 75 to 85: changes in the {sub_minute} minute, then {n['scorer2']} equalises."},
            ]},
            {"desk": "writer", "name": "Writer", "lines": [
                {"step": 4, "text": f"Drafted {en_count} sentences for the headline report."},
                {"step": 6, "text": "Rewrote sentence 3 with the Fact Gate's reason in hand."},
            ]},
            {"desk": "factgate", "name": "Fact Gate", "lines": [
                {"step": 5, "text": f"Struck sentence 3. It said 2 shots on target; the log says {n['sot']}."},
                {"step": 7, "text": f"Rechecked all {en_count} sentences: every number, name and minute agrees with the log."},
                {"step": 10, "text": f"Rechecked {translated} translated and easy-read sentences: same claims, same numbers."},
            ]},
            {"desk": "desk_editor", "name": "Desk Editor", "lines": [
                {"step": 8, "text": "Flagged sentence 5 for a person: the log cannot show a medical assessment."},
            ]},
            {"desk": "localise", "name": "Localisers", "lines": [
                {"step": 9, "text": "Wrote the Greek, Spanish and French editions; names stay in Latin script."},
                {"step": 9, "text": "Wrote the easy-read edition: short sentences, one idea each, numbers as digits."},
            ]},
            {"desk": "rights", "name": "Rights Guard", "lines": [
                {"step": 10, "text": f"Screened every name in all {len(all_sentences)} sentences: all fictional, all on the roster."},
            ]},
            {"desk": "seal", "name": "Seal and approval", "lines": [
                {"step": 11, "text": f"Sealed {len(edition_rows)} editions under one hash."},
                {"step": 12, "text": "Waiting for the club media officer to approve that exact hash."},
            ]},
        ],
        "handoffs": [
            {"step": 4, "from": "writer", "to": "factgate", "text": "Draft handed to the Fact Gate."},
            {"step": 5, "from": "factgate", "to": "writer", "text": "Sentence 3 sent back with the reason."},
            {"step": 7, "from": "factgate", "to": "desk_editor", "text": "Checked report handed to the Desk Editor."},
            {"step": 9, "from": "localise", "to": "factgate", "text": "Editions handed back to the Fact Gate for a recheck in every language."},
            {"step": 10, "from": "factgate", "to": "rights", "text": "Rechecked editions handed to the Rights Guard."},
            {"step": 11, "from": "rights", "to": "seal", "text": "Cleared editions handed to the seal."},
            {"step": 12, "from": "seal", "to": "approval", "text": "Sealed bundle handed to the approval card."},
        ],
        "gates": [
            {"gate": "factgate_draft", "name": "Fact Gate, first draft", "verdict": draft_report.status,
             "detail": f"Sentence 3 said 2 shots on target; the log counts {n['sot']} for {n['away']}.",
             "evidence": draft_reason},
            {"gate": "factgate", "name": "Fact Gate", "verdict": en_report.status,
             "detail": f"{en_count} of {en_count} English sentences agree with the event log.",
             "evidence": f"agon.governance.factgate.run_factgate over {en_count} sentences: {en_report.status}"},
            {"gate": "factgate_lang", "name": "Fact Gate, other editions", "verdict": "pass",
             "detail": f"{translated} of {translated} translated and easy-read sentences carry the same claims and numbers.",
             "evidence": f"agon.governance.factgate.check_translation over {translated} sentences: pass"},
            {"gate": "rights", "name": "Rights Guard", "verdict": rights_report.status,
             "detail": "Every name is on the fictional roster; no real league, club or player name was found.",
             "evidence": f"agon.governance.rights.run_rights over {len(all_sentences)} sentences and the roster: {rights_report.status}"},
        ],
        "editions": edition_rows,
        "sentence_types": sentence_types,
        "sentence_gates": sentence_gates,
        "confirm_rows": confirm_rows,
        "desk_editor_flags": [
            {"sentence_id": "en-05", "text": "The log shows the substitution, not the reason for it. A person must confirm the medical part."},
        ],
        "approval": {"status": "awaiting", "approved_at": None, "approver_role": None, "channel": None},
    }
    problems_run = validate_run(run)
    if problems_run:
        raise ValueError("the sample run breaks the run format: " + "; ".join(problems_run))
    return run, files


def _edition_bytes(edition: str, language: str, match_id: str, sentences: list[Sentence]) -> bytes:
    """One edition file: canonical UTF-8 JSON plus LF. "author": "hand" marks the design preview."""
    body = {
        "edition": edition,
        "language": language,
        "match_id": match_id,
        "sentences": [dict(s.to_dict(), author="hand") for s in sentences],
    }
    return canonical_utf8(body) + b"\n"


def run_bytes(run: dict[str, Any]) -> bytes:
    return canonical_utf8(run) + b"\n"


def index_for(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": INDEX_FORMAT,
        "runs": [{"run_id": r["run_id"], "file": r["run_id"] + ".json", "provenance": r["provenance"]} for r in runs],
    }


def write_sample(out_dir: str) -> dict[str, Any]:
    """Write index.json, <run>.json and <run>/ with the sealed bundle, binary, LF only."""
    run, files = build_sample_run()
    root = Path(out_dir)
    bundle_dir = root / run["bundle"]["dir"]
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (bundle_dir / name).write_bytes(data)
    (root / f"{run['run_id']}.json").write_bytes(run_bytes(run))
    (root / INDEX_NAME).write_bytes(canonical_utf8(index_for([run])) + b"\n")
    return run


# ---------------------------------------------------------------- validation


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_index(index: Any) -> list[str]:
    if not isinstance(index, dict) or set(index) != {"format", "runs"}:
        return ["runs index keys must be exactly format and runs"]
    problems = []
    if index["format"] != INDEX_FORMAT:
        problems.append(f"runs index format must be {INDEX_FORMAT}")
    runs = index["runs"]
    if not isinstance(runs, list) or not runs:
        return problems + ["runs index must list at least one run"]
    seen = set()
    for i, row in enumerate(runs):
        where = f"runs[{i}]"
        if not isinstance(row, dict) or set(row) != {"run_id", "file", "provenance"}:
            problems.append(f"{where} keys must be exactly file, provenance and run_id")
            continue
        rid = row["run_id"]
        if not isinstance(rid, str) or not RUN_ID.match(rid):
            problems.append(f"{where}.run_id must be lowercase letters, digits and hyphens")
        elif rid in seen:
            problems.append(f"{where}.run_id {rid} is listed twice")
        else:
            seen.add(rid)
            if row["file"] != rid + ".json":
                problems.append(f"{where}.file must be {rid}.json")
        if row["provenance"] not in PROVENANCES:
            problems.append(f"{where}.provenance must be design_preview or pipeline")
    return problems


def validate_run(run: Any) -> list[str]:
    """Problems with a run file's shape and honesty rules; empty means the browser may show it.

    The browser applies the same rules (AgonCore.validateRun); tests/js/run_cases.json holds both
    sides to one list of mutations of the shipped sample.
    """
    if not isinstance(run, dict):
        return ["run must be a JSON object"]
    if set(run) != RUN_KEYS:
        missing, extra = sorted(RUN_KEYS - set(run)), sorted(set(run) - RUN_KEYS)
        return [f"run keys differ: missing {', '.join(missing) or 'none'}; unexpected {', '.join(extra) or 'none'}"]
    problems: list[str] = []
    if run["format"] != RUN_FORMAT:
        problems.append(f"run format must be {RUN_FORMAT}")
    if not isinstance(run["run_id"], str) or not RUN_ID.match(run["run_id"]):
        problems.append("run_id must be lowercase letters, digits and hyphens")
    provenance = run["provenance"]
    if provenance not in PROVENANCES:
        problems.append("provenance must be design_preview or pipeline")
    if provenance == DESIGN_PREVIEW and run["label"] != DESIGN_PREVIEW_LABEL:
        problems.append("a design preview run must carry the exact design preview label")
    if provenance == PIPELINE and (not _nonempty(run["label"]) or run["label"] == DESIGN_PREVIEW_LABEL):
        problems.append("a pipeline run must carry its own run label")
    if not _is_int(run["seed"]) or run["seed"] < 0:
        problems.append("seed must be a non-negative integer")
    for key in ("match_id", "generator"):
        if not _nonempty(run[key]):
            problems.append(f"{key} must be a non-empty string")
    for side in ("home", "away"):
        club = run[side]
        if not isinstance(club, dict) or set(club) != {"club_id", "name"} or not _nonempty(club.get("name")):
            problems.append(f"{side} must be {{club_id, name}} with a name")
    score = run["score"]
    if not isinstance(score, dict) or set(score) != {"home", "away"} or not all(_is_int(v) and v >= 0 for v in score.values()):
        problems.append("score must be {home, away} with non-negative integers")
    bundle = run["bundle"]
    if not isinstance(bundle, dict) or set(bundle) != {"sha256", "dir", "manifest"}:
        problems.append("bundle keys must be exactly dir, manifest and sha256")
    else:
        if not is_sha256_hex(bundle["sha256"]):
            problems.append("bundle.sha256 must be 64 lowercase hex characters")
        if bundle["dir"] != run["run_id"]:
            problems.append("bundle.dir must equal run_id")
        if bundle["manifest"] != BUNDLE_MANIFEST:
            problems.append(f"bundle.manifest must be {BUNDLE_MANIFEST}")
    state = run["state"]
    if state not in STATES:
        problems.append("state must be one of " + ", ".join(STATES))
    states = run["states"]
    if not isinstance(states, list) or not states:
        problems.append("states must be a non-empty list")
    else:
        steps = []
        for i, row in enumerate(states):
            if not isinstance(row, dict) or set(row) != {"state", "step"} or row.get("state") not in STATES or not _is_int(row.get("step")):
                problems.append(f"states[{i}] must be {{state, step}} with a known state")
            else:
                steps.append(row["step"])
        if steps != sorted(steps):
            problems.append("states must be in step order")
        if isinstance(states[-1], dict) and states[-1].get("state") != state:
            problems.append("the last entry in states must be the current state")
    desk_ids: set[str] = set()
    desks = run["desks"]
    if not isinstance(desks, list) or not desks:
        problems.append("desks must be a non-empty list")
    else:
        for i, desk in enumerate(desks):
            if not isinstance(desk, dict) or set(desk) != {"desk", "name", "lines"} or not _nonempty(desk.get("name")):
                problems.append(f"desks[{i}] must be {{desk, name, lines}}")
                continue
            desk_ids.add(desk["desk"])
            lines = desk["lines"]
            if not isinstance(lines, list) or not lines or not all(
                isinstance(x, dict) and set(x) == {"step", "text"} and _is_int(x["step"]) and _nonempty(x["text"]) for x in lines
            ):
                problems.append(f"desks[{i}].lines must be a non-empty list of {{step, text}}")
    handoffs = run["handoffs"]
    if not isinstance(handoffs, list):
        problems.append("handoffs must be a list")
    else:
        for i, h in enumerate(handoffs):
            if not isinstance(h, dict) or set(h) != {"step", "from", "to", "text"} or not _is_int(h.get("step")) or not _nonempty(h.get("text")):
                problems.append(f"handoffs[{i}] must be {{step, from, to, text}}")
            elif h["from"] not in desk_ids or (h["to"] not in desk_ids and h["to"] != "approval"):
                problems.append(f"handoffs[{i}] names a desk that is not on the wall")
    gates = run["gates"]
    if not isinstance(gates, list) or not gates:
        problems.append("gates must be a non-empty list")
    else:
        for i, g in enumerate(gates):
            if not isinstance(g, dict) or set(g) != {"gate", "name", "verdict", "detail", "evidence"} or g.get("verdict") not in GATE_VERDICTS:
                problems.append(f"gates[{i}] must be {{gate, name, verdict, detail, evidence}} with a known verdict")
    editions = run["editions"]
    edition_ids: set[str] = set()
    if not isinstance(editions, list) or not editions:
        problems.append("editions must be a non-empty list")
    else:
        for i, e in enumerate(editions):
            if not isinstance(e, dict) or set(e) != EDITION_KEYS:
                problems.append(f"editions[{i}] keys must be exactly edition, file, language and name")
                continue
            if not isinstance(e["file"], str) or not EDITION_FILE.match(e["file"]) or e["file"] != f"edition-{e['edition']}.json":
                problems.append(f"editions[{i}].file must be edition-<edition>.json")
            if e["edition"] in edition_ids:
                problems.append(f"editions[{i}].edition {e['edition']} is listed twice")
            edition_ids.add(e["edition"])
    types = run["sentence_types"]
    if not isinstance(types, dict) or not types:
        problems.append("sentence_types must be a non-empty map")
        types = {}
    else:
        for sid, stype in types.items():
            if not SENTENCE_ID.match(sid) or stype not in SENTENCE_TYPES:
                problems.append(f"sentence_types[{sid}] must map a sentence id to a known type")
    gates_by_sentence = run["sentence_gates"]
    if not isinstance(gates_by_sentence, dict):
        problems.append("sentence_gates must be a map")
    else:
        for sid, verdicts in gates_by_sentence.items():
            if not SENTENCE_ID.match(sid) or not isinstance(verdicts, dict) or not verdicts or not all(
                v in GATE_VERDICTS for v in verdicts.values()
            ):
                problems.append(f"sentence_gates[{sid}] must map gate names to known verdicts")
    rows = run["confirm_rows"]
    if not isinstance(rows, list):
        problems.append("confirm_rows must be a list")
    else:
        needed = {sid for sid, stype in types.items() if stype in CONFIRM_TYPES}
        listed = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != {"row_id", "sentence_id", "type", "label"}:
                problems.append(f"confirm_rows[{i}] keys must be exactly label, row_id, sentence_id and type")
                continue
            if row["type"] not in CONFIRM_TYPES or row["label"] != CONFIRM_TYPES.get(row["type"]):
                problems.append(f"confirm_rows[{i}] must use the routing table's type and label")
            if types.get(row["sentence_id"]) != row["type"]:
                problems.append(f"confirm_rows[{i}] does not match the type of {row['sentence_id']}")
            listed.add(row["sentence_id"])
        if needed - listed:
            problems.append("every injury, referee, quote and transfer sentence needs a Confirm row: missing " + ", ".join(sorted(needed - listed)))
    flags = run["desk_editor_flags"]
    if not isinstance(flags, list) or not all(isinstance(x, dict) and set(x) == {"sentence_id", "text"} and _nonempty(x["text"]) for x in flags):
        problems.append("desk_editor_flags must be a list of {sentence_id, text}")
    approval = run["approval"]
    if not isinstance(approval, dict) or set(approval) != APPROVAL_KEYS or approval.get("status") not in APPROVAL_STATUSES:
        problems.append("approval must be {status, approved_at, approver_role, channel} with a known status")
    else:
        approved = approval["status"] == "approved"
        if approved != (state in ("APPROVED", "PUBLISHED")):
            problems.append("approval.status and state disagree")
        if approved and (not isinstance(approval["approved_at"], str) or not ISO_TIME.match(approval["approved_at"])
                         or not _nonempty(approval["approver_role"]) or not _nonempty(approval["channel"])):
            problems.append("an approved run must name approved_at (UTC, ending in Z), approver_role and channel")
        if not approved and approval["approved_at"] is not None:
            problems.append("a run that is not approved has no approved_at")
        if approved and provenance == DESIGN_PREVIEW:
            problems.append("a design preview run can never be approved")
    return problems


def verify_run_dir(runs_dir: Path) -> list[str]:
    """Check web/runs end to end: index, every run, every bundle recomputed, no stray files.

    Returns the run ids verified. Raises ValueError naming the first broken rule.
    """
    index_path = runs_dir / INDEX_NAME
    if not index_path.is_file():
        raise ValueError(f"{index_path} does not exist")
    index = json.loads(index_path.read_bytes().decode("utf-8"))
    problems = validate_index(index)
    if problems:
        raise ValueError("runs index: " + "; ".join(problems))
    expected_files = {INDEX_NAME, "README.txt"}
    verified = []
    for row in index["runs"]:
        run_path = runs_dir / row["file"]
        if not run_path.is_file():
            raise ValueError(f"runs index lists {row['file']}, which does not exist")
        raw = run_path.read_bytes()
        if b"\r" in raw:
            raise ValueError(f"{row['file']} has CR bytes; a CRLF checkout would change every hash")
        run = json.loads(raw.decode("utf-8"))
        problems = validate_run(run)
        if problems:
            raise ValueError(f"{row['file']}: " + "; ".join(problems))
        if run["run_id"] != row["run_id"] or run["provenance"] != row["provenance"]:
            raise ValueError(f"{row['file']}: run_id or provenance differs from the runs index")
        bundle_dir = runs_dir / run["bundle"]["dir"]
        manifest_bytes = (bundle_dir / BUNDLE_MANIFEST).read_bytes() if (bundle_dir / BUNDLE_MANIFEST).is_file() else b""
        if not manifest_bytes:
            raise ValueError(f"{row['file']}: bundle manifest {run['bundle']['dir']}/{BUNDLE_MANIFEST} is missing")
        artifacts = {
            p.name: p.read_bytes() for p in sorted(bundle_dir.iterdir()) if p.is_file() and p.name != BUNDLE_MANIFEST
        }
        stray_dirs = [p.name for p in bundle_dir.iterdir() if p.is_dir()]
        if stray_dirs:
            raise ValueError(f"{row['file']}: unexpected folders in the bundle: {', '.join(sorted(stray_dirs))}")
        check = verify_manifest(manifest_bytes, artifacts, expected_sha256=run["bundle"]["sha256"])
        if not check.ok:
            raise ValueError(f"{row['file']}: bundle does not verify: " + "; ".join(check.reasons))
        listed_files = {e["file"] for e in run["editions"]}
        if listed_files != set(artifacts):
            raise ValueError(f"{row['file']}: editions list {sorted(listed_files)} but the bundle holds {sorted(artifacts)}")
        sentence_ids = set()
        for e in run["editions"]:
            edition = json.loads(artifacts[e["file"]].decode("utf-8"))
            if edition.get("edition") != e["edition"] or edition.get("language") != e["language"]:
                raise ValueError(f"{row['file']}: {e['file']} does not hold edition {e['edition']} in {e['language']}")
            sentence_ids |= {s["sentence_id"] for s in edition.get("sentences", [])}
        unknown = (set(run["sentence_types"]) | set(run["sentence_gates"])) - sentence_ids
        unknown |= {r["sentence_id"] for r in run["confirm_rows"]} - sentence_ids
        unknown |= {f["sentence_id"] for f in run["desk_editor_flags"]} - sentence_ids
        if unknown:
            raise ValueError(f"{row['file']}: names sentences no edition holds: {', '.join(sorted(unknown))}")
        expected_files.add(row["file"])
        expected_files.add(run["bundle"]["dir"])
        verified.append(run["run_id"])
    present = {p.name for p in runs_dir.iterdir()}
    stray = sorted(present - expected_files)
    if stray:
        raise ValueError(f"files in {runs_dir.as_posix()} not listed in the runs index: {', '.join(stray)}")
    return verified


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webrun", description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="DIR", help="validate every run in DIR and recompute each bundle")
    group.add_argument("--write-sample", metavar="DIR", help="rewrite the hand-written design preview run into DIR")
    args = parser.parse_args(argv)
    if args.write_sample:
        os.makedirs(args.write_sample, exist_ok=True)
        run = write_sample(args.write_sample)
        sys.stdout.write(f"wrote {run['run_id']} bundle sha256 {run['bundle']['sha256']}\n")
        return 0
    try:
        ids = verify_run_dir(Path(args.check))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        sys.stdout.write(f"webrun: FAIL: {exc}\n")
        return 1
    sys.stdout.write(f"webrun: verified {len(ids)} runs: {', '.join(ids)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
