"""What each W-fulltime executor does, as plain async functions over the injected ports.

`agon.fleet.workflow` wraps each function in an Agent Framework executor with a fixed id; the
functions themselves import no Agent Framework code, so every branch is unit tested without it.
Each function takes a message's Draft and returns a NEW Draft (fan-out branches receive the same
object, so nothing here mutates its input).

Failure rules from spec section 4, all visible on the officer's card as flags, never silent:

* a model outage or a malformed answer: the step's documented fallback plus a flag;
* a Fact Gate mismatch: one retry through the Writer, then a template line with corrected claims;
* a translation outage: that edition is marked not available in this bundle;
* a rights refusal: the bundle is sealed with the refusal and no approval can be requested.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from typing import Any, Iterable, Literal

from agon.agents.ports import ModelOutputError, ModelUnavailable, PortUnavailable
from agon.agents.prompts import manifest_sha256
from agon.agents.roles import ask
from agon.fleet import templates
from agon.fleet.contracts import (
    CHANGES_REQUESTED,
    DECISION_REFUSED,
    EASY_READ,
    EDITION_LANGUAGE,
    FANOUT_EDITIONS,
    MAX_WRITER_ATTEMPTS,
    PUBLISH_ALREADY_REQUESTED,
    PUBLISH_REQUESTED,
    REFUSED_AT_INGEST,
    REFUSED_BY_GATE,
    ApprovalRequest,
    DecisionEnvelope,
    Draft,
    Localised,
    PublishRequest,
    RunInput,
    RunOutcome,
    SealedDraft,
)
from agon.fleet.deps import FleetDeps
from agon.fleet.sources import MatchRecord, MatchSourceError, load_facts
from agon.fleet import stores
from agon.governance import verifier
from agon.governance.claims import Sentence
from agon.governance.factgate import check_translation, run_factgate
from agon.governance.rights import run_rights
from agon.governance.seal import Artifact, canonical_utf8, gates_all_pass, seal, sha256_hex

TRIAGE_ROUTES: dict[str, str] = {
    "match_stat": "factgate",
    "injury_medical": "confirm",
    "disciplinary_referee": "confirm",
    "quote_attribution": "confirm",
    "transfer_contract": "confirm",
    "neutral_colour": "none",
}
DISCIPLINARY_KINDS = frozenset({"red_card", "yellow_card"})
TURNING_POINTS_MAX = 5
FALLBACK_TURNING_POINTS = 4
EASY_READ_MAX_WORDS = 20
EASY_READ_IDIOMS: tuple[str, ...] = (
    "back of the net", "against the run of play", "turned the tide", "park the bus", "game of two halves",
    "early doors", "into the mixer", "top bins", "worldie", "bags of", "on the front foot", "on the back foot",
)
EDITION_ORDER: tuple[str, ...] = ("en", *FANOUT_EDITIONS)


# ----------------------------------------------------------------- helpers


def _copy(draft: Draft) -> Draft:
    return copy.deepcopy(draft)


def add_flag(draft: Draft, code: str, detail: str, by: str, sentence_id: str | None = None) -> None:
    """Flags are only ever appended; no function in the fleet removes one."""
    entry: dict[str, Any] = {"code": code, "detail": detail, "by": by}
    if sentence_id is not None:
        entry["sentence_id"] = sentence_id
    if entry not in draft.flags:
        draft.flags.append(entry)


def record_for(deps: FleetDeps, draft: Draft) -> MatchRecord:
    return load_facts(deps.matches, draft.match_ref, draft.event_log_sha256)


def sentences_of(rows: Iterable[dict[str, Any]]) -> list[Sentence]:
    return [Sentence.from_dict(r) for r in rows]


def _header(record: MatchRecord) -> dict[str, Any]:
    home, away = record.meta["home"], record.meta["away"]
    return {
        "match_id": record.match_id,
        "league": record.roster.league_name,
        "home": {"id": home, "name": record.roster.clubs[home]},
        "away": {"id": away, "name": record.roster.clubs[away]},
    }


# ----------------------------------------------------------------- ingest, interpret, analyst


async def ingest(deps: FleetDeps, run: RunInput) -> Draft | RunOutcome:
    def refuse(code: str, detail: str) -> RunOutcome:
        return RunOutcome(run_id=run.run_id, status=REFUSED_AT_INGEST, bundle_sha256=None, code=code, detail=detail)

    if run.target not in verifier.TARGETS:
        return refuse("unknown_target", f"target {run.target!r} is not one of {list(verifier.TARGETS)}")
    try:
        record = deps.matches.load(run.match_ref)
    except MatchSourceError as exc:
        return refuse("match_not_found", str(exc))
    if not record.events:
        return refuse("no_events", f"match {record.match_id} has no events; the run is refused")
    draft = Draft(
        run_id=run.run_id,
        target=run.target,
        match_ref=dict(run.match_ref),
        match_id=record.match_id,
        home_id=record.meta["home"],
        away_id=record.meta["away"],
        event_log_sha256=record.event_log_sha256,
        seed_sha256=record.seed_sha256,
    )
    deps.ledger.advance(run.run_id, stores.DRAFT, f"ingested {len(record.events)} events", match_id=record.match_id)
    return draft


async def interpret(deps: FleetDeps, draft: Draft) -> Draft:
    d = _copy(draft)
    d.interpret = deps.interpret.interpret(record_for(deps, d))
    return d


async def analyst(deps: FleetDeps, draft: Draft) -> Draft:
    """The model picks turning points; code rejects invented ids, retries once, then falls back."""
    d = _copy(draft)
    record = record_for(deps, d)
    by_id = {e.event_id: e for e in record.events}
    candidates = d.interpret.get("candidates", [])
    payload: dict[str, Any] = {
        "match": _header(record),
        "final_score": d.interpret.get("final_score"),
        "interpret_source": d.interpret.get("source"),
        "windows": d.interpret.get("windows", []),
        "candidates": candidates,
    }
    picks: list[dict[str, str]] | None = None
    problems: list[str] = []
    for _attempt in (1, 2):
        try:
            answer = await ask(deps.model, "analyst", payload, deps.model_timeout_seconds)
        except ModelUnavailable as exc:
            add_flag(d, "analyst_unavailable", str(exc), "analyst")
            break
        except ModelOutputError as exc:
            problems = [str(exc)]
        else:
            problems = [f"event id {tp['event_id']} is not in the event log" for tp in answer if tp["event_id"] not in by_id]
            if len(answer) > TURNING_POINTS_MAX:
                problems.append(f"{len(answer)} turning points, the cap is {TURNING_POINTS_MAX}")
            if candidates and not answer:
                problems.append("no turning point was picked")
            if not problems:
                picks = answer
                break
        payload = {**payload, "feedback": problems}
    if picks is None:
        if problems:
            add_flag(d, "analyst_rejected", "; ".join(problems), "analyst")
        add_flag(d, "analyst_fallback", "turning points chosen by code: largest score-state swing first", "analyst")
        picks = [
            {"event_id": c["event_id"], "why": "Chosen by code: largest score-state swing.", "theme": "game_rhythm"}
            for c in candidates[:FALLBACK_TURNING_POINTS]
        ]
        author = "template"
    else:
        author = "model"
    d.turning_points = []
    for tp in picks:
        e = by_id[tp["event_id"]]
        d.turning_points.append(
            {
                "event_id": tp["event_id"],
                "minute": templates.display_minute(e.period, e.minute),
                "theme": tp["theme"],
                "why": tp["why"],
                "author": author,
            }
        )
    return d


# ----------------------------------------------------------------- writer, triage, factgate


async def writer(deps: FleetDeps, draft: Draft, retry: bool = False) -> Draft:
    d = _copy(draft)
    record = record_for(deps, d)
    brief = templates.writer_brief(record, d)
    current = {s["sentence_id"]: s for s in d.sentences}
    if retry:
        brief["feedback"] = [
            {"sentence_id": sid, "text": current[sid]["text"] if sid in current else "", "problems": reasons}
            for sid, reasons in sorted(d.writer_feedback.items())
        ]
    d.writer_attempts += 1
    try:
        sentences = await ask(deps.model, "writer", brief, deps.model_timeout_seconds)
    except (ModelUnavailable, ModelOutputError) as exc:
        code = "writer_unavailable" if isinstance(exc, ModelUnavailable) else "writer_output_invalid"
        if retry:
            add_flag(d, code, f"retry not written ({exc}); the Fact Gate falls back to template lines", "writer")
        else:
            d.sentences = [s.to_dict() for s in templates.template_recap(record)]
            add_flag(d, code, f"{exc}; template recap used instead of the Writer", "writer")
    else:
        if retry:
            wanted = set(d.writer_feedback)
            fresh = {s.sentence_id: s.to_dict() for s in sentences if s.sentence_id in wanted}
            d.sentences = [fresh.get(row["sentence_id"], row) for row in d.sentences]
            stray = sorted(s.sentence_id for s in sentences if s.sentence_id not in wanted)
            if stray:
                add_flag(d, "writer_retry_extra", f"retry rewrote sentences it was not asked to: {stray}; ignored", "writer")
        else:
            d.sentences = [s.to_dict() for s in sentences]
    d.writer_feedback = {}
    return d


def _route(sentence: Sentence, label: dict[str, Any] | None) -> dict[str, Any]:
    """The code table: the model measures (a label), code decides (a route)."""
    claim_ids = sorted({eid for c in sentence.claims for eid in c.event_ids})
    kinds = {c.value for c in sentence.claims if c.type == "event_ref"}
    model_type = label["type"] if label else None
    if kinds & DISCIPLINARY_KINDS:
        kind = "disciplinary_referee"
    elif model_type is None:
        kind = "match_stat" if sentence.claims else "neutral_colour"
    else:
        kind = model_type
    route = TRIAGE_ROUTES[kind]
    if kind != "neutral_colour" and not sentence.claims:
        route = "ungrounded"
    elif route == "none" and sentence.claims:
        route = "factgate"
    grounding = [g for g in label["grounding_ids"] if g in claim_ids] if label else claim_ids
    return {"type": kind, "model_type": model_type, "grounding_ids": grounding, "route": route}


async def triage(deps: FleetDeps, draft: Draft) -> Draft:
    d = _copy(draft)
    sentences = sentences_of(d.sentences)
    labels: dict[str, dict[str, Any]] = {}
    try:
        labels = await ask(deps.model, "triage", {"sentences": d.sentences}, deps.model_timeout_seconds)
    except (ModelUnavailable, ModelOutputError) as exc:
        add_flag(d, "triage_unavailable", f"{exc}; routed by the code table alone", "triage")
    d.triage = {s.sentence_id: _route(s, labels.get(s.sentence_id)) for s in sentences}
    d.confirm_rows = sorted(sid for sid, row in d.triage.items() if row["route"] == "confirm")
    return d


async def factgate(deps: FleetDeps, draft: Draft) -> tuple[Literal["retry", "checked"], Draft]:
    d = _copy(draft)
    record = record_for(deps, d)
    facts = record.facts()
    sentences = sentences_of(d.sentences)
    report = run_factgate(sentences, facts)
    reasons: dict[str, list[str]] = {v.sentence_id: list(v.reasons) for v in report.verdicts if not v.passed}
    for sid, row in d.triage.items():
        if row["route"] == "ungrounded":
            reasons.setdefault(sid, []).append(f"{row['type']} sentence makes no checkable claim")
    if reasons and d.writer_attempts < MAX_WRITER_ATTEMPTS:
        d.writer_feedback = reasons
        detail = "; ".join(f"{sid}: {' / '.join(r)}" for sid, r in sorted(reasons.items()))
        add_flag(d, "factgate_retry", f"sent back to the Writer once: {detail}", "factgate")
        return "retry", d
    final: list[Sentence] = []
    for s in sentences:
        if s.sentence_id in reasons:
            replacement = templates.template_line_for(s, record)
            final.append(replacement)
            add_flag(d, "factgate_template_line", "; ".join(reasons[s.sentence_id]), "factgate", s.sentence_id)
        else:
            final.append(s)
    if not final:
        final = templates.template_recap(record)
        add_flag(d, "writer_empty", "the Writer produced no sentences; template recap used", "factgate")
    d.sentences = [s.to_dict() for s in final]
    if reasons:
        # A template line answers a refused sentence, so its triage row is recomputed by code.
        d.triage.update({s.sentence_id: _route(s, None) for s in final if s.sentence_id in reasons})
        d.confirm_rows = sorted(sid for sid, row in d.triage.items() if row["route"] == "confirm")
    second = run_factgate(final, facts)
    verdict = "pass" if second.passed else "mismatch"
    still_wrong = [f"{v.sentence_id}: {r}" for v in second.verdicts if not v.passed for r in v.reasons]
    d.gate_verdicts["factgate"] = verdict
    d.gate_reasons["factgate"] = [f"{sid}: {r}" for sid, rs in sorted(reasons.items()) for r in rs] + still_wrong
    if still_wrong:
        add_flag(d, "factgate_mismatch", "template lines still disagree with the event log: " + "; ".join(still_wrong), "factgate")
    # CHECKED means "the Fact Gate ran and its verdict is recorded", not "it passed". A mismatch
    # still seals, so the officer sees the refusal on the card; `prepare_approval` then refuses
    # the bundle (SEALED to REFUSED) and no nonce is issued. The transition table is unchanged:
    # no run reaches SEALED without passing through this step.
    deps.ledger.advance(d.run_id, stores.CHECKED, f"fact gate {verdict}", factgate=verdict)
    return "checked", d


async def desk_editor(deps: FleetDeps, draft: Draft) -> Draft:
    """A critic that may add flags and Confirm rows and can never clear either."""
    d = _copy(draft)
    if not deps.desk_editor_enabled:
        add_flag(d, "desk_editor_not_run", "Desk Editor is switched off in this deployment", "desk_editor")
        return d
    payload = {
        "sentences": d.sentences,
        "interpret": {"source": d.interpret.get("source"), "candidates": d.interpret.get("candidates", [])},
        "flags": d.flags,
    }
    try:
        flags = await ask(deps.model, "desk_editor", payload, deps.model_timeout_seconds)
    except (ModelUnavailable, ModelOutputError) as exc:
        add_flag(d, "desk_editor_unavailable", f"Desk Editor unavailable: {exc}", "desk_editor")
        return d
    known = {s["sentence_id"] for s in d.sentences}
    rows = set(d.confirm_rows)
    for f in flags:
        if f["sentence_id"] not in known:
            add_flag(d, "desk_editor_unknown_sentence", f"flag for unknown sentence {f['sentence_id']} ignored", "desk_editor")
            continue
        add_flag(d, f"desk_{f['code']}", f["detail"], "desk_editor", f["sentence_id"])
        rows.add(f["sentence_id"])
    d.confirm_rows = sorted(rows)
    return d


# ----------------------------------------------------------------- localisation fan-out


def _protected_names(record: MatchRecord, sentences: list[Sentence]) -> list[str]:
    names: set[str] = set()
    for s in sentences:
        names.update(c.value for c in s.claims if c.type == "name" and isinstance(c.value, str))
        names.update(n for n in record.roster.all_names() if n in s.text)
    return sorted(names, key=lambda n: (-len(n), n))


async def localise(deps: FleetDeps, draft: Draft, language: str) -> Localised:
    """NMT, then the localiser-editor's post-edit. Always returns one message, available or not."""
    by = f"localise_{language}"
    record = record_for(deps, draft)
    sentences = sentences_of(draft.sentences)
    try:
        machine = await deps.translator.translate([s.text for s in sentences], language, _protected_names(record, sentences))
        if len(machine) != len(sentences):
            raise PortUnavailable(f"translator returned {len(machine)} lines for {len(sentences)}")
    except Exception as exc:  # noqa: BLE001 - every translator failure means "not in this bundle"
        flag = {"code": "edition_unavailable", "detail": f"{language} edition not available in this bundle: {exc}", "by": by}
        return Localised(draft=draft, edition=language, available=False, flags=[flag])
    flags: list[dict[str, Any]] = []
    payload = {
        "language": language,
        "sentences": [
            {"sentence_id": s.sentence_id, "source": s.text, "machine_translation": m} for s, m in zip(sentences, machine)
        ],
    }
    try:
        edited = await ask(deps.model, "localiser_editor", payload, deps.model_timeout_seconds)
    except (ModelUnavailable, ModelOutputError) as exc:
        edited = {}
        flags.append({"code": "post_edit_unavailable", "detail": f"{language}: {exc}; machine translation kept", "by": by})
    out = [
        Sentence(
            sentence_id=f"{s.sentence_id}-{language}",
            text=edited.get(s.sentence_id, m),
            language=language,
            claims=s.claims,
            author="model",
        ).to_dict()
        for s, m in zip(sentences, machine)
    ]
    return Localised(draft=draft, edition=language, available=True, sentences=out, flags=flags)


