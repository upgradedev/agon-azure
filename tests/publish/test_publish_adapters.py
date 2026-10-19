"""The Publisher with one adapter per destination: what lands where, and what is refused.

Covers the owner decision end to end on the in-memory store and sink: only the approved subset is
delivered, each adapter writes only under its own folder and refuses a destination the approval
record does not list, the feeds are new create-only versions behind a compare-and-set pointer,
errata supersede without touching the parent, and every refusal leaves nothing public behind.
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest

import govkit
from agon.governance import destinations as D
from agon.governance import publisher as P
from agon.governance import seal as S
from agon.governance import verifier
from agon.governance.channels import outlook, teams
from agon.governance.store import InMemoryStore
from agon.publish import adapters as A
from agon.publish.render import render_all

NOW = govkit.later(60)


def _approved(destinations=None, store=None, **bundle_kwargs):
    env = govkit.make_env(store=store, **bundle_kwargs)
    result = teams.handle(govkit.teams_payload(env, destinations=destinations), env.store, govkit.later(30))
    assert result.approved, result
    return env, result.approval_id


def _publish(pub, env, approval_id, now=NOW, files=None):
    return pub.publish(
        bundle_sha256=env.bundle.bundle_sha256,
        approval_id=approval_id,
        manifest_bytes=env.bundle.manifest_bytes,
        artifacts=env.files if files is None else files,
        now=now,
    )


def _under(sink, prefix):
    return sorted(k for k in sink.blobs if k.startswith(prefix))


# ----------------------------------------------------------------- the happy path


def test_all_six_destinations_each_get_their_files_and_a_receipt_entry():
    env, approval_id = _approved()
    sink = P.InMemorySink()
    result = _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id)
    assert result.ok, result
    sha = env.bundle.bundle_sha256
    for name, data in env.files.items():
        assert sink.get(f"feed/{sha}/{name}") == data
    assert sink.get(f"feed/site/{sha}/index.html").startswith(b"<!doctype html>")
    assert json.loads(sink.get(f"feed/site/{sha}/edition.json"))["edition"] == "en"
    assert sink.get(f"feed/previews/instagram/{sha}/payload.json") == env.files["destination-instagram.json"]
    assert sink.get(f"feed/previews/youtube/{sha}/payload.json") == env.files["destination-youtube.json"]
    for dest_id in ("newsroom_teams", "journalist_email"):
        assert sink.get(f"feed/outbox/{dest_id}/{sha}/payload.json") == env.files[f"destination-{dest_id}.json"]
        message = json.loads(sink.get(f"feed/outbox/{dest_id}/{sha}/message.json"))
        assert message["queue"] == A.OUTBOX_QUEUES[dest_id] and message["approval_id"] == approval_id
        assert message["payload_sha256"] == env.bundle.manifest["destinations"][dest_id]["sha256"]
        assert message["links"]["recap"] == f"/feed/site/{sha}/index.html"
    pointer = json.loads(sink.get("feed/feeds/latest.json"))
    assert pointer["latest_bundle_sha256"] == sha and pointer["items"] == 1

    receipt = env.store.read(P.PUBLISHED, sha).body
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    assert receipt["receipt_sha256"] == S.sha256_hex(S.canonical_utf8(body))
    assert receipt["destinations_offered"] == receipt["destinations_approved"] == list(D.DESTINATION_IDS)
    entries = receipt["destinations"]
    assert {k: v["delivery"] for k, v in entries.items()} == D.DELIVERY
    assert entries["instagram"]["note"] == D.PREVIEW_LABEL and entries["instagram"]["posted"] is False
    assert entries["newsroom_teams"]["note"] == D.OUTBOX_LABEL and entries["newsroom_teams"]["sent"] is False
    for dest_id, entry in entries.items():
        assert entry["label"] == D.LABELS[dest_id]
        assert entry["payload_sha256"] == env.bundle.manifest["destinations"][dest_id]["sha256"]
        prefix = f"feed/{A.default_adapters()[dest_id].prefix}/"
        assert entry["files"] and all(path.startswith(prefix) for path in entry["files"])
        for path, digest in entry["files"].items():
            assert S.sha256_hex(sink.get(path)) == digest


def test_only_the_ticked_subset_is_delivered_and_unticked_payloads_stay_out():
    env, approval_id = _approved(destinations=("club_site", "instagram"))
    sink = P.InMemorySink()
    assert _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id).ok
    sha = env.bundle.bundle_sha256
    assert _under(sink, "feed/feeds/") == [] and _under(sink, "feed/outbox/") == [] and _under(sink, "feed/previews/youtube/") == []
    assert sink.get(f"feed/site/{sha}/index.html") is not None
    assert sink.get(f"feed/previews/instagram/{sha}/payload.json") is not None
    archived = {k.rsplit("/", 1)[1] for k in _under(sink, f"feed/{sha}/")}
    assert "destination-youtube.json" not in archived and "destination-club_site.json" in archived
    receipt = env.store.read(P.PUBLISHED, sha).body
    assert receipt["destinations_approved"] == ["club_site", "instagram"]
    assert set(receipt["destinations"]) == {"club_site", "instagram"}
    assert "destination-feeds.json" not in receipt["files"]


def test_an_outlook_approval_delivers_every_offered_destination():
    env = govkit.make_env(destinations=("feeds", "journalist_email"))
    result = outlook.handle(govkit.outlook_payload(env), env.store, govkit.later(30))
    sink = P.InMemorySink()
    assert _publish(govkit.publisher(env.store, sink, "feed"), env, result.approval_id).ok
    assert env.store.read(P.PUBLISHED, env.bundle.bundle_sha256).body["destinations_approved"] == ["feeds", "journalist_email"]


def test_a_bundle_that_offers_nothing_publishes_the_sealed_bundle_only():
    env = govkit.make_env(destinations=())
    result = teams.handle(govkit.teams_payload(env), env.store, govkit.later(30))
    sink = P.InMemorySink()
    assert _publish(govkit.publisher(env.store, sink, "feed"), env, result.approval_id).ok
    sha = env.bundle.bundle_sha256
    assert all(k.startswith(f"feed/{sha}/") for k in sink.blobs)
    assert env.store.read(P.PUBLISHED, sha).body["destinations"] == {}


# ----------------------------------------------------------------- refusals


def _planted(store, sha, destinations, target="feed"):
    store.create(verifier.APPROVALS, "p" * 32, {
        "decision": "approve", "bundle_sha256": sha, "target": target, "destinations": destinations,
        "approver": govkit.OFFICER, "channel": "teams", "verified_at": govkit.later(30).isoformat(),
    })
    return "p" * 32


@pytest.mark.parametrize("listed, code", [
    (["club_site", "tiktok"], "approval_destination_not_offered"),
    ([], "no_destination_approved"),
    ("club_site", "approval_invalid"),
    (["club_site", "club_site"], "approval_invalid"),
])
def test_the_publisher_refuses_an_approval_record_it_cannot_honour(listed, code):
    store = InMemoryStore()
    sealed, files = govkit.make_bundle(destinations=("club_site", "feeds"))
    verifier.register_sealed_bundle(store, sealed.manifest_bytes)
    approval_id = _planted(store, sealed.bundle_sha256, listed)
    sink = P.InMemorySink()
    result = govkit.publisher(store, sink, "feed").publish(
        bundle_sha256=sealed.bundle_sha256, approval_id=approval_id, manifest_bytes=sealed.manifest_bytes, artifacts=files, now=NOW,
    )
    assert result.code == code, result
    assert sink.blobs == {} and store.read(P.PUBLISHED, sealed.bundle_sha256) is None


def test_the_publisher_refuses_an_approved_destination_with_no_adapter():
    env, approval_id = _approved(destinations=("club_site", "youtube"))
    sink = P.InMemorySink()
    adapters = {k: v for k, v in A.default_adapters().items() if k != "youtube"}
    result = _publish(P.Publisher(env.store, sink, "feed", adapters), env, approval_id)
    assert result.code == "no_adapter" and sink.blobs == {}


def test_an_adapter_refuses_a_destination_the_approval_record_does_not_list():
    env, approval_id = _approved(destinations=("club_site",))
    approval = env.store.read(verifier.APPROVALS, approval_id).body
    ctx = P.DeliveryContext(
        target="feed", bundle_sha256=env.bundle.bundle_sha256, approval_id=approval_id,
        manifest=env.bundle.manifest, approval=approval, published_at=NOW.isoformat(), now=NOW,
        approved_destinations=("club_site",),
    )
    reader = P._ReadOnly(P.InMemorySink())
    for dest_id, adapter in A.default_adapters().items():
        payload = env.files[D.artifact_name(dest_id)]
        if dest_id == "club_site":
            assert adapter.plan(ctx, payload, reader).destination == "club_site"
            continue
        with pytest.raises(P.DestinationRefused) as err:
            adapter.plan(ctx, payload, reader)
        assert err.value.code == "destination_not_approved"


def test_negative_control_a_rogue_adapter_writing_outside_its_folder_is_refused():
    class Rogue(A.ClubSiteAdapter):
        def plan(self, ctx, payload, reader):
            plan = super().plan(ctx, payload, reader)
            return P.DeliveryPlan(plan.destination, (*plan.files, P.PlannedFile("feed/feeds/latest.json", b"{}", "application/json")), plan.entry)

    env, approval_id = _approved(destinations=("club_site",))
    sink = P.InMemorySink()
    adapters = dict(A.default_adapters(), club_site=Rogue())
    result = _publish(P.Publisher(env.store, sink, "feed", adapters), env, approval_id)
    assert result.code == "adapter_path_outside_prefix" and sink.blobs == {}


def test_negative_control_an_adapter_registered_under_another_id_is_refused():
    with pytest.raises(ValueError):
        P.Publisher(InMemoryStore(), P.InMemorySink(), "feed", {"feeds": A.ClubSiteAdapter()})


def test_an_instagram_payload_over_the_platform_limit_is_refused_at_publish():
    """The preview adapter re-checks the published limits; a hand-sealed payload cannot slip past."""
    store = InMemoryStore()
    govkit.grant_default_roles(store)
    _, result, facts = govkit.match()
    good = json.loads(render_all(govkit.report_view(facts, result.meta["match_id"]))["instagram"])
    good["caption"] = "x" * 2201
    sealed, files = govkit.make_bundle(destinations=())
    data = S.canonical_utf8(good)
    resealed = S.seal(
        kind="fulltime", match_id=sealed.manifest["match_id"], seed_hash=sealed.manifest["seed_sha256"],
        event_log_hash=sealed.manifest["event_log_sha256"], charter_hash=sealed.manifest["charter_sha256"],
        artifacts=[S.Artifact(n, files[n], e["media_type"], e["role"], e.get("language")) for n, e in sealed.manifest["artifacts"].items()],
        gate_verdicts=govkit.PASS_VERDICTS, author_tags=sealed.manifest["author_tags"],
        attested=sealed.manifest["attested_scope"]["attested"], gate_cleared=sealed.manifest["attested_scope"]["gate_cleared"],
        destinations={"instagram": data},
    )
    files = dict(files, **{"destination-instagram.json": data})
    verifier.register_sealed_bundle(store, resealed.manifest_bytes)
    nonce = verifier.issue_nonce(store, resealed.bundle_sha256, target="feed", now=govkit.T0)
    env = govkit.Env(store=store, bundle=resealed, files=files, nonce=nonce)
    approval = teams.handle(govkit.teams_payload(env), store, govkit.later(30))
    sink = P.InMemorySink()
    out = _publish(govkit.publisher(store, sink, "feed"), env, approval.approval_id)
    assert out.code == "platform_limit" and "2201 characters" in out.detail and sink.blobs == {}


# ----------------------------------------------------------------- feeds


def _feed(sink):
    pointer = json.loads(sink.get("feed/feeds/latest.json"))
    feed = json.loads(sink.get(f"feed/{pointer['feed_json']}"))
    rss = ET.fromstring(sink.get(f"feed/{pointer['rss_xml']}"))
    return pointer, feed, rss


def test_each_publish_writes_a_new_feed_version_and_moves_the_pointer():
    store = InMemoryStore()
    sink = P.InMemorySink()
    pub = govkit.publisher(store, sink, "feed")
    first, a1 = _approved(store=store, tag=" one")
    assert _publish(pub, first, a1).ok
    v1, feed1, _ = _feed(sink)
    v1_bytes = sink.get(f"feed/{v1['feed_json']}")
    second, a2 = _approved(store=store, tag=" two", seed=3)
    assert _publish(pub, second, a2, now=NOW + timedelta(minutes=5)).ok
    v2, feed2, rss2 = _feed(sink)
    assert v2["version"] != v1["version"] and v2["items"] == 2
    assert sink.get(f"feed/{v1['feed_json']}") == v1_bytes  # the old version is never touched
    assert feed2["version"] == "https://jsonfeed.org/version/1.1"
    assert [i["_agon"]["bundle_sha256"] for i in feed2["items"]] == [second.bundle.bundle_sha256, first.bundle.bundle_sha256]
    assert [g.text for g in rss2.iter("guid")] == [i["id"] for i in feed2["items"]]
    assert rss2.get("version") == "2.0" and rss2.find("channel/title").text == A.FEED_TITLE
    assert feed2["items"][0]["url"] == f"/feed/site/{second.bundle.bundle_sha256}/index.html"


def test_a_feed_item_links_the_site_page_only_when_club_site_was_approved():
    env, approval_id = _approved(destinations=("feeds",))
    sink = P.InMemorySink()
    assert _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id).ok
    _, feed, rss = _feed(sink)
    assert "url" not in feed["items"][0] and rss.find("channel/item/link") is None


class FlakyPointerSink(P.InMemorySink):
    """Another publisher moves the pointer between this publish's plan and its pointer write."""

    def __init__(self, conflicts: int) -> None:
        super().__init__()
        self.conflicts = conflicts

    def write_pointer(self, path, data, media_type, etag):
        if self.conflicts:
            self.conflicts -= 1
            raise P.PointerConflict(path)
        super().write_pointer(path, data, media_type, etag)


