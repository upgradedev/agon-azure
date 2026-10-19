"""Publisher: seal check first, approval read back from the store, create-only, errata supersede."""

from __future__ import annotations

import copy
import threading

import govkit
from agon.governance import publisher as P
from agon.governance import seal as S
from agon.governance import verifier
from agon.governance.channels import console, teams
from agon.governance.store import InMemoryStore


def _approved_env(**kwargs):
    env = govkit.make_env(**kwargs)
    result = teams.handle(govkit.teams_payload(env), env.store, govkit.later(30))
    assert result.approved, result
    return env, result.approval_id


def _publish(pub, env, approval_id, *, manifest=None, files=None, sha=None):
    return pub.publish(
        bundle_sha256=sha or env.bundle.bundle_sha256,
        approval_id=approval_id,
        manifest_bytes=manifest if manifest is not None else env.bundle.manifest_bytes,
        artifacts=files if files is not None else env.files,
        now=govkit.later(60),
    )


def test_publish_writes_every_artifact_and_a_receipt():
    env, approval_id = _approved_env()
    sink = P.InMemorySink()
    result = _publish(P.Publisher(env.store, sink, "feed"), env, approval_id)
    assert result.ok, result
    sha = env.bundle.bundle_sha256
    for name, data in env.files.items():
        assert sink.get(f"feed/{sha}/{name}") == data
    assert sink.get(f"feed/{sha}/manifest.json") == env.bundle.manifest_bytes
    receipt = env.store.read(P.PUBLISHED, sha).body
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    assert receipt["receipt_sha256"] == S.sha256_hex(S.canonical_utf8(body))
    assert receipt["approver"] == govkit.OFFICER and receipt["channel"] == "teams"
    assert receipt["match_id"] == env.bundle.manifest["match_id"] == "s9-m0"
    assert receipt["kind"] == "fulltime"


def test_refuses_when_the_recomputed_hash_differs_from_the_approved_hash():
    env, approval_id = _approved_env()
    pub = P.Publisher(env.store, P.InMemorySink(), "feed")
    tampered = dict(env.files, **{"report-en.txt": env.files["report-en.txt"] + b"!"})
    assert _publish(pub, env, approval_id, files=tampered).code == "seal_mismatch"
    other, other_files = govkit.make_bundle(tag=" v-other")
    wrong = _publish(pub, env, approval_id, manifest=other.manifest_bytes, files=other_files, sha=other.bundle_sha256)
    assert wrong.code == "approved_hash_mismatch"
    lying = _publish(pub, env, approval_id, manifest=other.manifest_bytes, files=other_files)
    assert lying.code == "seal_mismatch"


def test_refuses_without_an_approval_or_with_changes_requested():
    env = govkit.make_env()
    pub = P.Publisher(env.store, P.InMemorySink(), "feed")
    assert _publish(pub, env, "0" * 32).code == "no_approval"
    changes = teams.handle(govkit.teams_payload(env, decision="changes"), env.store, govkit.later(5))
    assert _publish(pub, env, changes.approval_id).code == "not_approved"


def test_refuses_a_sandbox_approval_on_the_feed_publisher():
    env = govkit.make_env(target="sandbox")
    result = console.handle(govkit.console_body(env), env.store, govkit.later(5), principal=govkit.VISITOR)
    assert result.approved
    assert _publish(P.Publisher(env.store, P.InMemorySink(), "feed"), env, result.approval_id).code == "wrong_target"
    assert _publish(P.Publisher(env.store, P.InMemorySink(), "sandbox"), env, result.approval_id).ok


def test_second_publish_of_the_same_bundle_is_refused():
    env, approval_id = _approved_env()
    pub = P.Publisher(env.store, P.InMemorySink(), "feed")
    assert _publish(pub, env, approval_id).ok
    assert _publish(pub, env, approval_id).code == "already_published"


