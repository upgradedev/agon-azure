"""Decision intake: one queue per officer channel, drained through the channel adapter into verify().

Covers the rules in the `agon.intake` docstring: the channel comes from the queue, every route
reaches `verifier.verify` through its adapter exactly once, a refusal never burns the nonce, a
redelivery after a crash re-sends the pointer and approves nothing new, and the drain settles each
message (delete, leave for retry, or poison) without ever dropping one silently.
"""

from __future__ import annotations

import base64
import json

import pytest

import govkit
from agon import intake
from agon.governance import verifier
from agon.governance.channels import outlook, teams
from agon.governance.verifier import APPROVALS, NONCES, VerifyResult
from agon.intake import drain

NOW = govkit.later(30)
RUN = "run-0001"


class Outbox:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def send(self, queue: str, text: str) -> None:
        if self.fail:
            raise ConnectionError("queue unavailable")
        self.sent.append((queue, text))


def _payload(queue: str, env, **kw):
    if queue == "decisions-teams":
        return govkit.teams_payload(env, **kw)
    return govkit.outlook_payload(env, **kw)


# One valid message per route. A new route without a case fails test_every_route_has_a_case.
QUEUES = ("decisions-outlook", "decisions-teams")


def test_every_route_has_a_case():
    assert set(QUEUES) == set(intake.ROUTES)


@pytest.mark.parametrize("queue", QUEUES)
def test_a_valid_message_is_verified_and_its_pointer_forwarded(queue):
    env = govkit.make_env()
    out = Outbox()
    outcome = intake.handle_message(queue, intake.encode_message(RUN, _payload(queue, env)), env.store, NOW, out)
    assert outcome.status == "verified" and outcome.code == "approved"
    assert env.store.read(NONCES, env.nonce).body["status"] == "consumed"
    assert len(out.sent) == 1 and out.sent[0][0] == intake.VERIFIED_QUEUE
    pointer = json.loads(out.sent[0][1])
    assert pointer == {
        "kind": intake.VERIFIED_KIND, "run_id": RUN, "approval_id": verifier.approval_id_for(env.nonce),
        "bundle_sha256": env.bundle.bundle_sha256, "decision": "approve", "channel": intake.ROUTES[queue].CHANNEL, "code": "approved",
    }
    assert env.nonce not in out.sent[0][1], "the pointer must never carry the nonce"
    record = env.store.read(APPROVALS, pointer["approval_id"]).body
    assert record["channel"] == intake.ROUTES[queue].CHANNEL


