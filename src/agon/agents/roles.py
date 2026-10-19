"""One call path for every model role: load the pinned prompt, ask the port, enforce the schema.

`ask` returns the parsed, validated answer or raises one of two errors, and nothing else:

* `ModelUnavailable` for an outage, a timeout or any adapter failure;
* `ModelOutputError` for an answer that does not meet the role's schema.

The caller decides what each error means for the card (retry, template line, skipped critic).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from agon.agents import schemas
from agon.agents.ports import ModelOutputError, ModelPort, ModelUnavailable
from agon.agents.prompts import load_prompt

DEFAULT_TIMEOUT_SECONDS = 90.0

PARSERS: dict[str, Callable[[str], Any]] = {
    "analyst": schemas.parse_analyst,
    "writer": schemas.parse_writer,
    "localiser_editor": schemas.parse_post_edit,
    "triage": schemas.parse_triage,
    "desk_editor": schemas.parse_desk_flags,
}


async def ask(model: ModelPort, role: str, payload: dict[str, Any], timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    prompt = load_prompt(role)
    try:
        text = await asyncio.wait_for(model.complete(role, prompt, payload, schemas.SCHEMAS[role]), timeout)
    except ModelUnavailable:
        raise
    except asyncio.TimeoutError as exc:
        raise ModelUnavailable(f"{role} timed out after {timeout:g} s") from exc
    except ModelOutputError:
        raise
    except Exception as exc:  # noqa: BLE001 - any adapter failure is an outage for the card
        raise ModelUnavailable(f"{role} failed: {type(exc).__name__}: {exc}") from exc
    return PARSERS[role](text)
