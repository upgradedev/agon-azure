"""Destinations: the offered set is sealed, the ticked subset is verified and recorded.

Owner decision 2026-10-19: the officer ticks AFTER the seal, so the sealed manifest carries the
OFFERED destinations (each with its payload sha256), the decision carries the ticked subset, the
verifier refuses anything outside the offered set and an approve with nothing ticked, and the
approval record stores the approved subset. Every refusal leaves the nonce unconsumed.
"""

from __future__ import annotations

import copy
import json

import pytest

import govkit
from agon.governance import destinations as D
from agon.governance import seal as S
from agon.governance import verifier
from agon.governance.channels import console, outlook, signed_link, teams
from agon.governance.verifier import APPROVALS, NONCES, ApprovalDecision, verify

NOW = govkit.later(30)

PINNED = (
    ("club_site", "Club website (CMS feed)"),
    ("feeds", "RSS and JSON Feed"),
    ("instagram", "Instagram"),
    ("youtube", "YouTube"),
    ("newsroom_teams", "Internal newsroom (Teams)"),
    ("journalist_email", "Journalist email digest"),
)


def test_destination_ids_labels_and_delivery_are_pinned():
    assert D.DESTINATIONS == PINNED
    assert D.DELIVERY == {
        "club_site": "written", "feeds": "written", "instagram": "preview", "youtube": "preview",
        "newsroom_teams": "outbox", "journalist_email": "outbox",
    }
    assert D.PREVIEW_LABEL == "Preview: not posted; posting needs Meta and Google app approval."
    assert verifier.AS_OFFERED_CHANNELS == frozenset({"outlook"})


def _status(env):
    return env.store.read(NONCES, env.nonce).body["status"]


def _decision(env, **over):
    base = dict(
        bundle_sha256=env.bundle.bundle_sha256, nonce=env.nonce, decision="approve",
        approver=govkit.OFFICER, channel="teams", received_at=govkit.T0,
    )
    base.update(over)
    return ApprovalDecision(**base)


# ----------------------------------------------------------------- seal


def test_the_manifest_offers_each_destination_with_its_payload_hash():
    sealed, files = govkit.make_bundle()
    table = sealed.manifest["destinations"]
    assert list(S.offered_destinations(sealed.manifest)) == list(D.DESTINATION_IDS)
    for dest_id, entry in table.items():
        name = D.artifact_name(dest_id)
        assert entry == {"artifact": name, "label": D.LABELS[dest_id], "sha256": S.sha256_hex(files[name])}
        art = sealed.manifest["artifacts"][name]
        assert art["role"] == "destination" and art["media_type"] == "application/json"
        assert json.loads(files[name])["destination"] == dest_id
    assert S.verify_manifest(sealed.manifest_bytes, files, expected_sha256=sealed.bundle_sha256).ok


def test_a_bundle_that_offers_nothing_has_no_destinations_key():
    sealed, files = govkit.make_bundle(destinations=())
    assert "destinations" not in sealed.manifest
    assert not any(D.artifact_name(d) in files for d in D.DESTINATION_IDS)
    assert S.offered_destinations(sealed.manifest) == ()


def test_offering_a_subset_seals_only_those_payloads():
    sealed, files = govkit.make_bundle(destinations=("instagram", "club_site"))
    assert S.offered_destinations(sealed.manifest) == ("club_site", "instagram")
    assert set(sealed.manifest["destinations"]) == {"club_site", "instagram"}


@pytest.mark.parametrize("payloads, problem", [
    ({"tiktok": b"{}"}, "unknown destination"),
    ({"instagram": b'{"schema":"agon.destination/1","destination":"youtube"}'}, "names destination"),
    ({"instagram": b"not json"}, "not UTF-8 JSON"),
    ({"instagram": b'{"destination":"instagram"}'}, "schema"),
])
def test_seal_refuses_a_bad_payload(payloads, problem):
    with pytest.raises(S.SealError, match=problem):
        S.seal(
            kind="fulltime", match_id="s9-m0", seed_hash="a" * 64, event_log_hash="b" * 64, charter_hash=None,
            artifacts=[S.Artifact("report-en.txt", b"x", "text/plain", "edition", "en")],
            gate_verdicts=govkit.PASS_VERDICTS, author_tags={}, attested=["report-en.txt"], gate_cleared=[],
            destinations=payloads,
        )