def test_overwrite_of_different_bytes_is_refused_and_identical_bytes_resume():
    env, approval_id = _approved_env()
    sink = P.InMemorySink()
    sha = env.bundle.bundle_sha256
    sink.put_new(f"feed/{sha}/report-el.txt", b"someone else's bytes", "text/plain")
    result = _publish(P.Publisher(env.store, sink, "feed"), env, approval_id)
    assert result.code == "overwrite_refused"
    assert sink.get(f"feed/{sha}/report-el.txt") == b"someone else's bytes"
    assert env.store.read(P.PUBLISHED, sha) is None
    # Every path is checked before the first write: audio-el.mp3 sorts first and is NOT left behind.
    assert set(sink.blobs) == {f"feed/{sha}/report-el.txt"}

    env2, approval2 = _approved_env()
    sink2 = P.InMemorySink()
    sha2 = env2.bundle.bundle_sha256
    sink2.put_new(f"feed/{sha2}/report-el.txt", env2.files["report-el.txt"], "text/plain")  # half-finished run
    assert _publish(P.Publisher(env2.store, sink2, "feed"), env2, approval2).ok


def test_gate_refused_bundle_is_never_published_even_with_a_planted_approval():
    store = InMemoryStore()
    bad, files = govkit.make_bundle(verdicts={**govkit.PASS_VERDICTS, "rights": "refused"})
    verifier.register_sealed_bundle(store, bad.manifest_bytes)
    store.create(verifier.APPROVALS, "planted", {"decision": "approve", "bundle_sha256": bad.bundle_sha256, "target": "feed"})
    result = P.Publisher(store, P.InMemorySink(), "feed").publish(
        bundle_sha256=bad.bundle_sha256, approval_id="planted", manifest_bytes=bad.manifest_bytes, artifacts=files, now=govkit.T0
    )
    assert result.code == "bundle_refused_by_gate"


def test_erratum_supersedes_without_touching_the_original():
    env, approval_id = _approved_env()
    sink = P.InMemorySink()
    pub = P.Publisher(env.store, sink, "feed")
    assert _publish(pub, env, approval_id).ok
    v1 = env.bundle.bundle_sha256
    v1_receipt = copy.deepcopy(env.store.read(P.PUBLISHED, v1).body)
    v1_blobs = {k: v for k, v in sink.blobs.items() if k.startswith(f"feed/{v1}/")}

    v2_env = govkit.make_env(store=env.store, kind="erratum", parent=v1, tag=" corrected scorer")
    v2_ok = teams.handle(govkit.teams_payload(v2_env), env.store, govkit.later(90))
    assert v2_ok.approved
    assert _publish(pub, v2_env, v2_ok.approval_id).ok
    link = env.store.read(P.SUPERSESSIONS, v1).body
    assert link["parent"] == v1 and link["child"] == v2_env.bundle.bundle_sha256
    assert link["state"] == "published"
    assert env.store.read(P.PUBLISHED, v1).body == v1_receipt
    assert {k: v for k, v in sink.blobs.items() if k.startswith(f"feed/{v1}/")} == v1_blobs
    assert env.store.read(P.PUBLISHED, v2_env.bundle.bundle_sha256).body["parent_bundle_sha256"] == v1

    # A second erratum that names v1 again is refused: errata chain from the latest version.
    v3_env = govkit.make_env(store=env.store, kind="erratum", parent=v1, tag=" another fix")
    v3_ok = teams.handle(govkit.teams_payload(v3_env), env.store, govkit.later(120))
    assert _publish(pub, v3_env, v3_ok.approval_id).code == "parent_already_superseded"
    assert not any(k.startswith(f"feed/{v3_env.bundle.bundle_sha256}/") for k in sink.blobs)


def test_erratum_of_an_unpublished_parent_is_refused():
    env = govkit.make_env()
    orphan = govkit.make_env(store=env.store, kind="erratum", parent="e" * 64, tag=" orphan")
    ok = teams.handle(govkit.teams_payload(orphan), env.store, govkit.later(5))
    result = _publish(P.Publisher(env.store, P.InMemorySink(), "feed"), orphan, ok.approval_id)
    assert result.code == "parent_not_published"