def easy_read_problems(text: str) -> list[str]:
    problems = []
    words = text.split()
    if len(words) > EASY_READ_MAX_WORDS:
        problems.append(f"{len(words)} words, the cap is {EASY_READ_MAX_WORDS}")
    lowered = text.casefold()
    problems += [f"idiom {i!r}" for i in EASY_READ_IDIOMS if i in lowered]
    if any(ch in text for ch in ";()"):
        problems.append("semicolon or brackets")
    return problems


async def easy_read(deps: FleetDeps, draft: Draft) -> Localised:
    """English easy-read edition by rules as code: one output sentence per input, same claims."""
    record = record_for(deps, draft)
    out, flags = [], []
    for s in sentences_of(draft.sentences):
        sid = f"{s.sentence_id}-{EASY_READ}"
        problems = easy_read_problems(s.text)
        if not problems:
            out.append(Sentence(sid, s.text, "en", s.claims, s.author).to_dict())
            continue
        text = templates.render_claims(s.claims, "en", record) or templates.WITHHELD_LINE["en"]
        out.append(Sentence(sid, text, "en", s.claims, "template").to_dict())
        flags.append({"code": "easy_read_template_line", "detail": "; ".join(problems), "by": "easy_read", "sentence_id": sid})
    return Localised(draft=draft, edition=EASY_READ, available=True, sentences=out, flags=flags)


