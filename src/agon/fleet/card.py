"""The approval card payloads the fleet worker sends to the Logic App triggers, and the poster.

`la-agon-teams-approval` (infra/flows.bicep) takes, in its HTTP trigger schema:

    run_id, bundle_sha256, nonce, title, match_line, expires_at, editions, attested, gate_cleared,
    confirm_rows [{id, text}], destinations [{id, label}], evidence_url, published_url,
    verify_url, parent_message_id, erratum_note, report, report_label, flags, footer

and requires run_id, bundle_sha256, nonce, title and published_url. `la-agon-outlook-approval`
takes run_id, bundle_sha256, nonce, subject, summary, confirm_rows and verify_url. The key sets
below are checked against the Bicep source by tests/fleet/test_fleet_card.py.

The Match-Day Charter card (`charter_teams_payload`) goes to the same Teams flow: its own title,
the plain summary of the cue rules as `report` under its own `report_label`, a `footer` that says
no AI model wrote it, no Confirm rows and no destinations (the flow then says it publishes to the
club feed only). `report_label` and `footer` are optional; without them the flow prints the
full-time card's own wording, so a full-time payload never sends them.

Every value comes from the sealed bundle: the manifest registered in the store under its own
hash (the bytes are re-hashed here), the `report-en.json` artifact it names (for the text of each
Confirm row), and the synthetic match whose event-log hash the manifest seals (fictional club
names and the score). A match record whose hash differs from the manifest is refused.

`published_url` is where the Publisher writes this bundle's manifest. `Publisher.publish`
(agon.governance.publisher) writes ``<target>/<bundle_sha256>/manifest.json``; this module reads
the first segment as the public container of the same name, so for target feed the URL is
``<feed container URL><bundle_sha256>/manifest.json``. The flow polls that URL anonymously and
updates the card to "Published" once it answers 200.

What the card carries and what it never carries: the nonce is on the card by design (spec 7.2):
it is the single-use capability the officer's tap sends back, and the verifier consumes it once.
No access token, no SAS signature, no key and no Logic App URL is ever part of a payload, and
`FlowPoster` refuses a trigger URL that carries SAS parameters at all: it calls the trigger base
URL with ``api-version`` only and authenticates with a Microsoft Entra token of id-fleet, which
the trigger's access-control policy checks on issuer and appid.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agon.fleet.contracts import ApprovalRequest
from agon.fleet.sources import MatchRecord
from agon.governance import seal as seal_mod
from agon.governance import verifier
from agon.governance.recompute import score_after

TEAMS_KEYS: frozenset[str] = frozenset({
    "run_id", "bundle_sha256", "nonce", "title", "match_line", "expires_at", "editions", "attested",
    "gate_cleared", "confirm_rows", "destinations", "evidence_url", "published_url", "verify_url",
    "parent_message_id", "erratum_note", "report", "flags", "report_label", "footer",
})
TEAMS_REQUIRED: frozenset[str] = frozenset({"run_id", "bundle_sha256", "nonce", "title", "published_url"})
OUTLOOK_KEYS: frozenset[str] = frozenset({"run_id", "bundle_sha256", "nonce", "subject", "summary", "confirm_rows", "verify_url", "report"})
OUTLOOK_REQUIRED: frozenset[str] = frozenset({"run_id", "bundle_sha256", "nonce", "subject"})

# Teams refuses a message over 28 KB (spec 3.4 step 4); the card template adds its own text.
MAX_PAYLOAD_BYTES = 20 * 1024
ROW_TEXT_MAX = 400
ROW_TEXT_MIN = 80
EDITION_ORDER: tuple[str, ...] = ("en", "el", "es", "fr", "easy")
EDITION_LABELS: dict[str, str] = {
    "en": "English", "el": "Greek", "es": "Spanish", "fr": "French", "easy": "English easy read",
}
SAS_PARAMETERS: frozenset[str] = frozenset({"sp", "sv", "sig", "se", "st", "sr", "skoid", "sktid"})
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")


class CardError(ValueError):
    """The card cannot be built from this bundle, or a payload breaks the card contract."""


class FlowPostError(RuntimeError):
    """The Logic App trigger did not accept the payload. Never carries the payload or a token."""


@dataclass(frozen=True)
class CardContext:
    """Public URLs the card links to. None of them is a secret."""

    feed_base_url: str
    sandbox_base_url: str = ""
    site_url: str = ""

    def container_url(self, target: str) -> str:
        base = self.feed_base_url if target == "feed" else self.sandbox_base_url
        if not base.startswith("https://"):
            raise CardError(f"no https base URL configured for the {target} container")
        return base if base.endswith("/") else base + "/"


def published_url(context: CardContext, target: str, bundle_sha256: str) -> str:
    return f"{context.container_url(target)}{bundle_sha256}/{seal_mod.MANIFEST_NAME}"


@dataclass(frozen=True)
class CardInputs:
    manifest: dict[str, Any]
    record: MatchRecord
    report_en: dict[str, Any] | None
    card: dict[str, Any] | None = None


def load_inputs(store: Any, artifacts: Any, record: MatchRecord, bundle_sha256: str) -> CardInputs:
    """Read the sealed manifest by its hash and check it against the bytes and the match record."""
    doc = store.read(verifier.BUNDLES, bundle_sha256)
    if doc is None:
        raise CardError("the sealed manifest is not registered in the store")
    data = str(doc.body.get("manifest", "")).encode("utf-8")
    check = seal_mod.verify_manifest(data, expected_sha256=bundle_sha256)
    if not check.ok or check.manifest is None:
        raise CardError("; ".join(check.reasons) or "the sealed manifest does not verify")
    manifest = check.manifest
    if manifest["event_log_sha256"] != record.event_log_sha256 or manifest["match_id"] != record.match_id:
        raise CardError("the match record is not the one this bundle sealed")
    report_en = None
    entry = manifest["artifacts"].get("report-en.json")
    if entry is not None:
        raw = artifacts.get(entry["sha256"])
        if raw is None:
            raise CardError("report-en.json is missing from the artifact store")
        report_en = json.loads(raw.decode("utf-8"))
    card = None
    card_entry = manifest["artifacts"].get("card.json")
    if card_entry is not None:
        raw_card = artifacts.get(card_entry["sha256"])
        if raw_card is None:
            raise CardError("card.json is missing from the artifact store")
        card = json.loads(raw_card.decode("utf-8"))
    return CardInputs(manifest=manifest, record=record, report_en=report_en, card=card)


def _score_line(record: MatchRecord) -> tuple[str, str]:
    facts = record.facts()
    score = score_after(facts, None)
    home = record.roster.clubs[facts.home_id]
    away = record.roster.clubs[facts.away_id]
    return f"{home} {score['home']}-{score['away']} {away}", record.roster.league_name


def _editions(manifest: Mapping[str, Any]) -> list[str]:
    names = manifest["artifacts"]
    return [EDITION_LABELS[e] for e in EDITION_ORDER if f"report-{e}.json" in names]


def _gate_cleared_text(manifest: Mapping[str, Any]) -> str:
    cleared = list(manifest["attested_scope"]["gate_cleared"])
    if not cleared:
        return "none"
    editions = [EDITION_LABELS[e] for e in EDITION_ORDER if f"report-{e}.json" in cleared]
    audio = sum(1 for n in cleared if n.startswith("audio-"))
    captions = sum(1 for n in cleared if n.startswith("captions-"))
    parts = []
    if editions:
        parts.append(f"the {', '.join(editions)} editions")
    if audio:
        parts.append(f"{audio} audio files")
    if captions:
        parts.append(f"{captions} caption files")
    if "card.json" in cleared:
        parts.append("the card summary")
    other = len(cleared) - len([n for n in cleared if n.startswith(("report-", "audio-", "captions-")) or n == "card.json"])
    if other:
        parts.append(f"{other} other files")
    return "; ".join(parts) + ". Checked by the Fact Gate and the Rights Guard, sealed in the same hash."


def _confirm_rows(request: ApprovalRequest, report_en: Mapping[str, Any] | None, limit: int) -> list[dict[str, str]]:
    texts = {}
    if report_en is not None:
        texts = {row.get("sentence_id"): row.get("text", "") for row in report_en.get("sentences", [])}
    rows = []
    for sid in request.confirm_rows:
        text = str(texts.get(sid) or f"Sentence {sid} (text not found in the English report)")
        if len(text) > limit:
            text = text[: limit - 3].rstrip() + "..."
        rows.append({"id": sid, "text": text})
    return rows


# The headline report the officer attests, printed on the card and in the email (flows.bicep
# `report`): at most this many characters per Confirm-row character budget, so _fit shrinks both.
REPORT_CHARS_PER_ROW_CHAR = 20


def report_text(report_en: Mapping[str, Any] | None, limit: int) -> str | None:
    """The English headline report as plain text, sentences in order; None when it is unavailable.

    Truncation is stated in the text itself, never silent, so the officer knows the card is short.
    """
    if report_en is None:
        return None
    sentences = [str(row.get("text", "")).strip() for row in report_en.get("sentences", [])]
    text = " ".join(s for s in sentences if s)
    if not text:
        return None
    if len(text) > limit:
        text = text[: limit].rstrip() + " [shortened to fit the card; the full report is in the evidence link]"
    return text


FLAG_LINES_MAX = 8


def flag_lines(card: Mapping[str, Any] | None, limit: int) -> list[str]:
    """The sealed flags (gates, Desk Editor, fallbacks) as plain lines for the card.

    A flag never becomes a Confirm row and never changes what the officer must tick; it is shown
    so the officer reads it before approving. More than FLAG_LINES_MAX flags end with a line that
    says how many more are in the bundle, never silently.
    """
    if card is None:
        return []
    lines: list[str] = []
    flags = [f for f in card.get("flags", []) if isinstance(f, Mapping)]
    for flag in flags[:FLAG_LINES_MAX]:
        who = str(flag.get("by") or "check")
        what = str(flag.get("detail") or flag.get("code") or "flag")
        sentence = flag.get("sentence_id")
        line = f"{who}{' on ' + str(sentence) if sentence else ''}: {what}"
        if len(line) > limit:
            line = line[: limit - 3].rstrip() + "..."
        lines.append(line)
    if len(flags) > FLAG_LINES_MAX:
        lines.append(f"{len(flags) - FLAG_LINES_MAX} more flags are in the sealed card.json.")
    return lines


def _destinations(request: ApprovalRequest, manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """The destinations this bundle offers, as the card's {id, label} toggles; [] means feed only.

    Lane C6 seals the offered destinations into the manifest and adds them to the ApprovalRequest;
    the officer ticks a subset on the card and the verifier refuses anything outside the offer. The
    request's list wins; otherwise the manifest's `destinations` (a list of ids or {id, label}, or
    a table keyed by id) is read. Neither present: no toggles, and the flow says feed only.
    """
    offered: Any = getattr(request, "destinations", None) or manifest.get("destinations")
    if isinstance(offered, Mapping):
        offered = [{"id": key, **(value if isinstance(value, Mapping) else {})} for key, value in sorted(offered.items())]
    if not isinstance(offered, list):
        return []
    out = []
    for item in offered:
        if isinstance(item, str):
            out.append({"id": item, "label": item.replace("_", " ").capitalize()})
        elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
            out.append({"id": item["id"], "label": str(item.get("label") or item["id"].replace("_", " ").capitalize())})
    return out


def _fit(payload: dict[str, Any], rebuild: Callable[[int], dict[str, Any]]) -> dict[str, Any]:
    limit = ROW_TEXT_MAX
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        if limit <= ROW_TEXT_MIN:
            raise CardError(f"the card payload is over {MAX_PAYLOAD_BYTES} bytes even with short Confirm rows")
        limit = max(ROW_TEXT_MIN, limit // 2)
        payload = rebuild(limit)
    return payload


def teams_payload(request: ApprovalRequest, inputs: CardInputs, match_ref: Mapping[str, Any], context: CardContext) -> dict[str, Any]:
    score_line, league = _score_line(inputs.record)
    rows_count = len(request.confirm_rows)
    attested = "The English headline report" + (
        f" and the {rows_count} flagged sentence{'s' if rows_count != 1 else ''} you tick below." if rows_count else "."
    )

    def build(limit: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": request.run_id,
            "bundle_sha256": request.bundle_sha256,
            "nonce": request.nonce,
            "title": f"Full-time report ready: {score_line}",
            "match_line": (
                f"{league}, synthetic match (seed {match_ref.get('seed')}, match {match_ref.get('match_index')}). "
                f"Final score {score_line}."
            ),
            "expires_at": request.expiry,
            "editions": _editions(inputs.manifest),
            "attested": attested,
            "gate_cleared": _gate_cleared_text(inputs.manifest),
            "confirm_rows": _confirm_rows(request, inputs.report_en, limit),
            "destinations": _destinations(request, inputs.manifest),
            "published_url": published_url(context, request.target, request.bundle_sha256),
        }
        report = report_text(inputs.report_en, limit * REPORT_CHARS_PER_ROW_CHAR)
        if report is not None:
            payload["report"] = report
        flags = flag_lines(inputs.card, limit)
        if flags:
            payload["flags"] = flags
        if context.site_url.startswith("https://"):
            payload["evidence_url"] = context.site_url.rstrip("/") + "/#/newsroom"
        return payload

    payload = _fit(build(ROW_TEXT_MAX), build)
    check_payload(payload, TEAMS_KEYS, TEAMS_REQUIRED)
    return payload


def outlook_payload(request: ApprovalRequest, inputs: CardInputs, match_ref: Mapping[str, Any], context: CardContext) -> dict[str, Any]:
    score_line, league = _score_line(inputs.record)

    def build(limit: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": request.run_id,
            "bundle_sha256": request.bundle_sha256,
            "nonce": request.nonce,
            "subject": f"Approve the full-time report: {score_line}",
            "summary": (
                f"{league}, synthetic match (seed {match_ref.get('seed')}, match {match_ref.get('match_index')}). "
                f"Final score {score_line}. Editions: {', '.join(_editions(inputs.manifest))}. "
                f"You attest the English headline report and the flagged sentences below. "
                f"Approve before {request.expiry}. The same card is in the Teams approval channel; "
                f"the first decision on either counts."
            ),
            "confirm_rows": _confirm_rows(request, inputs.report_en, limit),
            "verify_url": published_url(context, request.target, request.bundle_sha256),
        }
        report = report_text(inputs.report_en, limit * REPORT_CHARS_PER_ROW_CHAR)
        if report is not None:
            payload["report"] = report
        return payload

    payload = _fit(build(ROW_TEXT_MAX), build)
    check_payload(payload, OUTLOOK_KEYS, OUTLOOK_REQUIRED)
    return payload


# ----------------------------------------------------------------- the Match-Day Charter card


CHARTER_REPORT_LABEL = "The live cue rules, exactly as sealed (the Greek, Spanish and French templates are in charter.json):"
CHARTER_FOOTER = (
    "Built by code from the fictional fixture; no AI model wrote these rules. Live cues fill only these "
    "templates and carry this hash. Nothing is published until you approve this exact hash."
)


@dataclass(frozen=True)
class CharterInputs:
    manifest: dict[str, Any]
    record: MatchRecord
    charter: dict[str, Any]


def load_charter_inputs(store: Any, artifacts: Any, record: MatchRecord, bundle_sha256: str) -> CharterInputs:
    """Read a sealed charter bundle by its hash and check it against the bytes and the fixture."""
    from agon.charter import ARTIFACT_NAME, EMPTY_EVENT_LOG_SHA256

    doc = store.read(verifier.BUNDLES, bundle_sha256)
    if doc is None:
        raise CardError("the sealed manifest is not registered in the store")
    check = seal_mod.verify_manifest(str(doc.body.get("manifest", "")).encode("utf-8"), expected_sha256=bundle_sha256)
    if not check.ok or check.manifest is None:
        raise CardError("; ".join(check.reasons) or "the sealed manifest does not verify")
    manifest = check.manifest
    if manifest["kind"] != "charter":
        raise CardError(f"bundle {bundle_sha256} is a {manifest['kind']} bundle, not a charter")
    if manifest["match_id"] != record.match_id or manifest["seed_sha256"] != record.seed_sha256:
        raise CardError("the fixture is not the one this charter was sealed for")
    if manifest["event_log_sha256"] != EMPTY_EVENT_LOG_SHA256:
        raise CardError("a charter is sealed before kickoff, over the empty event log")
    raw = artifacts.get(manifest["artifacts"][ARTIFACT_NAME]["sha256"])
    if raw is None:
        raise CardError(f"{ARTIFACT_NAME} is missing from the artifact store")
    charter = json.loads(raw.decode("utf-8"))
    if charter.get("match_id") != record.match_id:
        raise CardError("charter.json names a different match")
    return CharterInputs(manifest=manifest, record=record, charter=charter)


def charter_teams_payload(
    request: ApprovalRequest, inputs: CharterInputs, match_ref: Mapping[str, Any], context: CardContext
) -> dict[str, Any]:
    """The Teams card for a Match-Day Charter: the rules, the scope line, no rows, no destinations."""
    from agon.charter import LANGUAGE_NAMES, LANGUAGES, SCOPE_LINE, summary

    charter = inputs.charter
    fixture = charter["fixture"]
    home, away = fixture["home"]["name"], fixture["away"]["name"]
    templates = len(charter["templates"])
    held = len(charter["held_classes"])
    languages = [LANGUAGE_NAMES[lang] for lang in LANGUAGES]

    def build(limit: int) -> dict[str, Any]:
        text = summary(charter)
        budget = limit * REPORT_CHARS_PER_ROW_CHAR
        if len(text) > budget:
            text = text[:budget].rstrip() + " [shortened to fit the card; the full rules are in charter.json]"
        payload: dict[str, Any] = {
            "run_id": request.run_id,
            "bundle_sha256": request.bundle_sha256,
            "nonce": request.nonce,
            "title": f"Match-Day Charter: {home} v {away}, approve the live cue rules",
            "match_line": (
                f"{fixture['league']}, synthetic match (seed {match_ref.get('seed')}, match {match_ref.get('match_index')}), "
                f"before kickoff. {SCOPE_LINE}"
            ),
            "expires_at": request.expiry,
            "editions": languages,
            "attested": (
                f"The live cue rules below: {templates} cue templates in {', '.join(languages[:-1])} and {languages[-1]}, "
                f"their thresholds, the {held} held claim classes and the voices. {SCOPE_LINE}"
            ),
            "report": text,
            "report_label": CHARTER_REPORT_LABEL,
            "gate_cleared": "none. The charter_check gate and the Rights Guard passed on every template in every language.",
            "confirm_rows": [],
            "destinations": [],
            "published_url": published_url(context, request.target, request.bundle_sha256),
            "footer": CHARTER_FOOTER,
        }
        if context.site_url.startswith("https://"):
            payload["evidence_url"] = context.site_url.rstrip("/") + "/#/newsroom"
        return payload

    payload = _fit(build(ROW_TEXT_MAX), build)
    check_payload(payload, TEAMS_KEYS, TEAMS_REQUIRED)
    return payload


def check_payload(payload: Mapping[str, Any], allowed: frozenset[str], required: frozenset[str]) -> None:
    """The trigger schema's keys, and no secret-shaped value anywhere in the payload."""
    unknown = sorted(set(payload) - allowed)
    missing = sorted(k for k in required if not payload.get(k))
    if unknown or missing:
        raise CardError(f"payload keys differ from the trigger schema: unknown {unknown}, missing {missing}")
    for problem in secret_problems(payload):
        raise CardError(problem)