def _hand_built(mutate):
    """A canonical manifest built by hand (not by seal()), approved by a planted record."""
    data, sha, files = govkit.hand_built(mutate)
    store = InMemoryStore()
    verifier.register_sealed_bundle(store, data)
    store.create(verifier.APPROVALS, "planted", {"decision": "approve", "bundle_sha256": sha, "target": "feed"})
    return store, data, sha, files


def _rename_to_traversal(manifest, files):
    manifest["artifacts"]["../escape.txt"] = manifest["artifacts"].pop("report-el.txt")
    manifest["attested_scope"]["gate_cleared"] = ["audio-el.mp3"]
    files["../escape.txt"] = files.pop("report-el.txt")
    return files


def _drop_media_type(manifest, files):
    del manifest["artifacts"]["audio-el.mp3"]["media_type"]
    return files


def test_hand_built_malformed_manifests_are_refused_with_nothing_written():
    for mutate in (_rename_to_traversal, _drop_media_type):
        store, data, sha, files = _hand_built(mutate)
        sink = P.InMemorySink()
        result = P.Publisher(store, sink, "feed").publish(
            bundle_sha256=sha, approval_id="planted", manifest_bytes=data, artifacts=files, now=govkit.T0
        )
        assert result.code == "seal_mismatch", (mutate.__name__, result)
        assert sink.blobs == {}
        assert store.keys(P.PUBLISHED) == []
        try:
            verifier.issue_nonce(store, sha, target="feed", now=govkit.T0)
        except verifier.IssueError:
            pass
        else:
            raise AssertionError(f"{mutate.__name__}: a nonce was issued for a malformed manifest")


def test_a_bundle_missing_the_rights_verdict_is_never_published_even_with_a_planted_approval():
    """Reviewer probe: a bundle that never ran the Rights Guard must not reach the public feed."""
    store, data, sha, files = _hand_built(govkit.drop_gate("rights"))
    sink = P.InMemorySink()
    result = P.Publisher(store, sink, "feed").publish(
        bundle_sha256=sha, approval_id="planted", manifest_bytes=data, artifacts=files, now=govkit.T0
    )
    assert result.code == "required_gate_missing" and "rights" in result.detail
    assert sink.blobs == {}
    assert store.keys(P.PUBLISHED) == []


def test_erratum_for_another_match_cannot_supersede_the_parent():
    """Reviewer probe: an erratum sealed for s11-m0 naming a published s9-m0 parent was published
    and created a supersession record. It is now refused and nothing is written."""
    env, approval_id = _approved_env()
    sink = P.InMemorySink()
    pub = P.Publisher(env.store, sink, "feed")
    assert _publish(pub, env, approval_id).ok
    parent = env.bundle.bundle_sha256

    other = govkit.make_env(store=env.store, kind="erratum", parent=parent, seed=11, tag=" wrong match")
    assert other.bundle.manifest["match_id"] == "s11-m0"
    ok = teams.handle(govkit.teams_payload(other), env.store, govkit.later(90))
    assert ok.approved
    before = dict(sink.blobs)
    result = _publish(pub, other, ok.approval_id)
    assert result.code == "parent_match_mismatch"
    assert env.store.read(P.SUPERSESSIONS, parent) is None
    assert env.store.read(P.PUBLISHED, other.bundle.bundle_sha256) is None
    assert sink.blobs == before


def test_parent_receipt_without_a_match_id_fails_closed():
    env, approval_id = _approved_env()
    store = env.store
    parent = env.bundle.bundle_sha256
    store.create(P.PUBLISHED, parent, {"bundle_sha256": parent, "target": "feed"})  # legacy shape, no match_id
    child = govkit.make_env(store=store, kind="erratum", parent=parent, tag=" fix")
    ok = teams.handle(govkit.teams_payload(child), store, govkit.later(90))
    assert _publish(P.Publisher(store, P.InMemorySink(), "feed"), child, ok.approval_id).code == "parent_match_mismatch"


def test_an_artifact_named_like_the_manifest_cannot_be_sealed():
    arts = [S.Artifact("manifest.json", b"{}", "application/json", "edition", "en")]
    try:
        S.seal(**{**_seal_inputs(), "artifacts": arts, "attested": [], "gate_cleared": []})
    except S.SealError as exc:
        assert "reserved" in str(exc)
    else:
        raise AssertionError("seal() accepted an artifact named manifest.json")