async def factgate_lang(deps: FleetDeps, branches: list[Localised]) -> Draft:
    """Fan-in barrier: every edition carries the English claims and its prose agrees with them."""
    editions = sorted(b.edition for b in branches)
    if editions != sorted(FANOUT_EDITIONS):
        raise ValueError(f"fan-in expected one message per edition {sorted(FANOUT_EDITIONS)}, got {editions}")
    base = branches[0].draft
    if any(b.draft.run_id != base.run_id or b.draft.sentences != base.sentences for b in branches):
        raise ValueError("fan-in branches carry different drafts")
    d = _copy(base)
    record = record_for(deps, d)
    facts = record.facts()
    source = {s.sentence_id: s for s in sentences_of(d.sentences)}
    d.editions = {"en": list(d.sentences)}
    failed: list[str] = []
    for branch in sorted(branches, key=lambda b: EDITION_ORDER.index(b.edition)):
        for f in branch.flags:
            if f not in d.flags:
                d.flags.append(dict(f))
        if not branch.available:
            continue
        language = EDITION_LANGUAGE[branch.edition]
        suffix = f"-{branch.edition}"
        final = []
        for row in branch.sentences:
            t = Sentence.from_dict(row)
            src = source.get(t.sentence_id.removesuffix(suffix)) if t.sentence_id.endswith(suffix) else None
            if src is None:
                add_flag(d, "translation_unknown_sentence", f"{t.sentence_id} has no English source; dropped", "factgate_lang")
                continue
            verdict = check_translation(src, t, facts)
            if verdict.passed:
                final.append(row)
                continue
            text = templates.render_claims(src.claims, language, record) or templates.WITHHELD_LINE[language]
            fallback = Sentence(t.sentence_id, text, language, src.claims, "template")
            second = check_translation(src, fallback, facts)
            if not second.passed:
                failed += [f"{t.sentence_id}: {r}" for r in second.reasons]
            final.append(fallback.to_dict())
            add_flag(d, "translation_template_line", "; ".join(verdict.reasons), "factgate_lang", t.sentence_id)
        d.editions[branch.edition] = final
    d.gate_verdicts["factgate_lang"] = "mismatch" if failed else "pass"
    d.gate_reasons["factgate_lang"] = failed
    return d


