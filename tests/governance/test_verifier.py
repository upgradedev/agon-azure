"""The one verifier: hash, nonce, expiry, role, confirm rows, consume-once, target rules."""

from __future__ import annotations

import dataclasses

import pytest

import govkit
from agon.governance import verifier
from agon.governance.channels import console, outlook, signed_link, teams
from agon.governance.verifier import ApprovalDecision, NONCES, verify


def _decision(env, **over):
    base = dict(
        bundle_sha256=env.bundle.bundle_sha256,
        nonce=env.nonce,
        decision="approve",
        approver=govkit.OFFICER,
        channel="teams",
        received_at=govkit.T0,
    )
    base.update(over)
    return ApprovalDecision(**base)


def _status(env):
    return env.store.read(NONCES, env.nonce).body["status"]


def test_valid_approval_consumes_the_nonce_and_records_the_approval():
    env = govkit.make_env()
    result = verify(_decision(env), env.store, govkit.later(60))
    assert result.ok and result.approved and result.code == "approved"
    assert _status(env) == "consumed"
    record = env.store.read(verifier.APPROVALS, result.approval_id).body
    assert record["bundle_sha256"] == env.bundle.bundle_sha256
    assert record["approver"] == govkit.OFFICER and record["channel"] == "teams"
    assert env.nonce not in str(record)  # only the nonce hash is kept


def test_expiry_boundary_uses_injected_now():
    env = govkit.make_env(ttl=600)
    assert verify(_decision(env), env.store, govkit.later(599)).ok
    env = govkit.make_env(ttl=600)
    result = verify(_decision(env), env.store, govkit.later(600))
    assert result.code == "expired" and _status(env) == "pending"


def test_payload_time_is_never_trusted_for_expiry():
    env = govkit.make_env(ttl=600)
    early = _decision(env, received_at=govkit.later(1))
    assert verify(early, env.store, govkit.later(3600)).code == "expired"


def test_wrong_hash_is_refused_and_does_not_burn_the_nonce():
    env = govkit.make_env()
    other, _ = govkit.make_bundle(tag=" other")
    verifier.register_sealed_bundle(env.store, other.manifest_bytes)
    result = verify(_decision(env, bundle_sha256=other.bundle_sha256), env.store, govkit.later(5))
    assert result.code == "hash_mismatch"
    assert _status(env) == "pending"
    assert verify(_decision(env), env.store, govkit.later(6)).ok


def test_unknown_nonce_and_malformed_decisions():
    env = govkit.make_env()
    assert verify(_decision(env, nonce="not-a-real-nonce"), env.store, govkit.later(1)).code == "unknown_nonce"
    upper = _decision(env, bundle_sha256=env.bundle.bundle_sha256.upper())
    assert verify(upper, env.store, govkit.later(1)).code == "malformed_decision"
    naive = _decision(env, received_at=govkit.T0.replace(tzinfo=None))
    assert verify(naive, env.store, govkit.later(1)).code == "malformed_decision"
    assert verify(_decision(env, decision="approve-all"), env.store, govkit.later(1)).code == "malformed_decision"
    assert verify(_decision(env, channel="sms"), env.store, govkit.later(1)).code == "malformed_decision"
    assert _status(env) == "pending"


def test_wrong_role_is_refused():
    env = govkit.make_env()
    assert verify(_decision(env, approver=govkit.INTERN), env.store, govkit.later(1)).code == "not_approver"
    assert verify(_decision(env, approver=govkit.VISITOR), env.store, govkit.later(1)).code == "not_approver"
    assert _status(env) == "pending"


def test_approver_identity_is_case_folded():
    env = govkit.make_env()
    assert verify(_decision(env, approver="  Officer@Media.Example "), env.store, govkit.later(1)).ok


def test_replay_is_refused():
    env = govkit.make_env()
    assert verify(_decision(env), env.store, govkit.later(1)).ok
    again = verify(_decision(env), env.store, govkit.later(2))
    assert again.code == "already_consumed" and "teams" in again.detail


