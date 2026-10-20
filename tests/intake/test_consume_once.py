"""Consume-once across a crash between the verifier's two writes.

`verify()` writes twice: it replaces `nonces/<nonce>` to consumed by etag, then creates
`approvals/<approval_id>`. If the process dies between the two, or the create raises a transient
error, the drain leaves the message and the queue redelivers it. The redelivery must complete the
approval record and send the pointer once ("verified_replay"), never lose the officer's decision
and never burn the nonce with nothing recorded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import govkit
import pytest
from agon import intake
from agon.governance import verifier
from agon.governance.channels import teams
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


class FailApprovalCreateOnce(CrashAfterNonceWrite):
    """The nonce write lands; the approval create then fails once with a transient store error."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False  # the nonce replace succeeds normally
        self.fail_create = True

    def create(self, container: str, key: str, body: dict[str, Any]):
        if self.fail_create and container == APPROVALS:
            self.fail_create = False
            raise TimeoutError("store throttled the approval create")
        return super().create(container, key, body)


class Out:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, queue: str, text: str) -> None:
        self.sent.append((queue, text))


EXPECTED_RECORD_KEYS = {
    "approval_id", "decision", "approver", "channel", "received_at", "verified_at", "confirmed_rows",
    "destinations", "destinations_as_offered", "bundle_sha256", "nonce_sha256", "purpose", "target",
}


def _without_nonce_derived(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in ("approval_id", "nonce_sha256")}


def _crash_first_pass(store: CrashAfterNonceWrite, env: govkit.Env, q: Queues, queue: str = "decisions-teams") -> str:
    payload = govkit.teams_payload(env) if queue == "decisions-teams" else govkit.outlook_payload(env)
    mid = q.put(queue, intake.encode_message(RUN, payload))
    lines = drain.drain_once(q, store, lambda: NOW, queues=(queue,))
    assert [(line.message_id, line.status) for line in lines] == [(mid, "error")]
    assert store.read(NONCES, env.nonce).body["status"] == "consumed" and store.keys(APPROVALS) == []
    return mid


def test_a_transient_error_on_the_approval_create_is_completed_on_redelivery() -> None:
    store = FailApprovalCreateOnce()
    env = govkit.make_env(store=store)
    q = Queues()
    mid = _crash_first_pass(store, env, q, "decisions-outlook")
    second = drain.drain_once(q, store, lambda: govkit.later(120))
    assert [(line.message_id, line.status) for line in second] == [(mid, "verified_replay")]
    assert store.keys(APPROVALS) == [verifier.approval_id_for(env.nonce)]
    record = store.read(APPROVALS, verifier.approval_id_for(env.nonce)).body
    assert record["channel"] == "outlook" and record["destinations_as_offered"] is True


def test_teams_commits_then_crashes_an_outlook_decision_is_refused_and_the_teams_redelivery_completes() -> None:
    store = CrashAfterNonceWrite()
    env = govkit.make_env(store=store)
    q = Queues()
    teams_mid = _crash_first_pass(store, env, q)
    writes = len(store.writes)

    outlook_mid = q.put("decisions-outlook", intake.encode_message(RUN, govkit.outlook_payload(env)))
    second = drain.drain_once(q, store, lambda: govkit.later(60), queues=("decisions-outlook",))
    assert [(line.message_id, line.status, line.code) for line in second] == [(outlook_mid, "refused", "already_consumed")]
    assert len(store.writes) == writes, "a decision from another channel writes nothing"
    assert store.keys(APPROVALS) == [] and q.sent == []

    third = drain.drain_once(q, store, lambda: govkit.later(90), queues=("decisions-teams",))
    assert [(line.message_id, line.status) for line in third] == [(teams_mid, "verified_replay")]
    approval_id = verifier.approval_id_for(env.nonce)
    assert store.keys(APPROVALS) == [approval_id], "exactly one approval"
    assert store.read(APPROVALS, approval_id).body["channel"] == "teams"
    assert [json.loads(text)["channel"] for _, text in q.sent] == ["teams"]


def test_after_a_crash_a_message_naming_another_bundle_is_refused_and_writes_nothing() -> None:
    store = CrashAfterNonceWrite()
    env = govkit.make_env(store=store)
    q = Queues()
    mid = _crash_first_pass(store, env, q)
    writes = len(store.writes)
    forged = govkit.teams_payload(env)
    forged["data"]["bundle_sha256"] = "0" * 64
    out = Out()
    outcome = intake.handle_message("decisions-teams", intake.encode_message(RUN, forged), store, govkit.later(60), out)
    assert outcome.status == "refused" and outcome.code == "already_consumed"
    assert len(store.writes) == writes and store.keys(APPROVALS) == [] and out.sent == []
    again = drain.drain_once(q, store, lambda: govkit.later(90))
    assert [(line.message_id, line.status) for line in again] == [(mid, "verified_replay")]
    assert store.keys(APPROVALS) == [verifier.approval_id_for(env.nonce)]


