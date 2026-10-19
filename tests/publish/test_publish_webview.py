"""The `destinations` key of the agon.web-run/1 run format: built from the receipt, checked by webrun.

Lane D4 (the React console) reads exactly this key; the statuses are "delivered",
"preview_not_posted", "not_selected" and "not_offered". Each honesty rule is broken once here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import govkit
from agon import webrun
from agon.governance import destinations as D
from agon.governance import publisher as P
from agon.governance import verifier
from agon.governance.channels import teams
from agon.publish import webview as W

NOW = govkit.later(60)
RUNS = Path(__file__).resolve().parents[2] / "web" / "runs"


def _payloads(env):
    return {d: env.files[D.artifact_name(d)] for d in govkit.offered(env)}


def _published(destinations=("club_site", "instagram", "newsroom_teams"), offered=("club_site", "feeds", "instagram", "newsroom_teams")):
    env = govkit.make_env(destinations=offered)
    result = teams.handle(govkit.teams_payload(env, destinations=destinations), env.store, govkit.later(30))
    publish = govkit.publisher(env.store, P.InMemorySink(), "feed").publish(
        bundle_sha256=env.bundle.bundle_sha256, approval_id=result.approval_id,
        manifest_bytes=env.bundle.manifest_bytes, artifacts=env.files, now=NOW,
    )
    assert publish.ok
    approval = env.store.read(verifier.APPROVALS, result.approval_id).body
    return env, approval, publish.receipt


def test_a_published_run_reports_every_destination_honestly():
    env, approval, receipt = _published()
    rows = W.destinations_for_run(env.bundle.manifest, _payloads(env), approval=approval, receipt=receipt)
    assert [r["id"] for r in rows] == list(D.DESTINATION_IDS)
    status = {r["id"]: r["status"] for r in rows}
    assert status == {
        "club_site": "delivered", "feeds": "not_selected", "instagram": "preview_not_posted",
        "youtube": "not_offered", "newsroom_teams": "preview_not_posted", "journalist_email": "not_offered",
    }
    by_id = {r["id"]: r for r in rows}
    assert by_id["instagram"]["preview"]["note"] == D.PREVIEW_LABEL
    assert by_id["instagram"]["preview"]["text"] == json.loads(env.files["destination-instagram.json"])["caption"]
    assert by_id["newsroom_teams"]["preview"]["note"] == D.OUTBOX_LABEL
    assert by_id["club_site"]["preview"]["path"] == f"feed/site/{env.bundle.bundle_sha256}/index.html"
    assert by_id["youtube"]["preview"] is None and by_id["feeds"]["preview"]["path"] is None
    assert W.destination_problems(rows, approved=True, state="PUBLISHED") == []


def test_an_approved_run_not_yet_published_does_not_claim_delivery():
    env, approval, _ = _published()
    rows = W.destinations_for_run(env.bundle.manifest, _payloads(env), approval=approval, receipt=None)
    club = rows[0]
    assert club["status"] == "preview_not_posted" and club["preview"]["note"] == W.WAITING_NOTE
    assert W.destination_problems(rows, approved=True, state="APPROVED") == []


def test_a_run_awaiting_approval_lists_offered_destinations_as_not_selected():
    env = govkit.make_env()
    rows = W.destinations_for_run(env.bundle.manifest, _payloads(env))
    assert {r["status"] for r in rows} == {"not_selected"}
    assert W.destination_problems(rows, approved=False, state="AWAITING_APPROVAL") == []


def _rows():
    env, approval, receipt = _published()
    return W.destinations_for_run(env.bundle.manifest, _payloads(env), approval=approval, receipt=receipt)


@pytest.mark.parametrize("mutate, kwargs, problem", [
    (lambda r: None, {"approved": True, "state": "APPROVED"}, "delivered means written bytes on a PUBLISHED run"),
    (lambda r: None, {"approved": False, "state": "AWAITING_APPROVAL"}, "not approved has no delivered"),
    (lambda r: r[2]["preview"].update(note="Posted to Instagram."), {}, "note must say"),
    (lambda r: r[4]["preview"].update(note=D.PREVIEW_LABEL), {}, "note must say"),
    (lambda r: r.reverse(), {}, "registry order"),
    (lambda r: r.pop(), {}, "must list all 6"),
    (lambda r: r[0].update(label="Website"), {}, "label must be"),
    (lambda r: r[3].update(preview={"kind": "preview"}), {}, "preview is null exactly when"),
    (lambda r: r[1].update(preview=None), {}, "preview is null exactly when"),
    (lambda r: r[0].update(status="posted"), {}, "status must be one of"),
    (lambda r: r[2]["preview"].update(kind="written"), {}, "preview.kind must be preview"),
    (lambda r: r[0]["preview"].update(payload_sha256="x"), {}, "payload_sha256"),
])
def test_negative_control_each_destination_rule_fails_once(mutate, kwargs, problem):
    rows = copy.deepcopy(_rows())
    args = {"approved": True, "state": "PUBLISHED", **kwargs}
    mutate(rows)
    problems = W.destination_problems(rows, **args)
    assert any(problem in p for p in problems), problems


def test_webrun_accepts_the_key_and_runs_its_rules():
    run = json.loads((RUNS / "sample-s1.json").read_bytes().decode("utf-8"))
    assert "destinations" not in run and webrun.validate_run(run) == []
    env = govkit.make_env()
    with_key = dict(run, destinations=W.destinations_for_run(env.bundle.manifest, _payloads(env)))
    assert webrun.validate_run(with_key) == []
    delivered = copy.deepcopy(with_key)
    delivered["destinations"][0]["status"] = "delivered"
    assert any("not approved" in p for p in webrun.validate_run(delivered))
    assert webrun.validate_run(dict(run, live=True))[0].startswith("run keys differ")