def test_cross_channel_consume_once_teams_then_outlook_and_link():
    env = govkit.make_env()
    assert teams.handle(govkit.teams_payload(env), env.store, govkit.later(10)).approved
    late = outlook.handle(govkit.outlook_payload(env, email=govkit.DEPUTY), env.store, govkit.later(20))
    assert late.code == "already_consumed" and "teams" in late.detail
    secrets = govkit.TestSecrets()
    token = signed_link.issue_token(
        secrets=secrets,
        key_id="k1",
        approver=govkit.OFFICER,
        bundle_sha256=env.bundle.bundle_sha256,
        nonce=env.nonce,
        decision="approve",
        expires_at=govkit.later(900),
    )
    assert signed_link.handle(token, env.store, govkit.later(30), secrets=secrets).code == "already_consumed"


def test_changes_requested_also_consumes():
    env = govkit.make_env()
    first = teams.handle(govkit.teams_payload(env, decision="changes"), env.store, govkit.later(10))
    assert first.ok and not first.approved and first.code == "changes_requested"
    assert outlook.handle(govkit.outlook_payload(env), env.store, govkit.later(20)).code == "already_consumed"


def test_console_stand_in_can_never_approve_the_public_feed():
    env = govkit.make_env(target="feed")
    result = console.handle(govkit.console_body(env), env.store, govkit.later(5), principal=govkit.VISITOR)
    assert result.code == "channel_not_allowed" and _status(env) == "pending"
    sandbox = govkit.make_env(target="sandbox")
    assert console.handle(govkit.console_body(sandbox), sandbox.store, govkit.later(5), principal=govkit.VISITOR).approved


def test_unticked_confirm_rows_are_named_and_do_not_consume():
    env = govkit.make_env(confirm_rows=("s3", "s7"))
    result = teams.handle(govkit.teams_payload(env, confirmed=("s3",)), env.store, govkit.later(5))
    assert result.code == "unconfirmed_rows" and "s7" in result.detail
    assert _status(env) == "pending"
    assert teams.handle(govkit.teams_payload(env, confirmed=("s3", "s7")), env.store, govkit.later(6)).approved


def test_a_refused_bundle_cannot_get_a_nonce_or_an_approval():
    refused_verdicts = {"factgate": "pass", "rights": "refused"}
    env = govkit.make_env()
    bad, _ = govkit.make_bundle(verdicts=refused_verdicts)
    verifier.register_sealed_bundle(env.store, bad.manifest_bytes)
    with pytest.raises(verifier.IssueError):
        verifier.issue_nonce(env.store, bad.bundle_sha256, target="feed", now=govkit.T0)
    # Even a nonce planted straight into the store is refused by verify().
    env.store.create(NONCES, "planted", {
        "bundle_sha256": bad.bundle_sha256, "purpose": "fulltime", "target": "feed", "role": "approver",
        "issued_at": 0, "expires_at": 4_102_444_800, "confirm_rows": [], "status": "pending",
    })
    planted = dataclasses.replace(_decision(env), bundle_sha256=bad.bundle_sha256, nonce="planted")
    assert verify(planted, env.store, govkit.later(1)).code == "bundle_refused_by_gate"


def test_tampered_sealed_manifest_in_the_store_is_refused():
    env = govkit.make_env()
    doc = env.store.read(verifier.BUNDLES, env.bundle.bundle_sha256)
    tampered = doc.body["manifest"].replace('"fulltime"', '"charter"')
    env.store.replace(verifier.BUNDLES, env.bundle.bundle_sha256, {**doc.body, "manifest": tampered}, if_match=doc.etag)
    result = verify(_decision(env), env.store, govkit.later(1))
    assert result.code == "sealed_hash_mismatch" and _status(env) == "pending"


def test_nonce_issue_rejects_unknown_target_and_unsealed_bundle():
    env = govkit.make_env()
    with pytest.raises(verifier.IssueError):
        verifier.issue_nonce(env.store, env.bundle.bundle_sha256, target="public", now=govkit.T0)
    with pytest.raises(verifier.IssueError):
        verifier.issue_nonce(env.store, "f" * 64, target="feed", now=govkit.T0)