def secret_problems(value: Any, path: str = "$") -> list[str]:
    """Bearer tokens, SAS parameters and key-shaped fields. The nonce field is allowed by design."""
    out: list[str] = []
    if isinstance(value, Mapping):
        for k, v in value.items():
            if str(k).lower() in {"authorization", "token", "access_token", "key", "sig", "password", "secret"}:
                out.append(f"{path}.{k} is a credential-shaped field")
            if path == "$" and k == "nonce":
                continue  # the single-use capability the card exists to carry (spec 7.2)
            out += secret_problems(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += secret_problems(v, f"{path}[{i}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if "bearer " in lowered or _JWT.search(value):
            out.append(f"{path} looks like a bearer token")
        if lowered.startswith(("http://", "https://")):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(value).query)
            if SAS_PARAMETERS & {q.lower() for q in query}:
                out.append(f"{path} is a URL with SAS parameters")
    return out


# ----------------------------------------------------------------- the poster


def validate_flow_url(url: str) -> str:
    """A Logic App trigger base URL with api-version and nothing else (never the SAS sp, sv, sig)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("the flow URL must be https")
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    sas = sorted(SAS_PARAMETERS & {k.lower() for k in query})
    if sas:
        raise ValueError(f"the flow URL carries SAS parameters {sas}; use the base path with api-version only")
    if set(query) != {"api-version"} or len(query["api-version"]) != 1 or not query["api-version"][0]:
        raise ValueError("the flow URL must carry exactly one query parameter, api-version")
    if parts.fragment:
        raise ValueError("the flow URL must not carry a fragment")
    return url


def url_fingerprint(url: str) -> str:
    """What a log line may say about a flow URL: its host and a short hash, never the path."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.netloc}#{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"


class FlowPoster:
    """POST a payload to one Logic App trigger with an Entra bearer token (id-fleet, ARM audience)."""

    def __init__(
        self,
        name: str,
        url: str,
        token: Callable[[], str],
        *,
        timeout: float = 30.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.name = name
        self.url = validate_flow_url(url)
        self._token = token
        self.timeout = timeout
        self._open = opener

    def build_request(self, payload: Mapping[str, Any]) -> urllib.request.Request:
        return urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=utf-8"},
        )

    def post(self, payload: Mapping[str, Any]) -> int:
        try:
            request = self.build_request(payload)
            with self._open(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 0) or response.getcode())
        except urllib.error.HTTPError as exc:
            raise FlowPostError(f"{self.name} flow answered HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - the message never carries the payload or the token
            raise FlowPostError(f"{self.name} flow not reached: {type(exc).__name__}") from None
        if not 200 <= status < 300:
            raise FlowPostError(f"{self.name} flow answered HTTP {status}")
        return status
