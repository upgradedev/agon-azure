"""The sentence and claim model every edition is written in.

A sentence is prose plus the machine-checkable claims it makes. The prose can be in any of the
supported languages; the claims are language-neutral objects, so the fact gate checks the same
claims in English, Greek, Spanish and French and never parses the meaning of the prose.

Claim types and the shape of `value`:

* ``score``: ``{"home": int, "away": int}``, always home first. With no `event_ids` it is the
  final score; with `event_ids` it is the score immediately after the latest cited event.
* ``stat``: an int count, with `stat` naming the statistic (see `recompute.STAT_KEYS`) and
  `subject` naming a club id or a player id.
* ``minute``: the football display minute as a string: ``"71"``, ``"45+2"`` or ``"90+3"``.
  Every cited event must sit at exactly that display minute (see `recompute.display_minute`).
* ``name``: the display name of a player, club or the league, exactly as on the roster. `subject`
  may pin the id. Cited events must involve that entity.
* ``event_ref``: a kind from `recompute.EVENT_KINDS` (``goal``, ``red_card``, ...). Every cited
  event must be of that kind.

Author tags say who wrote the words: ``model`` (a Foundry agent), ``template`` (a deterministic
fallback line) or ``officer`` (edited by the club media officer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CLAIM_TYPES: tuple[str, ...] = ("score", "stat", "minute", "name", "event_ref")
AUTHOR_TAGS: tuple[str, ...] = ("model", "template", "officer")
LANGUAGES: tuple[str, ...] = ("en", "el", "es", "fr")


class ClaimModelError(ValueError):
    """Raised when a sentence or claim cannot be built from the given data."""


@dataclass(frozen=True)
class Claim:
    type: str
    value: Any
    event_ids: tuple[str, ...] = ()
    stat: str | None = None
    subject: str | None = None

    def __post_init__(self) -> None:
        if self.type not in CLAIM_TYPES:
            raise ClaimModelError(f"unknown claim type {self.type!r}")
        if not isinstance(self.event_ids, tuple) or not all(isinstance(e, str) for e in self.event_ids):
            raise ClaimModelError("event_ids must be a tuple of strings")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "value": self.value, "event_ids": list(self.event_ids)}
        if self.stat is not None:
            out["stat"] = self.stat
        if self.subject is not None:
            out["subject"] = self.subject
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        allowed = {"type", "value", "event_ids", "stat", "subject"}
        extra = set(data) - allowed
        if extra:
            raise ClaimModelError(f"unexpected claim fields {sorted(extra)}")
        if "type" not in data or "value" not in data:
            raise ClaimModelError("a claim needs type and value")
        return cls(
            type=data["type"],
            value=data["value"],
            event_ids=tuple(data.get("event_ids", ())),
            stat=data.get("stat"),
            subject=data.get("subject"),
        )


@dataclass(frozen=True)
class Sentence:
    sentence_id: str
    text: str
    language: str
    claims: tuple[Claim, ...] = field(default_factory=tuple)
    author: str = "model"

    def __post_init__(self) -> None:
        if not self.sentence_id:
            raise ClaimModelError("sentence_id is required")
        if self.language not in LANGUAGES:
            raise ClaimModelError(f"unsupported language {self.language!r}")
        if self.author not in AUTHOR_TAGS:
            raise ClaimModelError(f"unknown author tag {self.author!r}")
        if not isinstance(self.claims, tuple):
            raise ClaimModelError("claims must be a tuple")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_id": self.sentence_id,
            "text": self.text,
            "language": self.language,
            "claims": [c.to_dict() for c in self.claims],
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sentence":
        allowed = {"sentence_id", "text", "language", "claims", "author"}
        extra = set(data) - allowed
        if extra:
            raise ClaimModelError(f"unexpected sentence fields {sorted(extra)}")
        return cls(
            sentence_id=data["sentence_id"],
            text=data["text"],
            language=data["language"],
            claims=tuple(Claim.from_dict(c) for c in data.get("claims", ())),
            author=data.get("author", "model"),
        )