def test_seal_refuses_a_destination_artifact_passed_directly():
    with pytest.raises(S.SealError, match="destinations="):
        S.seal(
            kind="fulltime", match_id="s9-m0", seed_hash="a" * 64, event_log_hash="b" * 64, charter_hash=None,
            artifacts=[S.Artifact("destination-instagram.json", b"{}", "application/json", "destination")],
            gate_verdicts=govkit.PASS_VERDICTS, author_tags={}, attested=[], gate_cleared=[],
        )


def _tampered(mutate):
    sealed, files = govkit.make_bundle()
    manifest = copy.deepcopy(sealed.manifest)
    mutate(manifest)
    data = S.canonical_utf8(manifest)
    return S.verify_manifest(data, files, expected_sha256=S.sha256_hex(data))


@pytest.mark.parametrize("mutate, problem", [
    (lambda m: m["destinations"]["instagram"].update(label="Insta"), "label must be"),
    (lambda m: m["destinations"]["instagram"].update(sha256="0" * 64), "sha256 differs"),
    (lambda m: m["destinations"].pop("youtube"), "not offered in the destinations table"),
    (lambda m: m["destinations"].update(tiktok={"artifact": "x", "label": "x", "sha256": "0" * 64}), "unknown destination"),
    (lambda m: m.update(destinations={}), "non-empty map"),
    (lambda m: m.pop("destinations"), "with no destinations table"),
    (lambda m: m["destinations"]["feeds"].update(artifact="report-en.txt"), "must be the artifact"),
])
def test_a_hand_built_destinations_table_is_refused(mutate, problem):
    check = _tampered(mutate)
    assert not check.ok
    assert any(problem in r for r in check.reasons), check.reasons


def test_swapped_payload_bytes_fail_the_seal_check():
    sealed, files = govkit.make_bundle()
    swapped = dict(files, **{D.artifact_name("instagram"): files[D.artifact_name("youtube")]})
    check = S.verify_manifest(sealed.manifest_bytes, swapped, expected_sha256=sealed.bundle_sha256)
    assert not check.ok and any("instagram" in r for r in check.reasons)


# ----------------------------------------------------------------- verifier


def test_an_approval_records_the_ticked_subset_in_registry_order():
    env = govkit.make_env()
    result = verify(_decision(env, destinations=frozenset({"youtube", "club_site"})), env.store, NOW)
    assert result.approved
    record = env.store.read(APPROVALS, result.approval_id).body
    assert record["destinations"] == ["club_site", "youtube"]
    assert record["destinations_as_offered"] is False
    assert record["bundle_sha256"] == env.bundle.bundle_sha256


def test_a_destination_outside_the_offered_set_is_refused_without_burning_the_nonce():
    env = govkit.make_env(destinations=("club_site", "feeds"))
    for ticked in ({"instagram"}, {"club_site", "tiktok"}):
        result = verify(_decision(env, destinations=frozenset(ticked)), env.store, NOW)
        assert result.code == "destination_not_offered", result
        assert _status(env) == "pending"
    assert verify(_decision(env, destinations=frozenset({"feeds"})), env.store, NOW).approved


def test_an_approve_with_nothing_ticked_is_refused_without_burning_the_nonce():
    env = govkit.make_env()
    result = verify(_decision(env, destinations=frozenset()), env.store, NOW)
    assert result.code == "no_destination_selected"
    assert _status(env) == "pending" and env.store.keys(APPROVALS) == []


def test_only_outlook_may_approve_the_destinations_as_offered():
    env = govkit.make_env(destinations=("club_site", "journalist_email"))
    refused = verify(_decision(env, destinations_as_offered=True), env.store, NOW)
    assert refused.code == "destinations_not_ticked" and _status(env) == "pending"
    result = verify(_decision(env, channel="outlook", destinations_as_offered=True), env.store, NOW)
    assert result.approved
    record = env.store.read(APPROVALS, result.approval_id).body
    assert record["destinations"] == ["club_site", "journalist_email"] and record["destinations_as_offered"] is True