# ----------------------------------------------------------------- rights, audio, seal


async def rights(deps: FleetDeps, draft: Draft) -> Draft:
    d = _copy(draft)
    record = record_for(deps, d)
    everything = [Sentence.from_dict(row) for key in EDITION_ORDER for row in d.editions.get(key, [])]
    report = run_rights(record.roster, everything)
    d.gate_verdicts["rights"] = report.status
    d.gate_reasons["rights"] = [f"{r.code} at {r.where}: {r.detail}" for r in report.refusals]
    for r in report.refusals:
        add_flag(d, "rights_refusal", f"{r.code} at {r.where}: {r.detail}", "rights")
    return d


async def audio(deps: FleetDeps, draft: Draft) -> Draft:
    """Speech per edition with an AI disclosure first; captions are published even without audio."""
    d = _copy(draft)
    if d.gate_verdicts.get("rights") != "pass":
        add_flag(d, "audio_not_produced", "no audio for a bundle the Rights Guard refused", "audio")
        return d
    for edition in EDITION_ORDER:
        rows = d.editions.get(edition)
        if not rows:
            continue
        language = EDITION_LANGUAGE[edition]
        disclosure = templates.AUDIO_DISCLOSURE[language]
        text = " ".join([disclosure, *(row["text"] for row in rows)])
        captions: dict[str, Any] = {
            "schema": "agon.captions/1",
            "edition": edition,
            "language": language,
            "disclosure": disclosure,
            "sentences": [{"sentence_id": r["sentence_id"], "text": r["text"]} for r in rows],
            "voice": None,
            "word_timings": None,
        }
        try:
            result = await deps.speech.synthesize(text, language)
        except Exception as exc:  # noqa: BLE001 - every speech failure leaves captions only
            add_flag(d, "audio_unavailable", f"{edition}: {exc}; captions published without audio", "audio")
        else:
            sha = deps.artifacts.put(result.audio)
            extension = "mp3" if result.media_type == "audio/mpeg" else "bin"
            d.artifacts[f"audio-{edition}.{extension}"] = {
                "sha256": sha, "media_type": result.media_type, "role": "audio", "language": language,
            }
            captions["voice"] = result.voice
            captions["word_timings"] = result.word_timings
        data = canonical_utf8(captions)
        d.artifacts[f"captions-{edition}.json"] = {
            "sha256": deps.artifacts.put(data), "media_type": "application/json", "role": "captions", "language": language,
        }
    return d


