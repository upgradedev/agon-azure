"""Drain the inbound decision queues once: receive, hand each message to `handle_message`, settle it.

Settling rules (module docstring of `agon.intake` explains why):

* `handle_message` returned (verified, replayed, refused, undecodable, unknown queue): delete;
* `handle_message` raised: leave the message; it reappears after the visibility timeout;
* delivered more than `MAX_ATTEMPTS` times: copy it to `intake-poison` with its queue name and
  delivery count, then delete it. An officer's decision is never dropped silently.

The queue port is a protocol so the drain is tested with an in-memory queue; the Azure adapter is
`agon.intake.azure_queues`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

from agon.intake import MAX_ATTEMPTS, POISON_QUEUE, ROUTES, IntakeOutcome, handle_message

VISIBILITY_TIMEOUT_SECONDS = 60
BATCH = 16


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    pop_receipt: str
    text: str
    dequeue_count: int


class QueuePort(Protocol):
    def receive(self, queue: str, max_messages: int, visibility_timeout: int) -> list[InboundMessage]: ...

    def delete(self, queue: str, message: InboundMessage) -> None: ...

    def send(self, queue: str, text: str) -> None: ...


@dataclass(frozen=True)
class DrainLine:
    queue: str
    message_id: str
    status: str  # an IntakeOutcome status, or "poisoned" or "error"
    code: str
    detail: str


def drain_once(
    port: QueuePort,
    store: Any,
    clock: Callable[[], datetime],
    *,
    queues: tuple[str, ...] = tuple(ROUTES),
    log: Callable[[DrainLine], None] = lambda line: None,
) -> list[DrainLine]:
    lines: list[DrainLine] = []

    def record(line: DrainLine) -> None:
        lines.append(line)
        log(line)

    for queue in queues:
        for message in port.receive(queue, BATCH, VISIBILITY_TIMEOUT_SECONDS):
            if message.dequeue_count > MAX_ATTEMPTS:
                poison = {"queue": queue, "message_id": message.message_id, "dequeue_count": message.dequeue_count, "text": message.text}
                port.send(POISON_QUEUE, json.dumps(poison, sort_keys=True, separators=(",", ":")))
                port.delete(queue, message)
                record(DrainLine(queue, message.message_id, "poisoned", "max_attempts", f"moved to {POISON_QUEUE} after {message.dequeue_count} deliveries"))
                continue
            try:
                outcome: IntakeOutcome = handle_message(queue, message.text, store, clock(), port)
            except Exception as exc:  # noqa: BLE001 - any failure leaves the message for a retry
                record(DrainLine(queue, message.message_id, "error", type(exc).__name__, str(exc)[:300]))
                continue
            try:
                port.delete(queue, message)
            except Exception as exc:  # noqa: BLE001 - redelivery is safe: verify answers already_consumed
                record(DrainLine(queue, message.message_id, "error", "delete_failed", f"{outcome.status}; {type(exc).__name__}: {str(exc)[:200]}"))
                continue
            record(DrainLine(queue, message.message_id, outcome.status, outcome.code, outcome.detail))
    return lines
