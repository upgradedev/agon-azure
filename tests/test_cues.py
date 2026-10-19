"""The live cue engine (agon.cues): nothing without an approved charter, every cue under its hash.

The export is driven end to end offline: W-charter's steps seal a charter, the one verifier records
the officer's approval, the Publisher writes the feed archive into memory, and agon.cues reads it
back by its hash from a copy of the container on disk, exactly as it reads the public feed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agon import charter as C
from agon import cues as Q
from agon import webrecord
from agon.fleet.sources import SynthMatchSource
from agon.governance import verifier
from agon.governance.publisher import InMemorySink, Publisher
from agon.interpret.features import pass_difficulty, pass_distance_m, xg_ppm
from agon.interpret.model import load_model
from agon.publish.adapters import default_adapters
from agon.synth.schema import make_event

ROOT = Path(__file__).resolve().parents[1]
CUES_DIR = ROOT / "web" / "cues"
SCHEMA = CUES_DIR / Q.SCHEMA_FILE
SOURCE = SynthMatchSource()
APPROVED_HASH = "ab" * 32
APPROVED_AT = "2026-10-21T10:00:00Z"
OFFICER = "officer@media.example"


def _record(seed: int = 1):
    return SOURCE.load({"source": "synth", "seed": seed, "match_index": 0})


def _approved(seed: int = 1) -> Q.ApprovedCharter:
    return Q.ApprovedCharter(C.build_charter(_record(seed)), APPROVED_HASH, APPROVED_AT)


def _cues(seed: int = 1, kinematics=None, events=None):
    rec = _record(seed)
    return Q.generate(events if events is not None else rec.events, rec.meta["home"], rec.meta["away"], _approved(seed), kinematics=kinematics)


def _doc(seed: int = 1, kinematics=None):
    rec = _record(seed)
    cues, held = _cues(seed, kinematics)
    return Q.document(
        seed=seed, match_index=0, match_id=rec.match_id, generator=rec.meta["generator"], approved=_approved(seed),
        cues=cues, held=held, kinematics_source=None if kinematics is None else kinematics["source"],
    )


# ----------------------------------------------------------------- no charter, no cue


def test_no_approved_charter_means_no_cue_at_all():
    rec = _record()
    assert Q.generate(rec.events, rec.meta["home"], rec.meta["away"], None) == ([], [])


def test_an_unapproved_or_foreign_charter_is_refused():
    rec = _record()
    with pytest.raises(Q.CueError, match="64 lowercase hex"):
        Q.generate(rec.events, rec.meta["home"], rec.meta["away"], Q.ApprovedCharter(_approved().charter, "", APPROVED_AT))
    with pytest.raises(Q.CueError, match="approval time"):
        Q.generate(rec.events, rec.meta["home"], rec.meta["away"], Q.ApprovedCharter(_approved().charter, APPROVED_HASH, "yesterday"))
    with pytest.raises(Q.CueError, match="another match"):
        Q.generate(rec.events, rec.meta["home"], rec.meta["away"], _approved(2))


# ----------------------------------------------------------------- every cue under the approved rules


def test_every_cue_carries_the_charter_hash_its_template_and_its_source_events():
    rec = _record()
    cues, held = _cues()
    assert cues and held == []
    ids = {e.event_id for e in rec.events}
    charter = _approved().charter
    for cue in cues:
        assert cue["charter_sha256"] == APPROVED_HASH
        assert cue["template_id"] in charter["templates"]
        assert cue["overlay"] == charter["templates"][cue["template_id"]]["overlay"]
        assert cue["source_event_ids"] and set(cue["source_event_ids"]) <= ids
        assert set(cue["text"]) == {"en", "el", "es", "fr"}
        assert cue["window"]["out_ms"] - cue["window"]["in_ms"] == charter["templates"][cue["template_id"]]["duration_ms"]
    keys = [(c["clock"]["period"], c["clock"]["ms"]) for c in cues]
    assert keys == sorted(keys)
    assert len({c["cue_id"] for c in cues}) == len(cues)


def test_each_caption_is_exactly_its_charter_template_filled():
    """No word in a cue that the approved template did not hold: strip every slot value, the template remains."""
    charter = _approved().charter
    for cue in _cues(9)[0]:
        for lang, text in cue["text"].items():
            template = charter["templates"][cue["template_id"]]["text"][lang]
            pattern = "^" + re.sub(r"\\\{[a-z_]+\\\}", ".+?", re.escape(template)) + "$"
            assert re.match(pattern, text), (cue["template_id"], lang, text)


def test_the_clock_follows_the_broadcast_convention():
    rec = _record()
    by_id = {e.event_id: e for e in rec.events}
    for cue in _cues()[0]:
        e = by_id[cue["source_event_ids"][0]]
        assert cue["clock"]["period"] == e.period
        assert cue["clock"]["ms"] == (e.minute * 60 + e.second) * 1000
    second_half = [c for c in _cues()[0] if c["clock"]["period"] == 2]
    assert second_half and min(c["clock"]["ms"] for c in second_half) >= Q.SECOND_HALF_KICKOFF_MS


def test_goals_cards_subs_chances_and_passes_fire_at_the_charter_thresholds():
    model = load_model()
    for seed in (1, 9, 22):
        rec = _record(seed)
        home = rec.meta["home"]
        cues = _cues(seed)[0]
        by_id = {e.event_id: e for e in rec.events}
        kinds = [c["template_id"] for c in cues]
        assert kinds.count("goal") == rec.meta["score"]["home"] + rec.meta["score"]["away"]
        assert kinds.count("red_card") == sum(1 for e in rec.events if e.type == "card" and e.outcome in ("red", "second_yellow"))
        assert kinds.count("substitution") == sum(1 for e in rec.events if e.type == "sub" and e.qualifier != "injury")
        last_goal = [c for c in cues if c["template_id"] == "goal"][-1]
        assert (last_goal["data"]["home_goals"], last_goal["data"]["away_goals"]) == (rec.meta["score"]["home"], rec.meta["score"]["away"])
        for cue in cues:
            e = by_id[cue["source_event_ids"][0]].to_dict()
            if cue["template_id"] == "big_chance":
                assert e["outcome"] != "goal" and xg_ppm(e, home, model) == cue["data"]["xg_ppm"] >= 200_000
            if cue["template_id"] == "pass_quality":
                assert cue["data"]["distance_m"] == pass_distance_m(e) >= 30
                assert cue["data"]["difficulty"] == pass_difficulty(e, home, model) >= 50
        main = [c for c in cues if c["template_id"] != "player_tag"]
        tags = [c for c in cues if c["template_id"] == "player_tag"]
        assert len(tags) == len(main), "one name tag for the player at the centre of each cue"


def test_numbers_are_formatted_per_language():
    cue = next(c for c in _cues(9)[0] if c["template_id"] == "big_chance")
    hundredths = Q.xg_hundredths(cue["data"]["xg_ppm"])
    assert f"xG {hundredths // 100}.{hundredths % 100:02d}" in cue["text"]["en"]
    for lang in ("el", "es", "fr"):
        assert f"xG {hundredths // 100},{hundredths % 100:02d}" in cue["text"][lang]
    assert Q.decimal(268, 1, "en") == "26.8" and Q.decimal(268, 1, "fr") == "26,8"


def test_an_injury_replacement_is_held_and_never_cued():
    rec = _record()
    sub = next(e for e in rec.events if e.type == "sub")
    injury = make_event(**{**{k: v for k, v in sub.to_dict().items() if k != "event_id"}, "qualifier": "injury", "seq": sub.seq})
    events = [injury if e.event_id == sub.event_id else e for e in rec.events]
    cues, held = _cues(events=events)
    assert held == [{"event_id": injury.event_id, "held_class": "injury_medical", "clock": Q.clock_of(injury)}]
    assert all(injury.event_id not in c["source_event_ids"] for c in cues)
    text = " ".join(t for c in cues for t in c["text"].values()).casefold()
    assert not any(word in text for word in ("injur", "τραυμ", "lesi", "bless"))


def test_without_tracking_data_a_name_tag_carries_no_number():
    cues = _cues()[0]
    tags = [c for c in cues if c["template_id"] == "player_tag"]
    assert tags and all(not re.search(r"[0-9]", t) for c in tags for t in c["text"].values())
    assert not [c for c in cues if c["template_id"] in ("sprint", "top_speed", "distance")]
    assert _doc()["kinematics"] is None


def test_tracking_triggers_become_speed_and_distance_tags_at_the_briefs_thresholds():
    rec = _record()
    pid = next(e.player for e in rec.events if e.type == "pass" and e.team == rec.meta["home"])
    kinematics = {
        "format": Q.KINEMATICS_FORMAT,
        "source": "test tracking stream",
        "triggers": [
            {"type": "sprint", "player_id": pid, "period": 2, "ms": 3_925_000, "kmh_x10": 268},
            {"type": "sprint", "player_id": pid, "period": 2, "ms": 3_930_000, "kmh_x10": 251},  # below 25.2: dropped
            {"type": "top_speed", "player_id": pid, "period": 2, "ms": 3_926_000, "kmh_x10": 312},
            {"type": "top_speed", "player_id": pid, "period": 2, "ms": 3_940_000, "kmh_x10": 299},  # below 30: dropped
            {"type": "distance", "player_id": pid, "period": 2, "ms": 4_100_000, "km": 6},
            {"type": "distance", "player_id": pid, "period": 2, "ms": 4_200_000, "km": 7},  # not a 2 km step: dropped
            {"type": "sprint", "player_id": "X9P99", "period": 1, "ms": 60_000, "kmh_x10": 300},  # not in the charter
        ],
    }
    cues = _cues(kinematics=kinematics)[0]
    speed = [c for c in cues if c["template_id"] in ("sprint", "top_speed", "distance")]
    assert [(c["template_id"], c["data"]) for c in speed] == [
        ("sprint", {"speed_kmh_x10": 268}), ("top_speed", {"speed_kmh_x10": 312}), ("distance", {"distance_km": 6}),
    ]
    assert "26.8 km/h" in speed[0]["text"]["en"] and "26,8 km/h" in speed[0]["text"]["es"]
    assert all(c["overlay"] == "player_tag" and c["source_event_ids"] == [] for c in speed)
    doc = _doc(kinematics=kinematics)
    assert Q.validate(doc) == [] and doc["kinematics"] == {"format": Q.KINEMATICS_FORMAT, "source": "test tracking stream"}
    with pytest.raises(Q.CueError, match="kinematics input refused"):
        _cues(kinematics={**kinematics, "triggers": [{"type": "sprint", "player_id": pid}]})


# ----------------------------------------------------------------- the file and its schema


def test_the_document_validates_and_every_rule_has_teeth():
    doc = _doc()
    assert Q.validate(doc) == []
    broken = [
        lambda d: d["cues"][0].update(charter_sha256="cd" * 32),
        lambda d: d["cues"][0]["window"].update(out_ms=d["cues"][0]["window"]["in_ms"]),
        lambda d: d["cues"][0].update(overlay="ticker"),
        lambda d: d["cues"][0]["text"].pop("el"),
        lambda d: d["cues"][0]["clock"].update(label="71"),
        lambda d: d["cues"].reverse(),
        lambda d: d["cues"].append(copy.deepcopy(d["cues"][-1])),
        lambda d: d["cues"][-1].update(template_id="sprint", overlay="player_tag"),
        lambda d: d["charter"].update(approved_at="2026-10-21"),
        lambda d: d.update(extra=1),
    ]
    for i, mutate in enumerate(broken):
        d = copy.deepcopy(doc)
        mutate(d)
        assert Q.validate(d), i


def test_the_json_schema_and_the_validator_agree():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["required"]) == set(schema["properties"]) == Q.DOC_KEYS
    cue = schema["$defs"]["cue"]
    assert set(cue["required"]) == set(cue["properties"]) == Q.CUE_KEYS
    assert cue["properties"]["template_id"]["enum"] == list(Q.TEMPLATE_IDS)
    assert cue["properties"]["overlay"]["enum"] == list(Q.OVERLAYS)
    assert set(cue["properties"]["data"]["properties"]) == Q.DATA_KEYS
    assert schema["properties"]["languages"]["const"] == list(Q.LANGUAGES)
    assert set(schema["properties"]["charter"]["required"]) == Q.CHARTER_REF_KEYS
    assert set(schema["properties"]["held"]["items"]["properties"]["held_class"]["enum"]) == set(C.HELD_CLASSES)
    assert schema["properties"]["format"]["const"] == Q.FORMAT and schema["$id"] == Q.SCHEMA_FILE


def test_the_cue_text_passes_the_sites_text_rules():
    data = Q.document_bytes(_doc(9))
    assert webrecord.text_problems({"9.json": data}) == []


def test_every_committed_cues_file_is_valid():
    assert Q.check_dir(CUES_DIR) == sorted(p.name for p in CUES_DIR.glob("*.json") if p.name != Q.SCHEMA_FILE)


# ----------------------------------------------------------------- the export, from the published feed


class _Sender:
    def send(self, queue: str, text: str) -> None:
        pass


def _published_charter(tmp_path: Path, seed: int = 1):
    """W-charter's steps, the Teams tap through the intake and the one verifier, then the Publisher."""
    from agon import intake
    from agon.fleet import charter_steps, steps
    from agon.fleet.contracts import PublishRequest, RunInput
    from agon.fleet.local import fake_deps

    async def to_request(deps, run_in):
        draft = await charter_steps.charter_build(deps, run_in)
        sealed = await charter_steps.charter_seal(deps, await charter_steps.charter_check_step(deps, draft))
        return sealed, await steps.prepare_approval(deps, sealed)

    deps = fake_deps()
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    run_in = RunInput(run_id="charter-1", match_ref={"source": "synth", "seed": seed, "match_index": 0}, kind="charter")
    sealed, request = asyncio.run(to_request(deps, run_in))
    now = datetime.now(timezone.utc)  # the nonce was issued on the real clock (fake_deps)
    payload = {"responder": {"userPrincipalName": OFFICER}, "data": {"decision": "approve", "bundle_sha256": request.bundle_sha256, "nonce": request.nonce}}
    outcome = intake.handle_message("decisions-teams", intake.encode_message("charter-1", payload), deps.store, now, _Sender())
    assert outcome.status == "verified", outcome
    approval_id = verifier.approval_id_for(request.nonce)
    envelope = steps.envelope_from_record(deps.store.read(verifier.APPROVALS, approval_id).body, request.nonce)
    result = asyncio.run(steps.decide(deps, request, envelope, sealed.manifest_json))
    assert isinstance(result, PublishRequest)
    manifest = json.loads(sealed.manifest_json)
    files = {name: deps.artifacts.get(entry["sha256"]) for name, entry in manifest["artifacts"].items()}
    sink = InMemorySink()
    published = Publisher(deps.store, sink, "feed", default_adapters("")).publish(
        bundle_sha256=request.bundle_sha256, approval_id=approval_id, manifest_bytes=sealed.manifest_json.encode("utf-8"),
        artifacts=files, now=now,
    )
    assert published.ok, published
    container = tmp_path / "container"
    for path, (data, _) in sink.blobs.items():
        target = container / path.removeprefix("feed/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return container, request.bundle_sha256


def test_the_export_reads_the_published_charter_by_its_hash_and_writes_valid_cues(tmp_path: Path, capsys):
    container, sha = _published_charter(tmp_path)
    out = tmp_path / "cues"
    code = Q.main(["--local", str(container), "--charter", sha, "--approved-at", "2026-10-21T12:00:00+02:00", "--out", str(out)])
    assert code == 0, capsys.readouterr().out
    doc = json.loads((out / "1.json").read_text(encoding="utf-8"))
    assert doc["charter"]["bundle_sha256"] == sha and doc["charter"]["approved_at"] == "2026-10-21T10:00:00Z"
    assert all(c["charter_sha256"] == sha for c in doc["cues"]) and doc["cues"]
    assert Q.check_dir(out) == ["1.json"]
    first = (out / "1.json").read_bytes()
    assert Q.main(["--local", str(container), "--charter", sha, "--approved-at", "2026-10-21T10:00:00Z", "--out", str(out)]) == 1
    assert Q.main(["--local", str(container), "--charter", sha, "--approved-at", "2026-10-21T10:00:00Z", "--out", str(out), "--replace"]) == 0
    assert (out / "1.json").read_bytes() == first, "the export is deterministic"
    assert Q.main(["--check", str(out)]) == 0


def test_the_export_refuses_without_an_approval_time_or_a_published_charter(tmp_path: Path, capsys):
    container, sha = _published_charter(tmp_path)
    out = tmp_path / "cues"
    assert Q.main(["--local", str(container), "--charter", sha, "--out", str(out)]) == 1
    assert "--approved-at" in capsys.readouterr().out
    assert Q.main(["--local", str(container), "--charter", "0" * 64, "--approved-at", APPROVED_AT, "--out", str(out)]) == 1
    assert "never published" in capsys.readouterr().out
    assert not out.exists()