def test_a_lost_pointer_race_is_planned_again_and_lands():
    env, approval_id = _approved()
    sink = FlakyPointerSink(conflicts=1)
    assert _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id).ok
    assert json.loads(sink.get("feed/feeds/latest.json"))["latest_bundle_sha256"] == env.bundle.bundle_sha256


def test_a_pointer_that_keeps_moving_stops_before_the_receipt_and_is_retryable():
    env, approval_id = _approved()
    sink = FlakyPointerSink(conflicts=P.POINTER_ATTEMPTS)
    result = _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id)
    assert result.code == "pointer_conflict"
    assert env.store.read(P.PUBLISHED, env.bundle.bundle_sha256) is None
    retry = _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id, now=NOW + timedelta(minutes=3))
    assert retry.ok, retry


class DiesAfter(P.InMemorySink):
    def __init__(self, writes: int) -> None:
        super().__init__()
        self.left = writes

    def put_new(self, path, data, media_type):
        if self.left == 0:
            raise ConnectionError("storage went away")
        self.left -= 1
        super().put_new(path, data, media_type)


def test_a_publish_that_died_half_way_resumes_later_with_identical_bytes():
    env, approval_id = _approved()
    sink = DiesAfter(writes=18)  # dies after the newsroom message.json, which carries a date
    with pytest.raises(ConnectionError):
        _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id)
    sink.left = 10_000
    later = _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id, now=NOW + timedelta(hours=1))
    assert later.ok, later  # files are dated with the approval time, so the retry plans the same bytes


