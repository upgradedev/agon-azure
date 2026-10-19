"""The three ports between the workflow and the outside world: model, translator, speech.

Why ports: the workflow's gates are deterministic and must be testable without a network, and
the same executor code must run against Microsoft Foundry in production and against fakes in CI.
Each port raises its own `...Unavailable` error for any outage, timeout or refusal, and the
calling step turns that into a flag on the officer's card (spec section 4). Nothing is silent.

All methods are async because the Foundry client is async; the REST adapters run their blocking
calls in a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

MODEL_ROLES: tuple[str, ...] = ("analyst", "writer", "localiser_editor", "triage", "desk_editor")
"""Every model-backed role. Prompt agents: analyst, writer, localiser_editor. Structured: triage, desk_editor."""

PROMPT_AGENT_ROLES: tuple[str, ...] = ("analyst", "writer", "localiser_editor")
STRUCTURED_ROLES: tuple[str, ...] = ("triage", "desk_editor")


class PortUnavailable(RuntimeError):
    """Base class: the service behind a port could not answer."""


class ModelUnavailable(PortUnavailable):
    """The model call failed, timed out or was refused."""


class ModelOutputError(ValueError):
    """The model answered, but not in the shape the role's schema demands."""


class TranslatorUnavailable(PortUnavailable):
    """The translation call failed or timed out."""


class SpeechUnavailable(PortUnavailable):
    """The speech synthesis call failed or timed out."""


@dataclass(frozen=True)
class Prompt:
    """A versioned prompt file, already checked against its pinned sha256."""

    role: str
    version: str
    text: str
    sha256: str


class ModelPort(Protocol):
    """Ask one role a question. `payload` is JSON-native data; the answer is the raw text.

    The role's schema check happens in the caller (`agon.agents.roles`), never in the adapter,
    so a fake and a real model are held to exactly the same validation.
    """

    async def complete(self, role: str, prompt: Prompt, payload: dict[str, Any], schema: dict[str, Any]) -> str: ...


class TranslatorPort(Protocol):
    """Translate English sentences into `target`, keeping every name in `protected` verbatim."""

    async def translate(self, texts: Sequence[str], target: str, protected: Sequence[str]) -> list[str]: ...


@dataclass(frozen=True)
class SpeechResult:
    audio: bytes
    media_type: str
    voice: str
    # [{"text": str, "offset_ms": int, "duration_ms": int}], or None when the service gave none.
    word_timings: list[dict[str, Any]] | None = field(default=None)


class SpeechPort(Protocol):
    async def synthesize(self, text: str, language: str) -> SpeechResult: ...