def test_the_redelivery_completes_after_the_nonce_expired_and_keeps_the_first_pass_times() -> None:
    """The projection never reads `now`: past expires_at it still completes, with the original times."""
    store = CrashAfterNonceWrite()
    env = govkit.make_env(store=store, ttl=1800)
    q = Queues()
    mid = _crash_first_pass(store, env, q)
    expired = govkit.later(1800 + 3600)
    assert int(expired.timestamp()) >= store.read(NONCES, env.nonce).body["expires_at"]
    second = drain.drain_once(q, store, lambda: expired)
    assert [(line.message_id, line.status) for line in second] == [(mid, "verified_replay")]
    record = store.read(APPROVALS, verifier.approval_id_for(env.nonce)).body
    consumed = store.read(NONCES, env.nonce).body["consumed"]
    assert record["verified_at"] == consumed["verified_at"] == NOW.isoformat()
    assert record["received_at"] == consumed["received_at"] == NOW.isoformat()


def test_the_projected_record_is_the_record_the_normal_path_writes() -> None:
    """Field for field: built from the consumed nonce, equal to a run that never crashed."""
    calm = govkit.make_env()
    outcome = intake.handle_message("decisions-teams", intake.encode_message(RUN, govkit.teams_payload(calm)), calm.store, NOW, Out())
    assert outcome.status == "verified"
    normal = calm.store.read(APPROVALS, verifier.approval_id_for(calm.nonce)).body
    nonce_doc = calm.store.read(NONCES, calm.nonce).body
    assert set(normal) == EXPECTED_RECORD_KEYS
    assert normal == {
        **nonce_doc["consumed"],
        "bundle_sha256": calm.bundle.bundle_sha256,
        "nonce_sha256": hashlib.sha256(calm.nonce.encode("utf-8")).hexdigest(),
        "purpose": nonce_doc["purpose"],
        "target": nonce_doc["target"],
    }

    store = CrashAfterNonceWrite()
    crashed = govkit.make_env(store=store)
    q = Queues()
    _crash_first_pass(store, crashed, q)
    drain.drain_once(q, store, lambda: govkit.later(90))
    replayed = store.read(APPROVALS, verifier.approval_id_for(crashed.nonce)).body
    assert set(replayed) == EXPECTED_RECORD_KEYS
    assert crashed.bundle.bundle_sha256 == calm.bundle.bundle_sha256
    assert _without_nonce_derived(replayed) == _without_nonce_derived(normal)
    assert replayed["approval_id"] == verifier.approval_id_for(crashed.nonce)
    assert replayed["nonce_sha256"] == hashlib.sha256(crashed.nonce.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- the verifier on its own


def test_verify_projects_once_and_a_repeat_writes_nothing_more() -> None:
    store = CrashAfterNonceWrite()
    env = govkit.make_env(store=store)
    with pytest.raises(ConnectionError):
        verifier.verify(teams.parse(govkit.teams_payload(env), received_at=NOW), store, NOW)
    for seconds in (60, 120):
        moment = govkit.later(seconds)
        result = verifier.verify(teams.parse(govkit.teams_payload(env), received_at=moment), store, moment)
        assert not result.ok and result.code == "already_consumed"
    creates = [w for w in store.writes if w[0] == "create" and w[1] == APPROVALS]
    assert creates == [("create", APPROVALS, verifier.approval_id_for(env.nonce))]


def test_re_verifying_a_recorded_decision_attempts_no_write() -> None:
    """The fleet worker holds only read on approvals and re-verifies what the intake recorded."""
    store = CrashAfterNonceWrite()
    store.armed = False
    env = govkit.make_env(store=store)
    assert verifier.verify(teams.parse(govkit.teams_payload(env), received_at=NOW), store, NOW).ok
    before = len(store.writes)
    again = verifier.verify(teams.parse(govkit.teams_payload(env), received_at=govkit.later(60)), store, govkit.later(60))
    assert not again.ok and again.code == "already_consumed"
    assert store.writes[before:] == [], "no create is even attempted when the record is there"


def test_a_normal_decision_still_writes_the_nonce_then_one_record() -> None:
    store = CrashAfterNonceWrite()
    store.armed = False
    env = govkit.make_env(store=store)
    before = len(store.writes)
    result = verifier.verify(teams.parse(govkit.teams_payload(env), received_at=NOW), store, NOW)
    assert result.ok and result.code == "approved"
    assert store.writes[before:] == [("replace", NONCES, env.nonce), ("create", APPROVALS, verifier.approval_id_for(env.nonce))]


def test_a_consumed_nonce_whose_decision_names_another_approval_id_writes_nothing() -> None:
    env = govkit.make_env()
    key = govkit.plant_nonce(env.store, env.bundle.bundle_sha256, key="planted-consumed")
    doc = env.store.read(NONCES, key)
    tampered = {**doc.body, "status": "consumed", "consumed": {"approval_id": "0" * 32, "channel": "teams", "decision": "approve"}}
    env.store.replace(NONCES, key, tampered, if_match=doc.etag)
    payload = govkit.teams_payload(env)
    payload["data"]["nonce"] = key
    result = teams.handle(payload, env.store, NOW)
    assert not result.ok and result.code == "already_consumed"
    assert env.store.keys(APPROVALS) == []


def test_a_record_already_there_on_the_normal_path_keeps_the_refusal() -> None:
    """An etag-blind store lets two passes both consume; only the first record stands."""
    env = govkit.make_env()
    approval_id = verifier.approval_id_for(env.nonce)
    env.store.create(APPROVALS, approval_id, {"approval_id": approval_id, "planted": True})
    result = verifier.verify(teams.parse(govkit.teams_payload(env), received_at=NOW), env.store, NOW)
    assert not result.ok and result.code == "already_consumed"
    assert env.store.read(APPROVALS, approval_id).body == {"approval_id": approval_id, "planted": True}
