"""Shared fakes for the fleet worker tests (offline and Agent Framework). Synthetic data only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fleet_support import OFFICER, run_input

from agon import intake
from agon.fleet import worker
from agon.fleet.card import CardContext, FlowPostError
from agon.fleet.contracts import ApprovalRequest
from agon.governance.store import InMemoryStore


CONTEXT = CardContext(feed_base_url="https://agonexample.blob.core.windows.net/feed/", site_url="https://agon-example.azurestaticapps.net/")


class SpyStore(InMemoryStore):
    """Counts writes per container, so a test can prove the worker never writes approvals or nonces."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[str, str]] = []

    def create(self, container, key, body):
        self.writes.append(("create", container))
        return super().create(container, key, body)

    def replace(self, container, key, body, if_match):
        self.writes.append(("replace", container))
        return super().replace(container, key, body, if_match)


class MemQueues:
    """A queue port with delivery counts and no visibility timer: every receive redelivers."""

    def __init__(self) -> None:
        self.queues: dict[str, list[dict[str, Any]]] = {}
        self._ids = 0

    def send(self, queue: str, text: str) -> None:
        self._ids += 1
        self.queues.setdefault(queue, []).append({"id": f"m{self._ids}", "text": text, "count": 0})

    def receive(self, queue: str, max_messages: int, visibility_timeout: int) -> list[worker.InboundMessage]:
        out = []
        for m in self.queues.get(queue, [])[:max_messages]:
            m["count"] += 1
            out.append(worker.InboundMessage(m["id"], f"pop-{m['count']}", m["text"], m["count"]))
        return out

    def delete(self, queue: str, message: worker.InboundMessage) -> None:
        self.queues[queue] = [m for m in self.queues[queue] if m["id"] != message.message_id]

    def texts(self, queue: str) -> list[str]:
        return [m["text"] for m in self.queues.get(queue, [])]


class FakePoster:
    def __init__(self, name: str, fail: int = 0) -> None:
        self.name = name
        self.fail = fail
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> int:
        if self.fail:
            self.fail -= 1
            raise FlowPostError(f"{self.name} flow answered HTTP 503")
        self.payloads.append(payload)
        return 202


def runs_message(run_id: str = "run-1", target: str = "feed") -> str:
    return json.dumps({"run_id": run_id, "match_ref": run_input(run_id).match_ref, "target": target})


def intake_decides(w, request: ApprovalRequest, queues: MemQueues, decision: str = "approve") -> dict[str, Any]:
    """The api's intake: Teams payload through the channel adapter into verify(); pointer to `decisions`."""
    data = {"decision": decision, "bundle_sha256": request.bundle_sha256, "nonce": request.nonce}
    if decision == "approve":
        data.update({f"confirm:{row}": "true" for row in request.confirm_rows})
        # Tick the first two offered destinations (agon.governance.channels.teams reads dest:<id>).
        data.update({f"dest:{d['id']}": "true" if i < 2 else "false" for i, d in enumerate(request.destinations)})
    payload = {"responder": {"userPrincipalName": OFFICER}, "data": data}
    outcome = intake.handle_message(
        "decisions-teams", intake.encode_message(request.run_id, payload), w.deps.store, datetime.now(timezone.utc), queues
    )
    assert outcome.status == "verified", outcome
    return json.loads(queues.texts(worker.DECISIONS_QUEUE)[-1])