def _put_json(deps: FleetDeps, d: Draft, name: str, value: Any, role: str, language: str | None) -> None:
    entry: dict[str, Any] = {"sha256": deps.artifacts.put(canonical_utf8(value)), "media_type": "application/json", "role": role}
    if language is not None:
        entry["language"] = language
    d.artifacts[name] = entry


async def seal_step(deps: FleetDeps, draft: Draft) -> SealedDraft:
    d = _copy(draft)
    for edition in EDITION_ORDER:
        rows = d.editions.get(edition)
        if rows is None:
            continue
        report = {
            "schema": "agon.edition/1",
            "run_id": d.run_id,
            "match_id": d.match_id,
            "edition": edition,
            "language": EDITION_LANGUAGE[edition],
            "label": templates.EDITION_LABEL,
            "sentences": rows,
        }
        _put_json(deps, d, f"report-{edition}.json", report, "edition", EDITION_LANGUAGE[edition])
    card = {
        "schema": "agon.card/1",
        "run_id": d.run_id,
        "match_id": d.match_id,
        "target": d.target,
        "confirm_rows": d.confirm_rows,
        "flags": d.flags,
        "gate_reasons": d.gate_reasons,
        "triage": d.triage,
        "turning_points": d.turning_points,
        "editions": [e for e in EDITION_ORDER if e in d.editions],
        "interpret_source": d.interpret.get("source"),
        "interpret_note": d.interpret.get("note"),
    }
    _put_json(deps, d, "card.json", card, "card", None)
    artifacts = []
    for name in sorted(d.artifacts):
        entry = d.artifacts[name]
        data = deps.artifacts.get(entry["sha256"])
        if data is None:
            raise RuntimeError(f"artifact {name} ({entry['sha256']}) is missing from the artifact store")
        artifacts.append(Artifact(name, data, entry["media_type"], entry["role"], entry.get("language")))
    author_tags = {row["sentence_id"]: row["author"] for key in EDITION_ORDER for row in d.editions.get(key, [])}
    attested = ["report-en.json", *d.confirm_rows]
    gate_cleared = [name for name in sorted(d.artifacts) if name != "report-en.json"]
    deployment_ids = {**deps.deployment_ids, "prompts": manifest_sha256()}
    source = d.interpret.get("source")
    if isinstance(source, str):
        deployment_ids["interpret"] = source
    sealed = seal(
        kind="fulltime",
        match_id=d.match_id,
        seed_hash=d.seed_sha256,
        event_log_hash=d.event_log_sha256,
        charter_hash=None,
        artifacts=artifacts,
        gate_verdicts=d.gate_verdicts,
        author_tags=author_tags,
        attested=attested,
        gate_cleared=gate_cleared,
        deployment_ids=deployment_ids,
    )
    deps.ledger.advance(d.run_id, stores.SEALED, "sealed", bundle_sha256=sealed.bundle_sha256)
    return SealedDraft(draft=d, bundle_sha256=sealed.bundle_sha256, manifest_json=sealed.manifest_bytes.decode("utf-8"))


