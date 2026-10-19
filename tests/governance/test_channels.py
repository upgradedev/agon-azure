"""Channel adapters translate only. Malformed payloads are refused; nothing defaults to approve."""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import inspect
import json

import govkit
from agon.governance import verifier
from agon.governance.channels import console, outlook, signed_link, teams
from agon.governance.verifier import NONCES


def _status(env):
    return env.store.read(NONCES, env.nonce).body["status"]


# ----------------------------------------------------------------- Teams


def test_teams_maps_decisions_and_reads_the_responder():
    env = govkit.make_env()
    d = teams.parse(govkit.teams_payload(env, decision="request_changes", confirmed=("s3",)), received_at=govkit.T0)
    assert d.decision == "changes" and d.channel == "teams" and d.approver == govkit.OFFICER
    assert d.confirmed_rows == frozenset({"s3"})


def test_teams_refuses_unknown_decision_and_missing_responder_without_consuming():
    env = govkit.make_env()
    unknown = govkit.teams_payload(env, decision="approve_everything")
    assert teams.handle(unknown, env.store, govkit.later(1)).code == "malformed_payload"
    no_responder = govkit.teams_payload(env)
    no_responder["responder"] = {}
    assert teams.handle(no_responder, env.store, govkit.later(1)).code == "malformed_payload"
    assert teams.handle("not json", env.store, govkit.later(1)).code == "malformed_payload"
    assert _status(env) == "pending"


def test_teams_ignores_any_channel_or_time_in_the_payload():
    env = govkit.make_env()
    payload = govkit.teams_payload(env)
    payload["data"]["channel"] = "console"
    payload["responseTime"] = "2001-01-01T00:00:00Z"
    d = teams.parse(payload, received_at=govkit.later(7))
    assert d.channel == "teams" and d.received_at == govkit.later(7)


# ----------------------------------------------------------------- Outlook


def test_outlook_options_map_explicitly():
    env = govkit.make_env(confirm_rows=("s3",))
    assert outlook.parse(govkit.outlook_payload(env, option="Request changes"), received_at=govkit.T0).decision == "changes"
    plain = outlook.parse(govkit.outlook_payload(env, option="Approve", confirm_rows=("s3",)), received_at=govkit.T0)
    assert plain.decision == "approve" and plain.confirmed_rows == frozenset()
    full = outlook.parse(
        govkit.outlook_payload(env, option=outlook.OPTION_APPROVE_CONFIRM, confirm_rows=("s3",)), received_at=govkit.T0
    )
    assert full.confirmed_rows == frozenset({"s3"})


def test_outlook_unknown_option_is_refused_never_approved():
    env = govkit.make_env()
    for option in ("approve", "Yes", "Approve ", ""):
        result = outlook.handle(govkit.outlook_payload(env, option=option), env.store, govkit.later(1))
        assert not result.ok, option
    assert _status(env) == "pending"


def test_outlook_plain_approve_cannot_skip_confirm_rows():
    env = govkit.make_env(confirm_rows=("s3",))
    result = outlook.handle(govkit.outlook_payload(env, option="Approve", confirm_rows=("s3",)), env.store, govkit.later(1))
    assert result.code == "unconfirmed_rows"
    ok = outlook.handle(
        govkit.outlook_payload(env, option=outlook.OPTION_APPROVE_CONFIRM, confirm_rows=("s3",)), env.store, govkit.later(2)
    )
    assert ok.approved and ok.channel == "outlook"


# ----------------------------------------------------------------- signed link


def _token(env, secrets, **over):
    args = dict(
        secrets=secrets,
        key_id="k1",
        approver=govkit.OFFICER,
        bundle_sha256=env.bundle.bundle_sha256,
        nonce=env.nonce,
        decision="approve",
        expires_at=govkit.later(900),
    )
    args.update(over)
    return signed_link.issue_token(**args)


def _flip(text: str, index: int) -> str:
    ch = "A" if text[index] != "A" else "B"
    return text[:index] + ch + text[index + 1 :]


def test_signed_link_round_trip_approves_once():
    env, secrets = govkit.make_env(), govkit.TestSecrets()
    token = _token(env, secrets)
    ticked = govkit.offered(env)
    assert signed_link.handle(token, env.store, govkit.later(5), secrets=secrets, destinations=ticked).approved
    replay = signed_link.handle(token, env.store, govkit.later(6), secrets=secrets, destinations=ticked)
    assert replay.code == "already_consumed"


def test_tampered_signed_links_are_refused_without_consuming():
    env, secrets = govkit.make_env(), govkit.TestSecrets()
    token = _token(env, secrets)
    version, kid, payload_b64, mac_b64 = token.split(".")
    tampered = [
        ".".join([version, kid, _flip(payload_b64, 5), mac_b64]),
        ".".join([version, kid, payload_b64, _flip(mac_b64, 3)]),
        ".".join(["v2", kid, payload_b64, mac_b64]),
        ".".join([version, "k2", payload_b64, mac_b64]),
        token + ".extra",
        token.replace(".", "", 1),
    ]
    other_bundle, _ = govkit.make_bundle(tag=" other")
    other = _token(env, secrets, bundle_sha256=other_bundle.bundle_sha256).split(".")
    tampered.append(".".join([version, kid, other[2], mac_b64]))  # payload swapped under the old MAC
    for bad in tampered:
        result = signed_link.handle(bad, env.store, govkit.later(5), secrets=secrets)
        assert result.code == "malformed_payload", bad
    assert _status(env) == "pending"


def test_signed_link_payload_with_unknown_field_is_refused_even_with_a_valid_mac():
    env, secrets = govkit.make_env(), govkit.TestSecrets()
    payload = {"a": govkit.OFFICER, "b": env.bundle.bundle_sha256, "d": "approve", "e": 2_000_000_000, "n": env.nonce, "x": "1"}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    payload_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
    mac = hmac.new(secrets.keys["k1"], f"agon-signed-link|v1|k1|{payload_b64}".encode("ascii"), hashlib.sha256).digest()
    token = f"v1.k1.{payload_b64}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode('ascii')}"
    assert signed_link.handle(token, env.store, govkit.later(5), secrets=secrets).code == "malformed_payload"


def test_signed_link_expiry_and_short_keys():
    env, secrets = govkit.make_env(), govkit.TestSecrets()
    token = _token(env, secrets, expires_at=govkit.later(60))
    assert signed_link.handle(token, env.store, govkit.later(60), secrets=secrets).code == "malformed_payload"
    secrets.keys["short"] = b"x" * 16
    result = signed_link.handle("v1.short.e30.AAAA", env.store, govkit.later(1), secrets=secrets)
    assert result.code == "malformed_payload" and "shorter" in result.detail


def test_signed_link_uses_a_constant_time_compare():
    tree = ast.parse(inspect.getsource(signed_link))
    calls = {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
    }
    assert "hmac.compare_digest" in calls


# ----------------------------------------------------------------- console


def test_console_takes_the_approver_from_the_session_not_the_body():
    env = govkit.make_env(target="sandbox")
    body = govkit.console_body(env)
    body["approver"] = govkit.OFFICER
    assert console.handle(body, env.store, govkit.later(1), principal=govkit.VISITOR).code == "malformed_payload"
    d = console.parse(govkit.console_body(env), principal=govkit.VISITOR, received_at=govkit.T0)
    assert d.approver == govkit.VISITOR and d.channel == "console"
    assert verifier.normalize_principal(d.approver) == govkit.VISITOR