def test_changes_is_forwarded_as_changes():
    env = govkit.make_env()
    out = Outbox()
    outcome = intake.handle_message("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env, decision="changes")), env.store, NOW, out)
    assert outcome.status == "verified" and outcome.code == "changes_requested"
    assert json.loads(out.sent[0][1])["decision"] == "changes"


@pytest.mark.parametrize("queue", QUEUES)
def test_every_route_calls_verify_exactly_once_through_its_adapter(queue, monkeypatch):
    env = govkit.make_env()
    calls = []
    sentinel = VerifyResult(ok=False, code="spy_refusal", detail="spy")

    def spy(decision, store, now):
        calls.append(decision)
        return sentinel

    monkeypatch.setattr(verifier, "verify", spy)
    out = Outbox()
    outcome = intake.handle_message(queue, intake.encode_message(RUN, _payload(queue, env)), env.store, NOW, out)
    assert len(calls) == 1 and calls[0].channel == intake.ROUTES[queue].CHANNEL
    assert outcome.status == "refused" and outcome.code == "spy_refusal"
    assert out.sent == []


def test_the_channel_comes_from_the_queue_not_the_body():
    """A Teams-shaped body on the Outlook queue is parsed as Outlook, refused, and burns nothing."""
    env = govkit.make_env()
    out = Outbox()
    body = govkit.teams_payload(env)
    body["channel"] = "teams"
    outcome = intake.handle_message("decisions-outlook", intake.encode_message(RUN, body), env.store, NOW, out)
    assert outcome.status == "refused" and outcome.code == "malformed_payload"
    assert env.store.read(NONCES, env.nonce).body["status"] == "pending"
    assert out.sent == []


@pytest.mark.parametrize("text", [
    "not base64 at all!",
    base64.b64encode(b"\xff\xfe").decode(),
    base64.b64encode(b"[1, 2]").decode(),
    base64.b64encode(json.dumps({"run_id": RUN}).encode()).decode(),
    base64.b64encode(json.dumps({"run_id": RUN, "payload": {}, "channel": "teams"}).encode()).decode(),
    base64.b64encode(json.dumps({"run_id": "a/b", "payload": {}}).encode()).decode(),
    base64.b64encode(json.dumps({"run_id": RUN, "payload": []}).encode()).decode(),
    "",
])
def test_an_undecodable_message_is_refused_and_burns_nothing(text):
    env = govkit.make_env()
    out = Outbox()
    outcome = intake.handle_message("decisions-teams", text, env.store, NOW, out)
    assert outcome.status == "undecodable" and outcome.code == "malformed_message"
    assert env.store.read(NONCES, env.nonce).body["status"] == "pending"
    assert out.sent == []


def test_an_unknown_queue_has_no_route():
    env = govkit.make_env()
    outcome = intake.handle_message("decisions-console", intake.encode_message(RUN, govkit.console_body(env)), env.store, NOW, Outbox())
    assert outcome.status == "unknown_queue"
    assert env.store.read(NONCES, env.nonce).body["status"] == "pending"


def test_a_verifier_refusal_leaves_the_nonce_for_another_channel():
    env = govkit.make_env(confirm_rows=("s3",))
    out = Outbox()
    refused = intake.handle_message("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env)), env.store, NOW, out)
    assert refused.status == "refused" and refused.code == "unconfirmed_rows" and out.sent == []
    ok = intake.handle_message(
        "decisions-outlook",
        intake.encode_message(RUN, govkit.outlook_payload(env, option=outlook.OPTION_APPROVE_CONFIRM, confirm_rows=("s3",))),
        env.store, NOW, out,
    )
    assert ok.status == "verified" and json.loads(out.sent[0][1])["channel"] == "outlook"


def test_a_crash_between_verify_and_send_is_replayed_without_a_second_approval():
    env = govkit.make_env()
    text = intake.encode_message(RUN, govkit.teams_payload(env))
    with pytest.raises(ConnectionError):
        intake.handle_message("decisions-teams", text, env.store, NOW, Outbox(fail=True))
    assert env.store.read(NONCES, env.nonce).body["status"] == "consumed"
    out = Outbox()
    again = intake.handle_message("decisions-teams", text, env.store, govkit.later(60), out)
    assert again.status == "verified_replay" and again.code == "approved"
    assert json.loads(out.sent[0][1])["approval_id"] == verifier.approval_id_for(env.nonce)
    assert env.store.keys(APPROVALS) == [verifier.approval_id_for(env.nonce)]


def test_a_decision_another_channel_already_made_is_not_replayed():
    env = govkit.make_env()
    first = intake.handle_message("decisions-outlook", intake.encode_message(RUN, govkit.outlook_payload(env)), env.store, NOW, Outbox())
    assert first.status == "verified"
    out = Outbox()
    second = intake.handle_message("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env)), env.store, NOW, out)
    assert second.status == "refused" and second.code == "already_consumed"
    assert out.sent == []


def test_a_replay_with_a_different_bundle_is_not_replayed():
    env = govkit.make_env()
    intake.handle_message("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env)), env.store, NOW, Outbox())
    forged = govkit.teams_payload(env)
    forged["data"]["bundle_sha256"] = "0" * 64
    out = Outbox()
    outcome = intake.handle_message("decisions-teams", intake.encode_message(RUN, forged), env.store, NOW, out)
    assert outcome.status == "refused" and out.sent == []


# ----------------------------------------------------------------- the drain


class MemoryQueues:
    def __init__(self) -> None:
        self.queues: dict[str, list[drain.InboundMessage]] = {}
        self.deleted: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self.fail_delete = False
        self._n = 0

    def put(self, queue: str, text: str, dequeue_count: int = 1) -> str:
        self._n += 1
        mid = f"m{self._n}"
        self.queues.setdefault(queue, []).append(drain.InboundMessage(mid, f"pop-{mid}", text, dequeue_count))
        return mid

    def receive(self, queue, max_messages, visibility_timeout):
        return list(self.queues.get(queue, []))[:max_messages]

    def delete(self, queue, message):
        if self.fail_delete:
            raise ConnectionError("delete failed")
        self.queues[queue] = [m for m in self.queues[queue] if m.message_id != message.message_id]
        self.deleted.append((queue, message.message_id))

    def send(self, queue, text):
        self.sent.append((queue, text))


def test_drain_deletes_settled_messages_and_forwards_verified_ones():
    env = govkit.make_env()
    q = MemoryQueues()
    good = q.put("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env)))
    bad = q.put("decisions-outlook", "garbage")
    lines = drain.drain_once(q, env.store, lambda: NOW)
    assert {(l.message_id, l.status) for l in lines} == {(good, "verified"), (bad, "undecodable")}
    assert set(q.deleted) == {("decisions-teams", good), ("decisions-outlook", bad)}
    assert [s[0] for s in q.sent] == [intake.VERIFIED_QUEUE]


def test_drain_leaves_a_message_when_handling_raises():
    env = govkit.make_env()
    q = MemoryQueues()
    mid = q.put("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env)))

    class Broken:
        def read(self, *a):
            raise ConnectionError("store down")

    lines = drain.drain_once(q, Broken(), lambda: NOW)
    assert [(l.message_id, l.status) for l in lines] == [(mid, "error")]
    assert q.deleted == [] and q.sent == []


def test_drain_moves_a_message_to_poison_after_max_attempts_and_never_drops_it():
    env = govkit.make_env()
    q = MemoryQueues()
    text = intake.encode_message(RUN, govkit.teams_payload(env))
    mid = q.put("decisions-teams", text, dequeue_count=intake.MAX_ATTEMPTS + 1)
    lines = drain.drain_once(q, env.store, lambda: NOW)
    assert [(l.message_id, l.status) for l in lines] == [(mid, "poisoned")]
    assert q.sent[0][0] == intake.POISON_QUEUE
    assert json.loads(q.sent[0][1]) == {"queue": "decisions-teams", "message_id": mid, "dequeue_count": intake.MAX_ATTEMPTS + 1, "text": text}
    assert env.store.read(NONCES, env.nonce).body["status"] == "pending"


def test_a_failed_delete_is_reported_and_the_redelivery_is_safe():
    env = govkit.make_env()
    q = MemoryQueues()
    q.fail_delete = True
    mid = q.put("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(env)))
    first = drain.drain_once(q, env.store, lambda: NOW)
    assert [(l.message_id, l.status, l.code) for l in first] == [(mid, "error", "delete_failed")]
    q.fail_delete = False
    second = drain.drain_once(q, env.store, lambda: govkit.later(90))
    assert [(l.message_id, l.status) for l in second] == [(mid, "verified_replay")]
    assert env.store.keys(APPROVALS) == [verifier.approval_id_for(env.nonce)]
    assert [json.loads(t)["approval_id"] for _, t in q.sent] == [verifier.approval_id_for(env.nonce)] * 2


def test_routes_bind_each_queue_to_its_named_channel():
    assert intake.ROUTES == {"decisions-teams": teams, "decisions-outlook": outlook}


# ----------------------------------------------------------------- destinations through the queue routes


def test_a_teams_message_carries_the_ticked_destinations_into_the_approval_record():
    env = govkit.make_env()
    out = Outbox()
    payload = govkit.teams_payload(env, destinations=("feeds", "youtube"))
    outcome = intake.handle_message("decisions-teams", intake.encode_message(RUN, payload), env.store, NOW, out)
    assert outcome.status == "verified"
    record = env.store.read(APPROVALS, outcome.forwarded.approval_id).body
    assert record["destinations"] == ["feeds", "youtube"] and record["channel"] == "teams"


def test_a_teams_message_with_every_destination_off_is_refused_and_forwards_nothing():
    env = govkit.make_env()
    out = Outbox()
    payload = govkit.teams_payload(env, destinations=())
    outcome = intake.handle_message("decisions-teams", intake.encode_message(RUN, payload), env.store, NOW, out)
    assert outcome.status == "refused" and outcome.code == "no_destination_selected"
    assert out.sent == [] and env.store.read(NONCES, env.nonce).body["status"] == "pending"


def test_an_outlook_message_approves_the_destinations_as_offered():
    env = govkit.make_env(destinations=("club_site", "journalist_email"))
    out = Outbox()
    outcome = intake.handle_message("decisions-outlook", intake.encode_message(RUN, govkit.outlook_payload(env)), env.store, NOW, out)
    record = env.store.read(APPROVALS, outcome.forwarded.approval_id).body
    assert record["destinations"] == ["club_site", "journalist_email"] and record["destinations_as_offered"] is True