# ----------------------------------------------------------------- approval and publish request


async def prepare_approval(deps: FleetDeps, sealed: SealedDraft) -> ApprovalRequest | RunOutcome:
    """Register the sealed manifest, issue the single-use nonce, build the card payload.

    A bundle any gate refused gets no nonce and so no Approve button, server side too.
    """
    d = sealed.draft
    data = sealed.manifest_json.encode("utf-8")
    if sha256_hex(data) != sealed.bundle_sha256:
        raise RuntimeError("sealed manifest bytes do not hash to the sealed bundle hash")
    manifest = json.loads(sealed.manifest_json)

    def refused(code: str, detail: str) -> RunOutcome:
        deps.ledger.advance(d.run_id, stores.REFUSED, detail)
        return RunOutcome(run_id=d.run_id, status=REFUSED_BY_GATE, bundle_sha256=sealed.bundle_sha256, code=code, detail=detail)

    if not gates_all_pass(manifest):
        reasons = [f"{gate}: {r}" for gate in sorted(d.gate_reasons) for r in d.gate_reasons[gate]]
        return refused("bundle_refused_by_gate", "; ".join(reasons) or "a gate refused this bundle")
    verifier.register_sealed_bundle(deps.store, data)
    now = deps.clock()
    try:
        nonce = verifier.issue_nonce(
            deps.store,
            sealed.bundle_sha256,
            target=d.target,
            now=now,
            ttl_seconds=deps.approval_ttl_seconds,
            confirm_rows=d.confirm_rows,
        )
    except verifier.IssueError as exc:
        return refused("nonce_refused", str(exc))
    return ApprovalRequest(
        run_id=d.run_id,
        target=d.target,
        bundle_sha256=sealed.bundle_sha256,
        nonce=nonce,
        expiry=(now + timedelta(seconds=deps.approval_ttl_seconds)).isoformat(),
        attested_scope={k: list(v) for k, v in manifest["attested_scope"].items()},
        confirm_rows=list(d.confirm_rows),
    )


