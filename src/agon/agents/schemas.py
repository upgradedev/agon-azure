"""Output schemas for every model role, and the stdlib parsers that enforce them.

The JSON schemas are written for strict structured output (every property required, nullable
fields as a null type, no additional properties), so the same dict can be sent as a Foundry
`response_format` and published into a prompt agent's text format. The parsers below never trust
the model: they re-check every field by hand and raise `ModelOutputError` on the first problem, so
a fake and a real model meet exactly the same bar.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agon.agents.ports import ModelOutputError
from agon.governance.claims import CLAIM_TYPES, Claim, ClaimModelError, Sentence

THEMES: tuple[str, ...] = ("control_vs_chaos", "tactical_pressure", "game_rhythm")
"""The rules' three terms for a turning point: "control vs. chaos, tactical pressure changes, and game rhythm"."""

TRIAGE_TYPES: tuple[str, ...] = (
    "match_stat",
    "injury_medical",
    "disciplinary_referee",
    "quote_attribution",
    "transfer_contract",
    "neutral_colour",
)

DESK_FLAG_CODES: tuple[str, ...] = (
    "unsupported_emphasis",
    "ambiguous_attribution",
    "possible_misread",
    "tone",
    "other",
)

MAX_SENTENCES = 40
MAX_TEXT_CHARS = 600
MAX_DETAIL_CHARS = 300

_NULLABLE_STRING = {"type": ["string", "null"]}
_STRING_LIST = {"type": "array", "items": {"type": "string"}}

CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "value", "event_ids", "stat", "subject"],
    "properties": {
        "type": {"type": "string", "enum": list(CLAIM_TYPES)},
        "value": {
            "anyOf": [
                {"type": "integer"},
                {"type": "string"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["home", "away"],
                    "properties": {"home": {"type": "integer"}, "away": {"type": "integer"}},
                },
            ]
        },
        "event_ids": _STRING_LIST,
        "stat": _NULLABLE_STRING,
        "subject": _NULLABLE_STRING,
    },
}


def _envelope(name: str, list_key: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [list_key],
                "properties": {list_key: {"type": "array", "items": item}},
            },
        },
    }


SCHEMAS: dict[str, dict[str, Any]] = {
    "analyst": _envelope(
        "agon_turning_points",
        "turning_points",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["event_id", "why", "theme"],
            "properties": {
                "event_id": {"type": "string"},
                "why": {"type": "string"},
                "theme": {"type": "string", "enum": list(THEMES)},
            },
        },
    ),
    "writer": _envelope(
        "agon_report_sentences",
        "sentences",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["sentence_id", "text", "claims"],
            "properties": {
                "sentence_id": {"type": "string"},
                "text": {"type": "string"},
                "claims": {"type": "array", "items": CLAIM_SCHEMA},
            },
        },
    ),
    "localiser_editor": _envelope(
        "agon_post_edit",
        "sentences",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["sentence_id", "text"],
            "properties": {"sentence_id": {"type": "string"}, "text": {"type": "string"}},
        },
    ),
    "triage": _envelope(
        "agon_triage",
        "items",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["sentence_id", "type", "grounding_ids"],
            "properties": {
                "sentence_id": {"type": "string"},
                "type": {"type": "string", "enum": list(TRIAGE_TYPES)},
                "grounding_ids": _STRING_LIST,
            },
        },
    ),
    "desk_editor": _envelope(
        "agon_desk_flags",
        "flags",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["sentence_id", "code", "detail"],
            "properties": {
                "sentence_id": {"type": "string"},
                "code": {"type": "string", "enum": list(DESK_FLAG_CODES)},
                "detail": {"type": "string"},
            },
        },
    ),
}


# ----------------------------------------------------------------- parsing helpers


def load_object(text: str) -> dict[str, Any]:
    """Parse the model's text as one JSON object. A fenced code block is not accepted."""
    if not isinstance(text, str) or not text.strip():
        raise ModelOutputError("empty answer")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"answer is not JSON: {exc.msg} at {exc.pos}") from exc
    if not isinstance(value, dict):
        raise ModelOutputError("answer is not a JSON object")
    return value


