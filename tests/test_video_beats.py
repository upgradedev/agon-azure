"""Pure tests for the submission video pipeline (ci/video/); no ffmpeg, no network.

The ffmpeg-backed proof that the sync gate fails for the right reason lives in
ci/video/sync_gate_selftest.py and runs in .github/workflows/video-check.yml. These
tests hold the beat sheet, the frame-grid timing, the caption arithmetic, the gate's
pure rules, and the one literal that must agree in three places: the 115 s cap.

Dash characters are built with chr() so this file contains none.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ci" / "video"))

import beatsheet as bs  # noqa: E402
import compose  # noqa: E402
import narrate  # noqa: E402
import sync_gate  # noqa: E402

SHEET_PATH = ROOT / "video" / "beats.json"
EM, EN = chr(0x2014), chr(0x2013)


def _sheet_data() -> dict:
    return json.loads(SHEET_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------- the cap, three copies


def test_the_cap_literal_agrees_in_the_gate_the_fixture_and_ci():
    gate = re.findall(r"^CAP_SECONDS = (\d+)$", (ROOT / "ci/video/sync_gate.py").read_text(encoding="utf-8"), re.M)
    fixture = re.findall(r'"cap_seconds": (\d+)', (ROOT / "ci/fixtures/video/gate_cases.json").read_text(encoding="utf-8"))
    workflows = {}
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        found = re.findall(r'^\s*VIDEO_CAP_SECONDS: "(\d+)"$', path.read_text(encoding="utf-8"), re.M)
        if found:
            assert len(found) == 1, f"{path.name} pins VIDEO_CAP_SECONDS {len(found)} times"
            workflows[path.name] = found[0]
    assert len(gate) == 1, "the gate pins CAP_SECONDS exactly once"
    assert len(fixture) == 1, "the fixture pins cap_seconds exactly once"
    assert {"video.yml", "video-check.yml"} <= set(workflows), "both video workflows pin VIDEO_CAP_SECONDS"
    values = {"gate": gate[0], "fixture": fixture[0], **workflows}
    assert len(set(values.values())) == 1, f"the cap differs between copies: {values}"


def test_the_fixture_cap_is_its_own_literal_and_matches_the_gate_constant():
    fixture = json.loads((ROOT / "ci/fixtures/video/gate_cases.json").read_text(encoding="utf-8"))
    assert fixture["cap_seconds"] == sync_gate.CAP_SECONDS
    at_cap = [c for c in fixture["cases"] if c.get("expect") == ["total"]]
    assert at_cap and all(c["seconds"] == fixture["cap_seconds"] for c in at_cap), "a case sits exactly on the cap and fails"
    under = [c for c in fixture["cases"] if c.get("seconds") and not c["expect"]]
    assert under and all(c["seconds"] == fixture["cap_seconds"] - 1 / bs.FPS for c in under), "one frame under the cap passes"


# --------------------------------------------------------------------- the beat sheet


def test_the_beat_sheet_validates_and_its_floor_is_under_the_cap():
    sheet = bs.load_sheet(SHEET_PATH)
    errors, _ = bs.sheet_warnings(sheet, sync_gate.CAP_SECONDS)
    assert errors == []
    assert sheet.voice == "en-GB-SoniaNeural"
    assert [b.index for b in sheet.beats] == list(range(1, len(sheet.beats) + 1))


def test_the_beat_sheet_text_has_no_em_or_en_dash():
    text = SHEET_PATH.read_text(encoding="utf-8")
    assert EM not in text and EN not in text


def test_every_teams_beat_and_the_verify_beat_have_the_sources_they_need():
    sheet = bs.load_sheet(SHEET_PATH)
    teams = [p.target for b in sheet.beats for p in b.parts if p.kind == "teams"]
    assert teams == ["card-arrives", "officer-approves", "card-published"]
    verify = next(b for b in sheet.beats if b.id == "verify")
    web = verify.part("web")
    assert web is not None and web.viewport == "desktop" and web.facts, "the verify beat checks its facts on the page"


@pytest.mark.parametrize(
    "voice, ok",
    [
        ("en-GB-SoniaNeural", True),
        ("en-GB-RyanNeural", True),
        ("en-GB-SoniaHDNeural", False),
        ("en-US-Ava:DragonHDLatestNeural", False),
        ("en-GB-Sonia", False),
    ],
)
def test_only_standard_neural_voices_never_an_hd_name(voice, ok):
    if ok:
        assert bs.check_voice(voice) == voice
    else:
        with pytest.raises(bs.BeatSheetError):
            bs.check_voice(voice)


def _broken(mutate) -> dict:
    data = copy.deepcopy(_sheet_data())
    mutate(data)
    return data


@pytest.mark.parametrize(
    "mutate, words",
    [
        (lambda d: d["beats"][0].update(narration="Full time " + EM + " the report."), "dash"),
        (lambda d: d["beats"][1].update(id=d["beats"][0]["id"]), "unique"),
        (lambda d: d["beats"][1].update(visual=[{"source": "web:verify"}]), "starts with #/"),
        (lambda d: d["beats"][6].update(visual=[{"source": "web:#/verify", "seconds": 3}]), "last part"),
        (lambda d: d["beats"][0].update(visual=[{"source": "teams:card-arrives", "facts": [{"text": "x"}]}]), "only a web part"),
        (lambda d: d["beats"][6].update(visual=[{"source": "web:#/", "seconds": 2}, {"source": "web:#/verify"}]), "at most one web"),
        (lambda d: d["beats"][2].update(min_seconds=20, max_seconds=10), "min_seconds <= max_seconds"),
        (lambda d: d.update(voice="en-GB-SoniaHDNeural"), "never an HD"),
        (lambda d: d.update(site="http://example.com"), "https origin"),
    ],
)
def test_the_beat_sheet_refuses_each_broken_rule(mutate, words):
    with pytest.raises(bs.BeatSheetError, match=re.escape(words)):
        bs.parse_sheet(_broken(mutate))


def test_selecting_an_unknown_beat_fails_and_all_selects_every_beat():
    sheet = bs.load_sheet(SHEET_PATH)
    assert len(sheet.select("all")) == len(sheet.beats)
    assert [b.id for b in sheet.select("verify,card-arrives")] == ["card-arrives", "verify"]
    with pytest.raises(bs.BeatSheetError, match="unknown beat ids"):
        sheet.select("verify,nope")


# --------------------------------------------------------------------- captions and timing


def test_caption_chunks_fit_and_cover_every_word_once():
    for beat in bs.load_sheet(SHEET_PATH).beats:
        chunks = bs.split_caption(beat.narration)
        assert all(len(c) <= bs.CAPTION_MAX_CHARS for c in chunks), (beat.id, chunks)
        assert " ".join(chunks).split() == beat.narration.split()


def test_timing_snaps_to_frames_honours_min_and_refuses_over_max():
    sheet = bs.load_sheet(SHEET_PATH)
    beats = sheet.select("card-arrives,verify")
    timing = bs.build_timing(beats, {"card-arrives": 10_001, "verify": 3_000})
    first, second = timing["beats"]
    assert first["start_ms"] == 0 and first["window_ms"] % bs.FRAME_MS == 0
    assert first["window_ms"] >= bs.LEAD_IN_MS + 10_001 + bs.TAIL_MS
    assert second["start_ms"] == first["window_ms"], "beats follow each other on the frame grid"
    assert second["window_ms"] == round(beats[1].min_seconds * 1000), "a short narration is held to min_seconds"
    assert timing["total_ms"] == timing["total_frames"] * bs.FRAME_MS == first["window_ms"] + second["window_ms"]
    with pytest.raises(bs.BeatSheetError, match="over its max_seconds"):
        bs.build_timing(beats, {"card-arrives": 13_000, "verify": 3_000})


def test_cues_are_contiguous_inside_their_beat_and_never_overlap():
    sheet = bs.load_sheet(SHEET_PATH)
    beats = list(sheet.beats)
    timing = bs.build_timing(beats, {b.id: 6_000 for b in beats})
    cues = bs.timing_cues(timing)
    bounds, overlap = sync_gate.cue_problems(cues, timing["total_ms"])
    assert bounds == [] and overlap == []
    for row in timing["beats"]:
        inside = [c for c in row["cues"]]
        assert inside[0]["start_ms"] == row["speech_start_ms"]
        assert inside[-1]["end_ms"] <= row["start_ms"] + row["window_ms"]
        for a, b in zip(inside, inside[1:]):
            assert a["end_ms"] == b["start_ms"]


def test_srt_round_trips_and_rejects_malformed_blocks():
    cues = [bs.Cue(320, 2_480, "First, with a comma."), bs.Cue(2_480, 61_005, "Second.")]
    text = bs.write_srt(cues)
    assert "00:01:01,005" in text
    assert bs.parse_srt(text) == cues
    with pytest.raises(bs.BeatSheetError):
        bs.parse_srt("1\n00:00:01,000 -> 00:00:02,000\nNo arrow.\n")


# --------------------------------------------------------------------- the gate's pure rules


def test_total_fails_at_the_cap_and_passes_one_frame_under():
    assert not sync_gate.total_ok(115.0)
    assert not sync_gate.total_ok(115.04)
    assert sync_gate.total_ok(114.96)


def test_av_sync_allows_one_frame_and_not_one_more_millisecond():
    assert sync_gate.av_sync_ok(10.0, 10.04)
    assert sync_gate.av_sync_ok(10.04, 10.0)
    assert not sync_gate.av_sync_ok(10.0, 10.041)


def test_cue_problems_name_bounds_and_overlap_separately():
    cues = [bs.Cue(0, 1_000, "a"), bs.Cue(900, 1_500, "b"), bs.Cue(1_600, 2_100, "c"), bs.Cue(-5, 10, "d")]
    bounds, overlap = sync_gate.cue_problems(cues, 2_000)
    assert len(bounds) == 2  # cue 3 ends past 2000 ms, cue 4 starts before 0
    assert len(overlap) == 2  # cue 2 starts before cue 1 ends; cue 4 starts before cue 3 ends


def test_band_text_fraction_counts_bright_pixels_only():
    dark = bytes([12]) * 1000
    assert sync_gate.band_text_fraction(dark) == 0
    assert sync_gate.band_text_fraction(bytes([12]) * 990 + bytes([250]) * 10) == pytest.approx(0.01)


# --------------------------------------------------------------------- narrate and compose helpers


def test_ssml_escapes_text_and_names_the_voice():
    text = narrate.ssml("Pellmoor & Zennmark <1-1>", "en-GB-SoniaNeural", "en-GB", "0%")
    assert "Pellmoor &amp; Zennmark &lt;1-1&gt;" in text
    assert 'name="en-GB-SoniaNeural"' in text and "prosody" not in text
    assert 'rate="+5%"' in narrate.ssml("x", "en-GB-SoniaNeural", "en-GB", "+5%")


def test_the_cache_key_changes_only_for_the_edited_beat():
    data = _sheet_data()
    before = bs.parse_sheet(data)
    data["beats"][6]["narration"] = data["beats"][6]["narration"].replace("Anyone", "Everyone")
    after = bs.parse_sheet(data)
    keys = [(narrate.cache_key("azure", before, a), narrate.cache_key("azure", after, b)) for a, b in zip(before.beats, after.beats)]
    changed = [before.beats[i].id for i, (x, y) in enumerate(keys) if x != y]
    assert changed == ["verify"]


def test_compose_parses_blur_boxes_and_trims_and_refuses_bad_ones():
    boxes = compose.parse_boxes(["card-arrives:10,20,300,40", "card-arrives:0,0,64,64"])
    assert [b.w for b in boxes["card-arrives"]] == [300, 64]
    assert compose.parse_trims(["officer-approves=2.5"]) == {"officer-approves": 2.5}
    with pytest.raises(compose.ComposeError):
        compose.parse_boxes(["card-arrives:10,20,4,4"])
    with pytest.raises(compose.ComposeError):
        compose.parse_trims(["officer-approves=-1"])


def test_segment_captions_are_in_segment_time_and_cannot_inject_ass_codes():
    ass = compose.segment_ass([{"start_ms": 10_320, "end_ms": 12_000, "text": "A {\\b1} trick"}], 10_000, [])
    line = [l for l in ass.splitlines() if l.startswith("Dialogue:")][0]
    assert line.startswith("Dialogue: 0,0:00:00.32,0:00:02.00,Caption")
    assert "{" not in line.split(",,", 1)[1]


def test_named_parts_take_their_frames_and_the_last_takes_the_rest():
    beat = next(b for b in bs.load_sheet(SHEET_PATH).beats if b.id == "published")
    assert compose.part_frames(beat, 350) == [150, 200]
    with pytest.raises(compose.ComposeError):
        compose.part_frames(beat, 160)