async def decide(
    deps: FleetDeps, request: ApprovalRequest, envelope: DecisionEnvelope, sealed_manifest_json: str | None
) -> RunOutcome | PublishRequest:
    """The response handler's body: re-check the sealed bytes, then delegate to the one verifier."""

    def refused(code: str, detail: str) -> RunOutcome:
        return RunOutcome(run_id=request.run_id, status=DECISION_REFUSED, bundle_sha256=request.bundle_sha256, code=code, detail=detail)

    if sealed_manifest_json is None or sha256_hex(sealed_manifest_json.encode("utf-8")) != request.bundle_sha256:
        return refused("sealed_state_mismatch", "the sealed manifest in workflow state no longer hashes to the request")
    if envelope.bundle_sha256 != request.bundle_sha256:
        return refused("hash_mismatch", "the decision names a different bundle than the request")
    if envelope.nonce != request.nonce:
        return refused("nonce_mismatch", "the decision carries a different nonce than the request")
    try:
        received_at = datetime.fromisoformat(envelope.received_at)
    except (TypeError, ValueError):
        return refused("malformed_decision", "received_at is not an ISO timestamp")
    decision = verifier.ApprovalDecision(
        bundle_sha256=envelope.bundle_sha256,
        nonce=envelope.nonce,
        decision=envelope.decision,
        approver=envelope.approver,
        channel=envelope.channel,
        received_at=received_at,
        confirmed_rows=frozenset(envelope.confirmed_rows),
    )
    result = verifier.verify(decision, deps.store, deps.clock())
    if not result.ok and result.code == "already_consumed":
        recovered = _recover_publish_request(deps, request)
        if recovered is not None:
            return recovered
    if not result.ok:
        return refused(result.code, result.detail)
    if deps.ledger.status(request.run_id) == stores.SEALED:
        deps.ledger.advance(request.run_id, stores.AWAITING_APPROVAL, "recorded when the decision arrived")
    if result.decision == "changes":
        deps.ledger.advance(request.run_id, stores.CHANGES_REQUESTED, result.detail)
        return RunOutcome(
            run_id=request.run_id,
            status=CHANGES_REQUESTED,
            bundle_sha256=request.bundle_sha256,
            code=result.code,
            detail=result.detail,
        )
    deps.ledger.advance(request.run_id, stores.APPROVED, result.detail, approval_id=result.approval_id)
    return PublishRequest(
        run_id=request.run_id,
        target=request.target,
        bundle_sha256=request.bundle_sha256,
        approval_id=str(result.approval_id),
    )