def test_a_retry_after_the_pointer_already_moved_does_not_list_the_item_twice():
    env, approval_id = _approved()
    sink = P.InMemorySink()
    pub = govkit.publisher(env.store, sink, "feed")
    original = pub.store.create

    def fail_receipt(container, key, body):
        if container == P.PUBLISHED:
            raise ConnectionError("cosmos went away")
        return original(container, key, body)

    pub.store.create = fail_receipt  # type: ignore[method-assign]
    with pytest.raises(ConnectionError):
        _publish(pub, env, approval_id)
    pub.store.create = original  # type: ignore[method-assign]
    assert _publish(govkit.publisher(env.store, sink, "feed"), env, approval_id).ok
    _, feed, _ = _feed(sink)
    assert [i["_agon"]["bundle_sha256"] for i in feed["items"]] == [env.bundle.bundle_sha256]
    entry = env.store.read(P.PUBLISHED, env.bundle.bundle_sha256).body["destinations"]["feeds"]
    assert entry.get("already_listed") is True


# ----------------------------------------------------------------- errata


def test_an_erratum_supersedes_on_the_site_and_the_feeds_without_touching_the_parent():
    env, approval_id = _approved()
    sink = P.InMemorySink()
    pub = govkit.publisher(env.store, sink, "feed")
    assert _publish(pub, env, approval_id).ok
    parent = env.bundle.bundle_sha256
    parent_page = sink.get(f"feed/site/{parent}/index.html")
    v1, _, _ = _feed(sink)
    v1_bytes = sink.get(f"feed/{v1['feed_json']}")

    fix, fix_approval = _approved(store=env.store, kind="erratum", parent=parent, tag=" corrected")
    assert _publish(pub, fix, fix_approval, now=NOW + timedelta(minutes=10)).ok
    child = fix.bundle.bundle_sha256
    assert sink.get(f"feed/site/{parent}/index.html") == parent_page
    marker = json.loads(sink.get(f"feed/site/{parent}/superseded-by.json"))
    assert marker["superseded_bundle_sha256"] == parent and marker["by_bundle_sha256"] == child
    assert b"replaces an earlier version" in sink.get(f"feed/site/{child}/index.html")
    assert sink.get(f"feed/{v1['feed_json']}") == v1_bytes
    _, feed, _ = _feed(sink)
    newest, older = feed["items"]
    assert newest["_agon"]["bundle_sha256"] == child and newest["_agon"]["supersedes_bundle_sha256"] == parent
    assert older["title"].startswith(A.SUPERSEDED_PREFIX) and older["_agon"]["superseded_by_bundle_sha256"] == child
    reply = json.loads(sink.get(f"feed/outbox/newsroom_teams/{child}/payload.json"))
    assert reply["mode"] == "reply" and reply["reply_to_bundle_sha256"] == parent
    assert json.loads(sink.get(f"feed/outbox/newsroom_teams/{child}/message.json"))["parent_bundle_sha256"] == parent
    assert json.loads(sink.get(f"feed/previews/instagram/{child}/payload.json"))["mode"] == "edit_caption"
    assert json.loads(sink.get(f"feed/previews/youtube/{child}/payload.json"))["mode"] == "edit_metadata"