@pytest.mark.parametrize("over", [
    {"decision": "changes", "destinations": frozenset({"club_site"})},
    {"decision": "changes", "destinations_as_offered": True},
    {"destinations": frozenset({"club_site"}), "destinations_as_offered": True},
    {"destinations": {"club_site"}},
    {"destinations": frozenset({1})},
])
def test_malformed_destination_fields_are_refused(over):
    env = govkit.make_env()
    assert verify(_decision(env, **over), env.store, NOW).code == "malformed_decision"
    assert _status(env) == "pending"


def test_a_changes_decision_records_no_destinations():
    env = govkit.make_env()
    result = verify(_decision(env, decision="changes"), env.store, NOW)
    assert result.ok and result.code == "changes_requested"
    assert env.store.read(APPROVALS, result.approval_id).body["destinations"] == []


def test_a_bundle_that_offers_nothing_approves_with_no_destination_and_refuses_any():
    env = govkit.make_env(destinations=())
    assert verify(_decision(env, destinations=frozenset({"club_site"})), env.store, NOW).code == "destination_not_offered"
    result = verify(_decision(env), env.store, NOW)
    assert result.approved
    assert env.store.read(APPROVALS, result.approval_id).body["destinations"] == []


# ----------------------------------------------------------------- channels


def test_teams_reads_the_dest_toggles_and_ignores_them_on_changes():
    env = govkit.make_env()
    payload = govkit.teams_payload(env, destinations=("feeds", "instagram"))
    assert payload["data"]["dest:club_site"] == "false" and payload["data"]["dest:feeds"] == "true"
    decision = teams.parse(payload, NOW)
    assert decision.destinations == frozenset({"feeds", "instagram"}) and not decision.destinations_as_offered
    changes = teams.parse(govkit.teams_payload(env, decision="request_changes"), NOW)
    assert changes.decision == "changes" and changes.destinations == frozenset()
    result = teams.handle(payload, env.store, NOW)
    assert env.store.read(APPROVALS, result.approval_id).body["destinations"] == ["feeds", "instagram"]


def test_teams_with_every_toggle_off_is_refused():
    env = govkit.make_env()
    result = teams.handle(govkit.teams_payload(env, destinations=()), env.store, NOW)
    assert result.code == "no_destination_selected" and _status(env) == "pending"


def test_outlook_approves_as_offered_and_cannot_narrow_or_widen():
    env = govkit.make_env(destinations=("club_site", "feeds"))
    payload = dict(govkit.outlook_payload(env), destinations=["instagram"])
    decision = outlook.parse(payload, NOW)
    assert decision.destinations_as_offered and decision.destinations == frozenset()
    result = outlook.handle(payload, env.store, NOW)
    assert env.store.read(APPROVALS, result.approval_id).body["destinations"] == ["club_site", "feeds"]
    changes = outlook.parse(govkit.outlook_payload(env, option=outlook.OPTION_CHANGES), NOW)
    assert not changes.destinations_as_offered


def test_console_body_carries_the_ticked_chips():
    env = govkit.make_env(target="sandbox")
    body = govkit.console_body(env, destinations=("youtube",))
    result = console.handle(body, env.store, NOW, principal=govkit.VISITOR)
    assert env.store.read(APPROVALS, result.approval_id).body["destinations"] == ["youtube"]
    dropped = console.parse(govkit.console_body(env, decision="changes"), principal=govkit.VISITOR, received_at=NOW)
    assert dropped.destinations == frozenset()
    bad = console.handle(dict(body, destinations="youtube"), env.store, NOW, principal=govkit.VISITOR)
    assert bad.code == "malformed_payload"


def test_signed_link_takes_the_ticked_destinations_alongside_the_token():
    env, secrets = govkit.make_env(), govkit.TestSecrets()
    token = signed_link.issue_token(
        secrets=secrets, key_id="k1", approver=govkit.OFFICER, bundle_sha256=env.bundle.bundle_sha256,
        nonce=env.nonce, decision="approve", expires_at=govkit.later(900),
    )
    assert signed_link.handle(token, env.store, NOW, secrets=secrets).code == "no_destination_selected"
    assert signed_link.handle(token, env.store, NOW, secrets=secrets, destinations=["tiktok"]).code == "destination_not_offered"
    result = signed_link.handle(token, env.store, NOW, secrets=secrets, destinations=["journalist_email"])
    assert env.store.read(APPROVALS, result.approval_id).body["destinations"] == ["journalist_email"]