def _recover_publish_request(deps: FleetDeps, request: ApprovalRequest) -> PublishRequest | None:
    """Close the crash window between consume-once and the enqueue.

    If a worker died after `verify` consumed the nonce but before `publish_request` ran, every
    replay of the pause checkpoint meets "already consumed". The approval record the verifier
    wrote is the evidence: when it says "approve" for this bundle and target, the publish request
    is emitted again. The queue is create-only by approval id, so this can never publish twice.
    """
    approval_id = verifier.approval_id_for(request.nonce)
    doc = deps.store.read(verifier.APPROVALS, approval_id)
    if doc is None:
        return None
    body = doc.body
    if body.get("decision") != "approve" or body.get("bundle_sha256") != request.bundle_sha256 or body.get("target") != request.target:
        return None
    if deps.ledger.status(request.run_id) in (stores.SEALED, stores.AWAITING_APPROVAL):
        if deps.ledger.status(request.run_id) == stores.SEALED:
            deps.ledger.advance(request.run_id, stores.AWAITING_APPROVAL, "recorded when the decision arrived")
        deps.ledger.advance(request.run_id, stores.APPROVED, "recovered from the approval record", approval_id=approval_id)
    return PublishRequest(run_id=request.run_id, target=request.target, bundle_sha256=request.bundle_sha256, approval_id=approval_id)


async def enqueue_publish(deps: FleetDeps, request: PublishRequest) -> RunOutcome:
    created = deps.publish_queue.enqueue(request)
    return RunOutcome(
        run_id=request.run_id,
        status=PUBLISH_REQUESTED if created else PUBLISH_ALREADY_REQUESTED,
        bundle_sha256=request.bundle_sha256,
        code="publish_requested" if created else "already_enqueued",
        detail=f"approval {request.approval_id}",
    )