def test_the_sandbox_publisher_writes_the_sandbox_container_only():
    env = govkit.make_env(target="sandbox")
    from agon.governance.channels import console

    result = console.handle(govkit.console_body(env, destinations=("club_site", "feeds")), env.store, govkit.later(30), principal=govkit.VISITOR)
    sink = P.InMemorySink()
    assert _publish(govkit.publisher(env.store, sink, "sandbox"), env, result.approval_id).ok
    assert sink.blobs and all(k.startswith("sandbox/") for k in sink.blobs)
    assert _publish(govkit.publisher(env.store, P.InMemorySink(), "feed"), env, result.approval_id).code == "wrong_target"


def test_planning_never_writes():
    env, approval_id = _approved()
    approval = env.store.read(verifier.APPROVALS, approval_id).body
    ctx = P.DeliveryContext(
        target="feed", bundle_sha256=env.bundle.bundle_sha256, approval_id=approval_id, manifest=env.bundle.manifest,
        approval=approval, published_at=NOW.isoformat(), now=NOW, approved_destinations=tuple(D.DESTINATION_IDS),
    )
    sink = P.InMemorySink()
    before = copy.deepcopy(sink.blobs)
    for dest_id, adapter in A.default_adapters().items():
        adapter.plan(ctx, env.files[D.artifact_name(dest_id)], P._ReadOnly(sink))
    assert sink.blobs == before == {}
    assert not hasattr(P._ReadOnly(sink), "put_new") and not hasattr(P._ReadOnly(sink), "write_pointer")
