"""Consume-once across a crash between the verifier's two writes.

`verify()` writes twice: it replaces `nonces/<nonce>` to consumed by etag, then creates
`approvals/<approval_id>`. If the process dies between the two, or the create raises a transient
error, the drain leaves the message and the queue redelivers it. The redelivery must complete the
approval record and send the pointer once ("verified_replay"), never lose the officer's decision
and never burn the nonce with nothing recorded.
"""

from __future__ import annotations

import json
from typing import Any

import govkit
from agon import intake
from agon.governance import verifier
from agon.governance.store import InMemoryStore
from agon.governance.verifier import APPROVALS, NONCES
from agon.intake import drain

NOW = govkit.later(30)
RUN = "run-0001"


class CrashAfterNonceWrite(InMemoryStore):
    """Performs the real nonce replace, then raises once: the process died right after the write."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = True
        self.writes: list[tuple[str, str, str]] = []

    def create(self, container: str, key: str, body: dict[str, Any]):
        doc = super().create(container, key, body)
        self.writes.append(("create", container, key))
        return doc

    def replace(self, container: str, key: str, body: dict[str, Any], if_match: str):
        doc = super().replace(container, key, body, if_match)
        self.writes.append(("replace", container, key))
        if self.armed and container == NONCES:
            self.armed = False
            raise ConnectionError("the process died right after the nonce write")
        return doc


class Queues:
    """The drain's queue port in memory. A message stays until deleted, so a re-drain redelivers it."""

    def __init__(self) -> None:
        self.queues: dict[str, list[drain.InboundMessage]] = {}
        self.deleted: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self._n = 0

    def put(self, queue: str, text: str) -> str:
        self._n += 1
        mid = f"m{self._n}"
        self.queues.setdefault(queue, []).append(drain.InboundMessage(mid, f"pop-{mid}", text, 1))
        return mid

    def receive(self, queue: str, max_messages: int, visibility_timeout: int) -> list[drain.InboundMessage]:
        return list(self.queues.get(queue, []))[:max_messages]

    def delete(self, queue: str, message: drain.InboundMessage) -> None:
        self.queues[queue] = [m for m in self.queues[queue] if m.message_id != message.message_id]
        self.deleted.append((queue, message.message_id))

    def send(self, queue: str, text: str) -> None:
        self.sent.append((queue, text))


def test_a_crash_right_after_the_nonce_write_is_completed_on_redelivery() -> None:
    store = CrashAfterNonceWrite()
    env = govkit.make_env(store=store)
    q = Queues()
    mid = q.put("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env)))

    first = drain.drain_once(q, store, lambda: NOW)
    assert [(line.message_id, line.status) for line in first] == [(mid, "error")]
    assert store.read(NONCES, env.nonce).body["status"] == "consumed"
    assert store.keys(APPROVALS) == [] and q.deleted == [] and q.sent == []

    second = drain.drain_once(q, store, lambda: govkit.later(90))
    assert [(line.message_id, line.status) for line in second] == [(mid, "verified_replay")]
    approval_id = verifier.approval_id_for(env.nonce)
    assert store.keys(APPROVALS) == [approval_id], "exactly one approval"
    assert q.deleted == [("decisions-teams", mid)]
    assert [queue for queue, _ in q.sent] == [intake.VERIFIED_QUEUE]
    pointer = json.loads(q.sent[0][1])
    assert pointer["approval_id"] == approval_id and pointer["decision"] == "approve" and pointer["channel"] == "teams"