def _seal_inputs():
    return dict(
        kind="fulltime",
        match_id="s9-m0",
        seed_hash="a" * 64,
        event_log_hash="b" * 64,
        charter_hash=None,
        artifacts=[],
        gate_verdicts=dict(govkit.PASS_VERDICTS),
        author_tags={},
        attested=[],
        gate_cleared=[],
    )


def _rename_to_manifest(manifest, files):
    manifest["artifacts"]["manifest.json"] = manifest["artifacts"].pop("report-el.txt")
    manifest["attested_scope"]["gate_cleared"] = ["audio-el.mp3"]
    files["manifest.json"] = files.pop("report-el.txt")
    return files


def test_reviewer_probe_hand_built_manifest_json_artifact_writes_nothing():
    """Reviewer probe: an artifact named manifest.json was approved, then failed overwrite_refused
    with its bytes left at feed/<hash>/manifest.json. Now it is refused before anything is written."""
    store, data, sha, files = _hand_built(_rename_to_manifest)
    sink = P.InMemorySink()
    result = P.Publisher(store, sink, "feed").publish(
        bundle_sha256=sha, approval_id="planted", manifest_bytes=data, artifacts=files, now=govkit.T0
    )
    assert result.code == "seal_mismatch" and "reserved" in result.detail
    assert sink.blobs == {}
    try:
        verifier.issue_nonce(store, sha, target="feed", now=govkit.T0)
    except verifier.IssueError as exc:
        assert "reserved" in str(exc)
    else:
        raise AssertionError("a nonce was issued for a manifest with a reserved artifact name")


class _RaceStore(InMemoryStore):
    """Holds the first two readers of one parent's supersession record until both have read it,
    so both concurrent errata pass the pre-check and the race is settled by the store alone."""

    def __init__(self) -> None:
        super().__init__(after_read=self._hold)
        self.armed = False
        self._barrier = threading.Barrier(2, timeout=10)
        self._lock = threading.Lock()
        self._waiting = 0

    def _hold(self, container, key):
        if not self.armed or container != P.SUPERSESSIONS:
            return
        with self._lock:
            if self._waiting >= 2:
                return
            self._waiting += 1
        self._barrier.wait()


def test_concurrent_errata_of_one_parent_loser_writes_no_public_bytes():
    store = _RaceStore()
    env, approval_id = _approved_env(store=store)
    sink = P.InMemorySink()
    pub = P.Publisher(store, sink, "feed")
    assert _publish(pub, env, approval_id).ok
    parent = env.bundle.bundle_sha256
    a = govkit.make_env(store=store, kind="erratum", parent=parent, tag=" fix a")
    b = govkit.make_env(store=store, kind="erratum", parent=parent, tag=" fix b")
    ok_a = teams.handle(govkit.teams_payload(a), store, govkit.later(90))
    ok_b = teams.handle(govkit.teams_payload(b), store, govkit.later(91))
    assert ok_a.approved and ok_b.approved

    results = {}

    def run(label, e, approval):
        results[label] = _publish(pub, e, approval)

    store.armed = True
    threads = [threading.Thread(target=run, args=("a", a, ok_a.approval_id)), threading.Thread(target=run, args=("b", b, ok_b.approval_id))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    store.armed = False

    codes = sorted(r.code for r in results.values())
    assert codes == ["parent_already_superseded", "published"], codes
    winner_label = "a" if results["a"].ok else "b"
    winner, loser = (a, b) if winner_label == "a" else (b, a)
    assert not any(k.startswith(f"feed/{loser.bundle.bundle_sha256}/") for k in sink.blobs)
    assert sink.get(f"feed/{winner.bundle.bundle_sha256}/manifest.json") == winner.bundle.manifest_bytes
    link = store.read(P.SUPERSESSIONS, parent).body
    assert link["child"] == winner.bundle.bundle_sha256 and link["state"] == "published"
