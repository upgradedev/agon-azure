"""M16: approval routes that skip the verifier (target 0).

Static: every file in governance/channels, selected by path, passes `routes.audit_channels`.
Dynamic: `verifier.verify` is replaced by a spy; every adapter must call it exactly once for a
valid payload and return exactly what it returned. Negative control: rogue adapters written to a
temporary folder must each be flagged.

Scope: no HTTP router exists yet, so today the count covers the four channel adapters only. When
the API router lands, this test must enumerate its routes by path as well.
"""

from __future__ import annotations

import shutil
import textwrap

import pytest

import govkit
from agon.governance import routes, verifier
from agon.governance.channels import console, outlook, signed_link, teams
from agon.governance.verifier import NONCES, VerifyResult

NOW = govkit.later(30)
SECRETS = govkit.TestSecrets()


def _signed(env):
    token = signed_link.issue_token(
        secrets=SECRETS,
        key_id="k1",
        approver=govkit.OFFICER,
        bundle_sha256=env.bundle.bundle_sha256,
        nonce=env.nonce,
        decision="approve",
        expires_at=govkit.later(900),
    )
    return signed_link.handle(token, env.store, NOW, secrets=SECRETS)


# One valid call per adapter. A new adapter without a case here fails test_every_adapter_has_a_case.
CASES = {
    "teams": (teams, "feed", lambda env: teams.handle(govkit.teams_payload(env), env.store, NOW)),
    "outlook": (outlook, "feed", lambda env: outlook.handle(govkit.outlook_payload(env), env.store, NOW)),
    "signed_link": (signed_link, "feed", _signed),
    "console": (console, "sandbox", lambda env: console.handle(govkit.console_body(env), env.store, NOW, principal=govkit.VISITOR)),
}


def test_static_audit_scans_every_channel_file_and_finds_no_bypass(capsys):
    report = routes.audit_channels()
    assert report.ok, report.problems
    assert len(report.scanned) >= len(verifier.CHANNELS) + 1, report.scanned
    assert set(report.adapters) == set(verifier.CHANNELS)
    assert routes.main([]) == 0
    out = capsys.readouterr().out
    assert f"channel files scanned: {len(report.scanned)}" in out
    assert "routes that skip verify(): 0" in out


def test_every_adapter_has_a_case():
    assert set(CASES) == set(routes.audit_channels().adapters) == set(verifier.CHANNELS)


@pytest.mark.parametrize("name", sorted(CASES))
def test_adapter_returns_only_what_verify_returns(name, monkeypatch):
    module, target, call = CASES[name]
    env = govkit.make_env(target=target)
    calls = []
    sentinel = VerifyResult(ok=False, code="spy_refusal", detail="spy")

    def spy(decision, store, now):
        calls.append(decision)
        return sentinel

    monkeypatch.setattr(verifier, "verify", spy)
    result = call(env)
    assert result is sentinel
    assert len(calls) == 1
    assert calls[0].channel == module.CHANNEL == name
    assert calls[0].nonce == env.nonce and calls[0].bundle_sha256 == env.bundle.bundle_sha256
    assert env.store.read(NONCES, env.nonce).body["status"] == "pending"
    assert env.store.keys(verifier.APPROVALS) == []


@pytest.mark.parametrize("name", sorted(CASES))
def test_adapter_cannot_approve_when_verify_refuses(name, monkeypatch):
    _, target, call = CASES[name]
    env = govkit.make_env(target=target)
    monkeypatch.setattr(verifier, "verify", lambda d, s, n: VerifyResult(ok=False, code="no", detail="no"))
    assert not call(env).ok


@pytest.mark.parametrize("name", sorted(CASES))
def test_adapter_approves_through_the_real_verifier(name):
    _, target, call = CASES[name]
    env = govkit.make_env(target=target)
    assert call(env).approved
    assert env.store.read(NONCES, env.nonce).body["status"] == "consumed"


ROGUES = {
    "rogue_ok.py": """
        from agon.governance import verifier
        CHANNEL = "teams"
        def handle(payload, store, now):
            return verifier.VerifyResult(ok=True, code="approved", detail="self-approved")
    """,
    "rogue_direct_import.py": """
        from agon.governance import verifier
        from agon.governance.verifier import verify
        CHANNEL = "teams"
        def handle(payload, store, now):
            verifier.verify(payload, store, now)
            return verify(payload, store, now)
    """,
    "rogue_store_write.py": """
        from agon.governance import verifier
        CHANNEL = "outlook"
        def handle(payload, store, now):
            store.replace("nonces", payload["nonce"], {"status": "consumed"}, if_match="*")
            return verifier.verify(payload, store, now)
    """,
    "rogue_dynamic.py": """
        from agon.governance import verifier
        CHANNEL = "console"
        def handle(payload, store, now):
            return getattr(verifier, "ver" + "ify")(payload, store, now)
    """,
    "rogue_other_function.py": """
        from agon.governance import verifier
        CHANNEL = "signed_link"
        def _check(payload, store, now):
            return verifier.verify(payload, store, now)
        def handle(payload, store, now):
            verifier.issue_nonce(store, payload["b"], target="feed", now=now)
            return None
    """,
    "rogue_unknown_channel.py": """
        from agon.governance import verifier
        CHANNEL = "sms"
        def handle(payload, store, now):
            return verifier.verify(payload, store, now)
    """,
}


def test_negative_control_rogue_adapters_are_flagged(tmp_path):
    for name, source in ROGUES.items():
        (tmp_path / name).write_text(textwrap.dedent(source), encoding="utf-8")
    shutil.copy(teams.__file__, tmp_path / "teams.py")  # a legitimate adapter alongside them
    report = routes.audit_channels(tmp_path)
    assert not report.ok
    for name in ROGUES:
        assert any(p.startswith(name + ":") for p in report.problems), (name, report.problems)
    assert not any(p.startswith("teams.py:") for p in report.problems), report.problems
    assert routes.main(["--dir", str(tmp_path)]) == 1


def test_negative_control_an_empty_folder_fails(tmp_path):
    report = routes.audit_channels(tmp_path)
    assert not report.ok and report.problems