def _items(obj: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if set(obj) != {key}:
        raise ModelOutputError(f"answer must hold exactly the key {key!r}, got {sorted(obj)}")
    items = obj[key]
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ModelOutputError(f"{key} must be a list of objects")
    if len(items) > MAX_SENTENCES:
        raise ModelOutputError(f"{key} has {len(items)} items, the cap is {MAX_SENTENCES}")
    return items


def _exact_keys(item: dict[str, Any], keys: set[str], where: str) -> None:
    if set(item) != keys:
        raise ModelOutputError(f"{where} must hold exactly {sorted(keys)}, got {sorted(item)}")


def _text(item: dict[str, Any], key: str, where: str, cap: int = MAX_TEXT_CHARS) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelOutputError(f"{where}.{key} must be a non-empty string")
    if len(value) > cap:
        raise ModelOutputError(f"{where}.{key} is longer than {cap} characters")
    return value.strip()


def _string_list(item: dict[str, Any], key: str, where: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ModelOutputError(f"{where}.{key} must be a list of strings")
    return list(value)


_SENTENCE_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def _sentence_id(item: dict[str, Any], where: str) -> str:
    """Sentence ids become artifact keys and edition suffixes, so they are short and plain."""
    value = item.get("sentence_id")
    if not isinstance(value, str) or not _SENTENCE_ID.match(value):
        raise ModelOutputError(f"{where}.sentence_id must match {_SENTENCE_ID.pattern}")
    return value


def _unique_ids(ids: list[str], where: str) -> None:
    if len(set(ids)) != len(ids):
        raise ModelOutputError(f"{where} repeats a sentence_id")


# ----------------------------------------------------------------- per-role parsers


def parse_analyst(text: str) -> list[dict[str, str]]:
    out = []
    for n, item in enumerate(_items(load_object(text), "turning_points")):
        where = f"turning_points[{n}]"
        _exact_keys(item, {"event_id", "why", "theme"}, where)
        theme = item.get("theme")
        if theme not in THEMES:
            raise ModelOutputError(f"{where}.theme {theme!r} is not one of {list(THEMES)}")
        out.append({"event_id": _text(item, "event_id", where, 64), "why": _text(item, "why", where), "theme": theme})
    return out


def _claim(data: Any, where: str) -> Claim:
    if not isinstance(data, dict):
        raise ModelOutputError(f"{where} must be an object")
    _exact_keys(data, {"type", "value", "event_ids", "stat", "subject"}, where)
    if not isinstance(data["event_ids"], list) or not all(isinstance(e, str) for e in data["event_ids"]):
        raise ModelOutputError(f"{where}.event_ids must be a list of strings")
    for key in ("stat", "subject"):
        if data[key] is not None and not isinstance(data[key], str):
            raise ModelOutputError(f"{where}.{key} must be a string or null")
    try:
        return Claim(
            type=data["type"],
            value=data["value"],
            event_ids=tuple(data["event_ids"]),
            stat=data["stat"],
            subject=data["subject"],
        )
    except ClaimModelError as exc:
        raise ModelOutputError(f"{where}: {exc}") from exc


def parse_writer(text: str, author: str = "model") -> list[Sentence]:
    sentences = []
    for n, item in enumerate(_items(load_object(text), "sentences")):
        where = f"sentences[{n}]"
        _exact_keys(item, {"sentence_id", "text", "claims"}, where)
        if not isinstance(item["claims"], list):
            raise ModelOutputError(f"{where}.claims must be a list")
        claims = tuple(_claim(c, f"{where}.claims[{k}]") for k, c in enumerate(item["claims"]))
        try:
            sentences.append(
                Sentence(
                    sentence_id=_sentence_id(item, where),
                    text=_text(item, "text", where),
                    language="en",
                    claims=claims,
                    author=author,
                )
            )
        except ClaimModelError as exc:
            raise ModelOutputError(f"{where}: {exc}") from exc
    _unique_ids([s.sentence_id for s in sentences], "sentences")
    return sentences


def parse_post_edit(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, item in enumerate(_items(load_object(text), "sentences")):
        where = f"sentences[{n}]"
        _exact_keys(item, {"sentence_id", "text"}, where)
        sid = _sentence_id(item, where)
        if sid in out:
            raise ModelOutputError("sentences repeats a sentence_id")
        out[sid] = _text(item, "text", where)
    return out


def parse_triage(text: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for n, item in enumerate(_items(load_object(text), "items")):
        where = f"items[{n}]"
        _exact_keys(item, {"sentence_id", "type", "grounding_ids"}, where)
        sid = _sentence_id(item, where)
        if sid in out:
            raise ModelOutputError("items repeats a sentence_id")
        kind = item.get("type")
        if kind not in TRIAGE_TYPES:
            raise ModelOutputError(f"{where}.type {kind!r} is not one of {list(TRIAGE_TYPES)}")
        out[sid] = {"type": kind, "grounding_ids": _string_list(item, "grounding_ids", where)}
    return out


def parse_desk_flags(text: str) -> list[dict[str, str]]:
    out = []
    for n, item in enumerate(_items(load_object(text), "flags")):
        where = f"flags[{n}]"
        _exact_keys(item, {"sentence_id", "code", "detail"}, where)
        code = item.get("code")
        if code not in DESK_FLAG_CODES:
            raise ModelOutputError(f"{where}.code {code!r} is not one of {list(DESK_FLAG_CODES)}")
        out.append(
            {
                "sentence_id": _sentence_id(item, where),
                "code": code,
                "detail": _text(item, "detail", where, MAX_DETAIL_CHARS),
            }
        )
    return out
