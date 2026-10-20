"""Export a published bundle as a recorded run the console shows (agon.web-run/1, provenance "pipeline").

Usage, from a checkout of the repository:

    PYTHONPATH=src python -m agon.webrecord \\
        --feed-base-url https://<storage account>.blob.core.windows.net/feed/ \\
        --bundle <bundle sha256> --commit <40-character commit sha> \\
        --approver-role approver --channel teams \\
        --out web/runs --readiness ci/readiness.json
    PYTHONPATH=src python -m agon.webrun --check web/runs

    --local DIR reads a copy of the container instead of the feed URL (same layout: DIR/<sha>/...).
    --readiness-only RUN_JSON rewrites ci/readiness.json for a run already in web/runs.

What it reads, all public (the feed container allows anonymous blob reads, no listing):

* <feed>/<sha>/manifest.json: the sealed manifest. Its sha256 must equal --bundle, and its shape,
  canonical form and gate verdicts are checked by agon.governance.seal.
* <feed>/<sha>/<artifact> for every artifact the manifest lists: each must match its sha256 and
  length. The only artifact that may be absent is the payload of a destination the officer did not
  tick, which the Publisher never publishes (agon.governance.publisher), so the approved set is the
  set of offered destinations whose payload IS published.
* the destinations' own files, to tell delivered from waiting: site/<sha>/index.html, the feed item
  agon:<target>:<sha> in the version feeds/latest.json names, previews/<id>/<sha>/payload.json and
  outbox/<id>/<sha>/message.json.

What it cannot read: the approval record and the receipt live in Cosmos, not in the container. So
the approver role and the channel are required arguments; the approval time is read from the
outbox message or the feed item when those destinations were ticked (they carry the verifier's
time), and --approved-at is required otherwise and must agree when both exist. The commit is not
in the bundle either: pass the commit of the fleet image that produced the run (the deploy run
that built the job image names it), as 40 hex characters.

What it writes into --out: <run_id>.json, <run_id>/ holding the manifest and every published
artifact byte for byte (so Verify recomputes the real hash), and index.json with the new run FIRST,
so the console shows it by default; every run already listed stays after it, the hand-written
design preview included. With --readiness it rewrites the three anchored label markers of the demo
target to the new default run (ci/web_check.py page_label_markers, the same strings the console
renders) and adds the demo-recorded targets, so the live gate keeps checking the exact labels.

The names, the score and the generator come from the seed in the match id, and the export is
refused unless they reproduce the manifest's seed_sha256 and event_log_sha256. The Newsroom Wall
lines are rebuilt from the published bundle, and the first line says so. Sealed text cannot be
edited, so every file it would write is run through the same rules as ci/check_dashes.py,
ci/web_check.py and ci/readiness.py first, and the export is refused, naming the file and the
rule, when one fails. Nothing is written until the whole export passes agon.webrun.verify_run_dir
in a scratch copy.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Protocol, Sequence

from agon import webrun
from agon.governance import destinations as dest_mod
from agon.governance.recompute import MatchFacts, Roster, score_after
from agon.governance.seal import (
    canonical_utf8,
    event_log_sha256,
    gates_all_pass,
    is_sha256_hex,
    offered_destinations,
    seed_sha256,
    sha256_hex,
    verify_manifest,
)
from agon.governance.verifier import CHANNELS
from agon.publish.webview import destinations_for_run
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.world import build_world

MANIFEST = webrun.BUNDLE_MANIFEST
MATCH_ID = re.compile(r"^s([0-9]{1,9})-m([0-9]{1,4})$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
TARGETS = ("feed", "sandbox")
EDITION_ORDER = ("en", "el", "es", "fr", "easy")
EDITION_NAMES = {"en": "English", "el": "Greek", "es": "Spanish", "fr": "French", "easy": "Easy-read English"}
GATE_NAMES = {"factgate": "Fact Gate", "factgate_lang": "Fact Gate, other editions", "rights": "Rights Guard"}
CHANNEL_WORDS = {"teams": "Microsoft Teams", "outlook": "an Outlook approval email", "signed_link": "a signed link", "console": "the console"}
STATE_STEPS = (("DRAFT", 2), ("CHECKED", 4), ("SEALED", 6), ("AWAITING_APPROVAL", 7), ("APPROVED", 8), ("PUBLISHED", 9))
LABEL_PREFIXES = ('id="wall-label">', 'id="approve-label">', 'id="recap-label">')
RECORDED_TARGETS = ("demo-recorded", "demo-recorded-bundle")
HOST = "https://__AGON_SWA_HOSTNAME__"
TEXT_SUFFIXES = (".json", ".txt", ".vtt", ".html", ".xml", ".md")


class ExportError(ValueError):
    """The bundle cannot be shown as a recorded run; the message says why."""


# ----------------------------------------------------------------- reading the container


class Feed(Protocol):
    def get(self, path: str) -> bytes | None: ...


def _safe_path(path: str) -> str:
    parts = path.split("/")
    if not path or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ExportError(f"refusing the container path {path!r}")
    return path


class HttpFeed:
    """The public feed container over https: a 404 is a missing blob, anything else an error."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        if not base_url.startswith("https://"):
            raise ExportError("--feed-base-url must be an https URL")
        self.base = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def get(self, path: str) -> bytes | None:
        url = self.base + _safe_path(path)
        request = urllib.request.Request(url, headers={"User-Agent": "agon-webrecord/1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - https only, checked above
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise ExportError(f"{url} answered {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ExportError(f"{url} could not be read: {exc}") from exc


class LocalFeed:
    """A copy of the container on disk, laid out as the container is (DIR/<sha>/manifest.json)."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def get(self, path: str) -> bytes | None:
        target = (self.root / _safe_path(path)).resolve()
        if self.root not in target.parents or not target.is_file():
            return None
        return target.read_bytes()


def _json(data: bytes, what: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(f"{what} is not UTF-8 JSON: {exc}") from exc


def utc_z(value: str, what: str) -> str:
    """An ISO time with a zone, as YYYY-MM-DDTHH:MM:SSZ in UTC (the run format's form)."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ExportError(f"{what} {value!r} is not an ISO 8601 time") from exc
    if moment.tzinfo is None:
        raise ExportError(f"{what} {value!r} has no time zone")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Published:
    sha: str
    target: str
    manifest_bytes: bytes
    manifest: dict[str, Any]
    artifacts: dict[str, bytes]
    approved: tuple[str, ...]
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    public_times: dict[str, str] = field(default_factory=dict)


def read_published(feed: Feed, sha: str, target: str = "feed") -> Published:
    """Read and re-hash everything the Publisher published for one bundle."""
    if not is_sha256_hex(sha):
        raise ExportError("--bundle must be 64 lowercase hex characters")
    if target not in TARGETS:
        raise ExportError(f"--target must be one of {', '.join(TARGETS)}")
    raw = feed.get(f"{sha}/{MANIFEST}")
    if raw is None:
        raise ExportError(f"{sha}/{MANIFEST} is not in the container: this bundle was never published there")
    check = verify_manifest(raw, None, expected_sha256=sha)
    if not check.ok or check.manifest is None:
        raise ExportError("the published manifest does not verify: " + "; ".join(check.reasons))
    manifest = check.manifest
    if not gates_all_pass(manifest):
        raise ExportError("a gate did not pass in this manifest, and the Publisher never publishes such a bundle")
    offered = offered_destinations(manifest)
    payload_of = {dest_mod.artifact_name(d): d for d in offered}
    artifacts: dict[str, bytes] = {}
    for name in sorted(manifest["artifacts"]):
        if not webrun.ARTIFACT_FILE.match(name):
            raise ExportError(f"artifact name {name!r} is not a plain file name")
        data = feed.get(f"{sha}/{name}")
        if data is None:
            if name in payload_of:
                continue
            raise ExportError(f"{name} is listed in the manifest but not published; only an unticked destination's payload may be absent")
        entry = manifest["artifacts"][name]
        if entry.get("sha256") != sha256_hex(data) or entry.get("bytes") != len(data):
            raise ExportError(f"{name} does not match its sha256 or length in the manifest")
        artifacts[name] = data
    approved = tuple(d for d in offered if dest_mod.artifact_name(d) in artifacts)
    if offered and not approved:
        raise ExportError("no destination payload is published, so no approval reached the Publisher for this bundle")
    published = Published(sha, target, raw, manifest, artifacts, approved)
    _read_deliveries(feed, published)
    return published


def _read_deliveries(feed: Feed, p: Published) -> None:
    """Receipt-like entries from the destinations' public files, and the approval times they carry."""
    sha, target = p.sha, p.target
    if "club_site" in p.approved and feed.get(f"site/{sha}/index.html") is not None:
        p.entries["club_site"] = {"page": f"{target}/site/{sha}/index.html"}
    if "feeds" in p.approved:
        pointer_bytes = feed.get("feeds/latest.json")
        if pointer_bytes is not None:
            pointer = _json(pointer_bytes, "feeds/latest.json")
            feed_path = pointer.get("feed_json") if isinstance(pointer, dict) else None
            feed_bytes = feed.get(feed_path) if isinstance(feed_path, str) else None
            if feed_bytes is not None and sha256_hex(feed_bytes) == pointer.get("feed_json_sha256"):
                items = _json(feed_bytes, feed_path).get("items", [])
                item = next((i for i in items if isinstance(i, dict) and i.get("id") == f"agon:{target}:{sha}"), None)
                if item is not None:
                    p.entries["feeds"] = {"files": {f"{target}/{feed_path}": pointer["feed_json_sha256"]}}
                    if isinstance(item.get("date_published"), str):
                        p.public_times["the feed item"] = utc_z(item["date_published"], "the feed item's date_published")
    for dest_id in ("instagram", "youtube"):
        path = f"previews/{dest_id}/{sha}/payload.json"
        if dest_id in p.approved and feed.get(path) is not None:
            p.entries[dest_id] = {"preview": f"{target}/{path}"}
    for dest_id in ("newsroom_teams", "journalist_email"):
        path = f"outbox/{dest_id}/{sha}/message.json"
        data = feed.get(path) if dest_id in p.approved else None
        if data is not None:
            message = _json(data, path)
            p.entries[dest_id] = {"message": f"{target}/{path}"}
            if isinstance(message, dict) and isinstance(message.get("approved_at"), str):
                p.public_times[f"the {dest_id} outbox message"] = utc_z(message["approved_at"], f"{path} approved_at")


def approval_time(p: Published, given: str | None) -> str:
    """The verifier's time: from the public files when they carry it, else from --approved-at."""
    values = set(p.public_times.values())
    if len(values) > 1:
        raise ExportError(f"the published files disagree on the approval time: {p.public_times}")
    public = next(iter(values), None)
    if given is not None:
        stated = utc_z(given, "--approved-at")
        if public is not None and stated != public:
            raise ExportError(f"--approved-at {stated} disagrees with {public} in the published files")
        return stated
    if public is None:
        raise ExportError("no published file carries the approval time (neither feeds nor an outbox was ticked): pass --approved-at")
    return public


# ----------------------------------------------------------------- the match, from the seed


@dataclass(frozen=True)
class MatchHeader:
    seed: int
    home: dict[str, str]
    away: dict[str, str]
    score: dict[str, int]


def match_header(manifest: Mapping[str, Any]) -> MatchHeader:
    """Names and score from the synthetic generator, refused unless they reproduce the manifest's hashes."""
    m = MATCH_ID.match(str(manifest.get("match_id", "")))
    if not m:
        raise ExportError(f"match id {manifest.get('match_id')!r} is not s<seed>-m<index>")
    seed, index = int(m.group(1)), int(m.group(2))
    if seed_sha256(seed, index, GENERATOR_VERSION) != manifest.get("seed_sha256"):
        raise ExportError(f"seed {seed}, match {index} and generator {GENERATOR_VERSION} do not reproduce the manifest's seed_sha256")
    league = build_world(seed)
    result = simulate_match(league, index)
    if event_log_sha256(result.events) != manifest.get("event_log_sha256"):
        raise ExportError("the regenerated event log does not hash to the manifest's event_log_sha256")
    roster = Roster.from_league(league)
    home, away = result.meta["home"], result.meta["away"]
    facts = MatchFacts(events=tuple(result.events), home_id=home, away_id=away, roster=roster)
    final = score_after(facts, None)
    return MatchHeader(
        seed=seed,
        home={"club_id": home, "name": roster.clubs[home]},
        away={"club_id": away, "name": roster.clubs[away]},
        score={"home": int(final["home"]), "away": int(final["away"])},
    )


# ----------------------------------------------------------------- the run


def _editions(p: Published) -> tuple[list[dict[str, str]], dict[str, list[dict[str, Any]]]]:
    metas: list[dict[str, str]] = []
    sentences: dict[str, list[dict[str, Any]]] = {}
    for edition in EDITION_ORDER:
        name = f"report-{edition}.json"
        if name not in p.artifacts:
            continue
        body = _json(p.artifacts[name], name)
        if not isinstance(body, dict) or body.get("edition") != edition or not isinstance(body.get("sentences"), list):
            raise ExportError(f"{name} does not hold the {edition} edition")
        language = body.get("language")
        if not isinstance(language, str):
            raise ExportError(f"{name} names no language")
        metas.append({"edition": edition, "file": name, "language": language, "name": EDITION_NAMES[edition]})
        sentences[edition] = [s for s in body["sentences"] if isinstance(s, dict)]
    if "en" not in sentences:
        raise ExportError("the bundle has no English edition (report-en.json)")
    return metas, sentences


def _card(p: Published) -> dict[str, Any]:
    if "card.json" not in p.artifacts:
        raise ExportError("the bundle has no card.json, so the Confirm rows and the triage are unknown")
    card = _json(p.artifacts["card.json"], "card.json")
    if not isinstance(card, dict) or not isinstance(card.get("triage"), dict) or not isinstance(card.get("confirm_rows"), list):
        raise ExportError("card.json holds no triage or Confirm rows")
    return card


def _join(words: Sequence[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def build_run(
    p: Published,
    *,
    commit: str,
    approver_role: str,
    channel: str,
    approved_at: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """The agon.web-run/1 record for one published bundle. Raises ExportError when it cannot be honest."""
    if not COMMIT.match(commit):
        raise ExportError("--commit must be the full 40-character lowercase commit sha")
    if channel not in CHANNELS:
        raise ExportError(f"--channel must be one of {', '.join(CHANNELS)}")
    if not approver_role.strip():
        raise ExportError("--approver-role must name the role that approved")
    run_id = run_id or f"run-{p.sha[:12]}"
    if not webrun.RUN_ID.match(run_id):
        raise ExportError(f"run id {run_id!r} must be lowercase letters, digits and hyphens")
    header = match_header(p.manifest)
    metas, sentences = _editions(p)
    card = _card(p)
    verdicts = p.manifest["gate_verdicts"]
    english = [str(s.get("sentence_id")) for s in sentences["en"]]
    triage = card["triage"]
    types: dict[str, str] = {}
    for sid in english:
        row = triage.get(sid)
        if not isinstance(row, dict) or row.get("type") not in webrun.SENTENCE_TYPES:
            raise ExportError(f"card.json has no sentence type for {sid}")
        types[sid] = row["type"]
    needs_confirm = sorted(sid for sid, t in types.items() if t in webrun.CONFIRM_TYPES)
    if needs_confirm != sorted(str(r) for r in card["confirm_rows"]):
        raise ExportError(
            f"the card asked the officer to confirm {sorted(card['confirm_rows'])}, but the run format needs a Confirm row for "
            f"every injury, referee, quote and transfer sentence: {needs_confirm}"
        )
    confirm_rows = [
        {"row_id": f"confirm-{sid}", "sentence_id": sid, "type": types[sid], "label": webrun.CONFIRM_TYPES[types[sid]]}
        for sid in needs_confirm
    ]
    all_ids = {str(s.get("sentence_id")) for rows in sentences.values() for s in rows}
    flags = [
        {"sentence_id": f["sentence_id"], "text": str(f.get("detail") or f.get("code"))}
        for f in card.get("flags", [])
        if isinstance(f, dict) and f.get("sentence_id") in all_ids and (f.get("detail") or f.get("code"))
    ]
    sentence_gates: dict[str, dict[str, str]] = {}
    for edition, rows in sentences.items():
        check = "factgate" if edition == "en" else "factgate_lang"
        for s in rows:
            sentence_gates[str(s["sentence_id"])] = {check: verdicts[check], "rights": verdicts["rights"]}
    reasons = card.get("gate_reasons") if isinstance(card.get("gate_reasons"), dict) else {}
    gates = [
        {
            "gate": gate,
            "name": GATE_NAMES.get(gate, gate),
            "verdict": verdict,
            "detail": f"{GATE_NAMES.get(gate, gate)}: {verdict}, as sealed in the bundle manifest.",
            "evidence": "; ".join(str(r) for r in reasons.get(gate, [])) or f"manifest gate_verdicts.{gate} = {verdict}",
        }
        for gate, verdict in sorted(verdicts.items())
    ]
    payloads = {d: p.artifacts.get(dest_mod.artifact_name(d), b"{}") for d in offered_destinations(p.manifest)}
    destinations = destinations_for_run(
        p.manifest, payloads, receipt={"destinations_approved": list(p.approved), "destinations": p.entries}
    )
    authors = [str(s.get("author")) for s in sentences["en"]]
    by_model, by_template = authors.count("model"), authors.count("template")
    languages = [EDITION_NAMES[m["edition"]] for m in metas if m["edition"] != "en"]
    delivered = [r["label"] for r in destinations if r["status"] == "delivered"]
    prepared = [r["label"] for r in destinations if r["status"] == "preview_not_posted"]
    flagged = len(flags)
    desks = [
        {"desk": "whistle", "name": "Whistle", "lines": [
            {"step": 1, "text": "This trace is rebuilt from the published bundle, not taken from the live run log."},
            {"step": 1, "text": f"Full time: {header.home['name']} {header.score['home']}, {header.away['name']} {header.score['away']}. "
                                f"The event log was frozen for the report."},
        ]},
        {"desk": "writer", "name": "Writer", "lines": [
            {"step": 2, "text": f"Wrote {len(english)} sentences for the headline report: {by_model} by a Microsoft Foundry agent, "
                                f"{by_template} from templates."},
        ]},
        {"desk": "factgate", "name": "Fact Gate", "lines": [
            # What agon.governance.factgate checks: the claims, recomputed from the log, and the
            # numbers written in digits; a number spelled out in words is out of its scope.
            {"step": 3, "text": "Recomputed every number, name and minute the headline report claims from the event log, "
                                f"and matched every number written in digits to a claim: {verdicts['factgate']}."},
            {"step": 5, "text": f"Checked every translated and easy-read sentence: {verdicts['factgate_lang']}."},
        ]},
        {"desk": "desk_editor", "name": "Desk Editor", "lines": [
            {"step": 4, "text": (f"Flagged {flagged} sentence{'' if flagged == 1 else 's'} for a person; "
                                 if flagged else "Flagged no sentence for a person; ")
                                + f"{len(confirm_rows)} Confirm row{'' if len(confirm_rows) == 1 else 's'} on the card."},
        ]},
        {"desk": "localise", "name": "Localisers", "lines": [
            {"step": 4, "text": _localise_line(languages)},
        ]},
        {"desk": "rights", "name": "Rights Guard", "lines": [
            {"step": 5, "text": f"Screened every name in {len(all_ids)} sentences: {verdicts['rights']}."},
        ]},
        {"desk": "seal", "name": "Seal and approval", "lines": [
            {"step": 6, "text": f"Sealed {len(p.manifest['artifacts'])} artifacts under one hash, {p.sha[:12]}."},
            {"step": 7, "text": "Waited for the club media officer to approve that exact hash."},
            {"step": 8, "text": f"Approved on {CHANNEL_WORDS[channel]} by the {approver_role} at {webrun_utc(approved_at)}."},
            {"step": 9, "text": ("Published: " + _join(delivered) + " received renderings of the approved payloads" if delivered else "Published")
                                + ("; " + _join(prepared) + " were prepared and not posted or sent." if prepared else ".")},
        ]},
    ]
    handoffs = [
        {"step": 3, "from": "writer", "to": "factgate", "text": "Headline report handed to the Fact Gate."},
        {"step": 4, "from": "factgate", "to": "desk_editor", "text": "Checked report handed to the Desk Editor."},
        {"step": 5, "from": "localise", "to": "factgate", "text": "Editions handed to the Fact Gate in every language."},
        {"step": 5, "from": "factgate", "to": "rights", "text": "Checked editions handed to the Rights Guard."},
        {"step": 6, "from": "rights", "to": "seal", "text": "Cleared editions handed to the seal."},
        {"step": 7, "from": "seal", "to": "approval", "text": "Sealed bundle handed to the approval card."},
    ]
    run = {
        "format": webrun.RUN_FORMAT,
        "run_id": run_id,
        "provenance": webrun.PIPELINE,
        "label": f"Recorded run, {approved_at[:10]}, commit {commit[:7]}",
        "seed": header.seed,
        "match_id": p.manifest["match_id"],
        "generator": GENERATOR_VERSION,
        "home": header.home,
        "away": header.away,
        "score": header.score,
        "bundle": {"sha256": p.sha, "dir": run_id, "manifest": MANIFEST},
        "state": "PUBLISHED",
        "states": [{"state": s, "step": n} for s, n in STATE_STEPS],
        "desks": desks,
        "handoffs": handoffs,
        "gates": gates,
        "editions": metas,
        "sentence_types": types,
        "sentence_gates": sentence_gates,
        "confirm_rows": confirm_rows,
        "desk_editor_flags": flags,
        "approval": {
            "status": "approved",
            "approved_at": approved_at,
            "approver_role": approver_role,
            "channel": channel,
            "approved_bundle_sha256": p.sha,
        },
        "destinations": destinations,
    }
    problems = webrun.validate_run(run)
    if problems:
        raise ExportError("the exported run breaks the run format: " + "; ".join(problems))
    return run


def webrun_utc(iso: str) -> str:
    """2026-10-21T18:05:00Z as 18:05 UTC on 2026-10-21, the console's wording."""
    return f"{iso[11:16]} UTC on {iso[:10]}"


def _localise_line(languages: list[str]) -> str:
    """The Localisers desk line: the translated editions, and the easy-read edition apart, because
    agon.fleet.steps.easy_read makes it by rules in code, not by a localiser."""
    easy = EDITION_NAMES["easy"]
    translated = [name for name in languages if name != easy]
    parts = [f"Wrote the {_join(translated)} edition{'' if len(translated) == 1 else 's'}"] if translated else []
    if easy in languages:
        parts.append("the easy-read edition was made by rules in code")
    if not parts:
        return "Wrote no other edition."
    text = "; ".join(parts) + "."
    return text[0].upper() + text[1:]


# ----------------------------------------------------------------- the repository's own text rules


def _ci_module(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "ci" / f"{name}.py"
    if not path.is_file():
        raise ExportError("run the exporter from a checkout of the repository: it applies the rules in ci/ before writing")
    spec = importlib.util.spec_from_file_location(f"agon_webrecord_{name}", path)
    if spec is None or spec.loader is None:
        raise ExportError(f"cannot load ci/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def text_problems(files: Mapping[str, bytes]) -> list[str]:
    """Dashes (ci/check_dashes.py and ci/web_check.py) and the live gate's rules (real names,
    self-scores, the banned words of ci/readiness.py) in every text file the export writes: the
    page and the gate fetch the run and its editions, and tests/test_web_runs.py scans every file
    under web/ with the same rules, so a payload that trips one is refused here, not in CI."""
    web_check = _ci_module("web_check")
    readiness = _ci_module("readiness")
    rules = readiness.build_rules()
    problems: list[str] = []
    for name, data in sorted(files.items()):
        if not name.endswith(TEXT_SUFFIXES):
            continue
        text = data.decode("utf-8")
        problems += [f"{name}: {v.message} (line {v.line})" for v in web_check.check_dashes(name, text)]
        problems += [f"{name}: {hit}" for hit in readiness.forbidden_hits(text, rules)]
    return problems


# ----------------------------------------------------------------- writing


def run_files(run: dict[str, Any], p: Published) -> dict[str, bytes]:
    """Every file the export adds under the runs folder, by its path relative to that folder."""
    files = {f"{run['run_id']}.json": webrun.run_bytes(run), f"{run['run_id']}/{MANIFEST}": p.manifest_bytes}
    for name, data in p.artifacts.items():
        files[f"{run['run_id']}/{name}"] = data
    return files


def _index_with(runs_dir: Path, run: dict[str, Any]) -> bytes:
    index_path = runs_dir / webrun.INDEX_NAME
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"format": webrun.INDEX_FORMAT, "runs": []}
    rows = index.get("runs", [])
    if any(r.get("run_id") == run["run_id"] for r in rows):
        raise ExportError(f"{run['run_id']} is already in {index_path}; a recorded run is never overwritten")
    row = {"file": f"{run['run_id']}.json", "provenance": run["provenance"], "run_id": run["run_id"]}
    return canonical_utf8({"format": webrun.INDEX_FORMAT, "runs": [row, *rows]}) + b"\n"


def write_export(runs_dir: Path, run: dict[str, Any], p: Published) -> list[str]:
    """Check the export in a scratch copy, then write it. Returns the run ids the folder now verifies."""
    files = run_files(run, p)
    problems = text_problems(files)
    if problems:
        raise ExportError("sealed text the site cannot serve: " + "; ".join(problems))
    if (runs_dir / run["run_id"]).exists() or (runs_dir / f"{run['run_id']}.json").exists():
        raise ExportError(f"{run['run_id']} already exists in {runs_dir}; a recorded run is never overwritten")
    index_bytes = _index_with(runs_dir, run)
    with tempfile.TemporaryDirectory() as scratch:
        trial = Path(scratch) / "runs"
        if runs_dir.is_dir():
            shutil.copytree(runs_dir, trial)
        else:
            trial.mkdir()
        _write_files(trial, files, index_bytes)
        try:
            webrun.verify_run_dir(trial)
        except ValueError as exc:
            raise ExportError(f"the export does not verify: {exc}") from exc
    runs_dir.mkdir(parents=True, exist_ok=True)
    _write_files(runs_dir, files, index_bytes)
    return webrun.verify_run_dir(runs_dir)


def _write_files(root: Path, files: Mapping[str, bytes], index_bytes: bytes) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / webrun.INDEX_NAME).write_bytes(index_bytes)


def rewrite_readiness(config_path: Path, run: dict[str, Any]) -> list[str]:
    """Point the live gate at the run the page now shows by default. Returns the new label markers.

    The three anchored label markers of the demo target become page_label_markers(run), the strings
    ci/web_check.py requires on the page and the console renders; the demo-recorded targets check
    the run file and its sealed manifest on the live host. Every other marker and target stays.
    """
    web_check = _ci_module("web_check")
    text = config_path.read_text(encoding="utf-8")
    config = json.loads(text)
    targets = config.get("targets", [])
    demo = next((t for t in targets if t.get("name") == "demo"), None)
    if demo is None:
        raise ExportError(f"{config_path} has no demo target")
    labels = [marker for _, marker in web_check.page_label_markers(run)]
    demo["markers"] = [m for m in demo["markers"] if not m.startswith(LABEL_PREFIXES)] + labels
    kept = [t for t in targets if t.get("name") not in RECORDED_TARGETS]
    if run.get("provenance") == "design_preview":
        # back to the hand-written sample: demo-run and demo-bundle already cover it
        config["targets"] = kept
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return labels
    run_id, sha = run["run_id"], run["bundle"]["sha256"]
    approval = run.get("approval") or {}
    if approval.get("status") != "approved" or approval.get("approved_bundle_sha256") != sha:
        raise ExportError(f"{run_id} is not approved for its own bundle hash, so the live gate cannot hold its approved label")
    recorded = [
        {
            "name": "demo-recorded",
            "url": f"{HOST}/runs/{run_id}.json",
            "markers": [f'"run_id":"{run_id}"', '"provenance":"pipeline"', run["label"], f'"approved_bundle_sha256":"{sha}"'],
            "scan_assets": False,
        },
        {
            "name": "demo-recorded-bundle",
            "url": f"{HOST}/runs/{run_id}/{MANIFEST}",
            "markers": ['"schema":"agon.bundle/1"', '"report-en.json":'],
            "scan_assets": False,
        },
    ]
    at = next((i + 1 for i, t in enumerate(kept) if t.get("name") == "demo-bundle"), len(kept))
    config["targets"] = kept[:at] + recorded + kept[at:]
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return labels


# ----------------------------------------------------------------- command line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agon.webrecord", description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--feed-base-url", help="the public feed container URL, the deploy output feedBaseUrl")
    source.add_argument("--local", type=Path, help="a copy of the container on disk")
    source.add_argument("--readiness-only", type=Path, metavar="RUN_JSON", help="only rewrite --readiness for this run file")
    parser.add_argument("--bundle", help="the published bundle sha256")
    parser.add_argument("--target", default="feed", choices=TARGETS)
    parser.add_argument("--commit", help="the 40-character commit sha of the fleet image that produced the run")
    parser.add_argument("--approver-role", help="the verifier role that approved, for example approver")
    parser.add_argument("--channel", choices=CHANNELS, help="the channel the approval came through")
    parser.add_argument("--approved-at", help="the approval time, ISO 8601 with a zone; required when no public file carries it")
    parser.add_argument("--run-id", help="the run id (default run-<first 12 hex of the bundle>)")
    parser.add_argument("--out", type=Path, default=Path("web/runs"), help="the runs folder (default web/runs)")
    parser.add_argument("--readiness", type=Path, help="rewrite this readiness config for the new default run")
    args = parser.parse_args(argv)
    try:
        if args.readiness_only is not None:
            if args.readiness is None:
                raise ExportError("--readiness-only needs --readiness")
            run = json.loads(args.readiness_only.read_text(encoding="utf-8"))
            labels = rewrite_readiness(args.readiness, run)
            sys.stdout.write("webrecord: readiness markers now " + "; ".join(labels) + "\n")
            return 0
        missing = [flag for flag, value in (("--bundle", args.bundle), ("--commit", args.commit),
                                           ("--approver-role", args.approver_role), ("--channel", args.channel)) if not value]
        if missing:
            raise ExportError("missing " + ", ".join(missing))
        feed: Feed = LocalFeed(args.local) if args.local is not None else HttpFeed(args.feed_base_url)
        published = read_published(feed, args.bundle, args.target)
        approved_at = approval_time(published, args.approved_at)
        run = build_run(published, commit=args.commit, approver_role=args.approver_role, channel=args.channel,
                        approved_at=approved_at, run_id=args.run_id)
        ids = write_export(args.out, run, published)
        sys.stdout.write(f"webrecord: wrote {run['run_id']} ({run['label']}), bundle {published.sha}; "
                         f"{args.out.as_posix()} verifies {len(ids)} runs: {', '.join(ids)}\n")
        if args.readiness is not None:
            labels = rewrite_readiness(args.readiness, run)
            sys.stdout.write("webrecord: readiness markers now " + "; ".join(labels) + "\n")
    except ExportError as exc:
        sys.stdout.write(f"webrecord: FAIL: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
