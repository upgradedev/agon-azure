"""Analyst briefs: a draft passes only when every number in it is one of the scenario's own numbers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import whatif_brief
from agon import webintel
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.whatif.brief import BRIEF_LABEL, allowed, check_brief, numbers, prompt
from agon.whatif.scenarios import SET_DIR
from foundry_smoke import Response

SET_FILE = Path(SET_DIR, "whatif-1.json")


@pytest.fixture(scope="module")
def whatif() -> dict:
    return json.loads(SET_FILE.read_bytes())


@pytest.fixture(scope="module")
def red(whatif) -> dict:
    return whatif["scenarios"][0]


def _good(sc: dict) -> str:
    n = numbers(sc)
    return (
        f"In {n['replays_per_arm']} paired replays of the engine from {n['scenario_clock']}, the side won "
        f"{n['scenario_win_pct']} with the change against {n['baseline_win_pct']} without it. "
        f"Its points per match changed by {n['points_for_delta_mean']} ({n['interval_level']} interval "
        f"{n['points_for_delta_lo']} to {n['points_for_delta_hi']}). "
        "This is a synthetic engine, not a prediction about real football."
    )


def test_a_draft_built_from_the_numbers_passes(red):
    assert check_brief(_good(red), red) == []


def test_a_number_that_is_not_the_scenarios_is_refused(red):
    text = _good(red).replace("paired replays", "paired replays (and 4321 rival models)")
    assert any("the number 4321 " in r for r in check_brief(text, red))


def test_a_bare_number_never_passes_as_a_percentage(red):
    # The scenario minute (60 for collapse, 30 here) and K (200) are allowed as numbers, never as percents.
    n = numbers(red)
    minute, k = n["scenario_minute"], n["replays_per_arm"]
    assert minute in allowed(red) and k in allowed(red)
    for fake in (f"{minute}%", f"{k}%"):
        assert fake not in allowed(red)
        text = _good(red).replace(n["scenario_win_pct"], fake, 1)
        assert any(f"the number {fake} " in r for r in check_brief(text, red)), fake
    # A real percent written without its sign still passes.
    assert check_brief(_good(red).replace(n["scenario_win_pct"], n["scenario_win_pct"][:-1] + " percent", 1), red) == []


def test_a_dropped_minus_sign_is_refused(red):
    delta = numbers(red)["points_for_delta_mean"]
    assert delta.startswith("-"), "fixture expects a negative points change for a red card"
    text = _good(red).replace(f"changed by {delta}", f"fell by {delta[1:]}")
    assert delta[1:] not in allowed(red)
    assert check_brief(text, red)


def test_sentence_count_and_dashes_are_checked(red):
    two = _good(red).rsplit(" This is", 1)[0]
    assert any("3 sentences" in r for r in check_brief(two, red))
    assert check_brief(_good(red).replace(" against ", chr(0x2014)), red)


def test_the_prompt_carries_the_numbers_and_the_synthetic_label(red, whatif):
    clubs = {c["club_id"]: c["name"] for c in whatif["clubs"]}
    text = prompt(red, clubs)
    assert "not a prediction about real football" in text
    for value in numbers(red).values():
        assert json.dumps(value) in text


def _fake(reply_for):
    def transport(url, headers, body, timeout):
        assert url.endswith("/openai/v1/chat/completions") and headers["Authorization"] == "Bearer t0k"
        content = json.loads(body)["messages"][0]["content"]
        return Response(200, json.dumps({"model": "gpt-5.6-luna-2026-07-09", "choices": [{"message": {"content": reply_for(content)}}],
                                         "usage": {"total_tokens": 1}}))
    return transport


def test_the_ci_script_records_accepted_and_refused_drafts(tmp_path, monkeypatch, whatif):
    monkeypatch.setenv("AGON_AI_TOKEN", "t0k")
    by_question = {sc["question"]: sc for sc in whatif["scenarios"]}

    def reply(content: str) -> str:
        sc = next(s for q, s in by_question.items() if json.dumps(q)[1:-1] in content)
        return _good(sc) if sc["scenario_id"] != "press_up_home_60" else "It changed by 12.34 goals. Two. Three."

    code = whatif_brief.main(["--endpoint", "https://agon-x.openai.azure.com/", "--seeds", "1", "--out", str(tmp_path)],
                             transport=_fake(reply))
    assert code == 0
    out = json.loads((tmp_path / "brief-1.json").read_bytes())
    assert out["whatif_sha256"] == sha256_hex(SET_FILE.read_bytes()) and out["label"] == BRIEF_LABEL
    status = {r["scenario_id"]: r["status"] for r in out["briefs"]}
    assert status["press_up_home_60"] == "refused" and list(status.values()).count("accepted") == 6
    refused = next(r for r in out["briefs"] if r["status"] == "refused")
    assert refused["text"] is None and refused["refused_draft"]


def test_the_ci_script_needs_a_token_and_fails_on_a_refused_call(tmp_path, monkeypatch):
    monkeypatch.delenv("AGON_AI_TOKEN", raising=False)
    assert whatif_brief.main(["--endpoint", "https://agon-x.openai.azure.com/", "--out", str(tmp_path)]) == 1
    monkeypatch.setenv("AGON_AI_TOKEN", "t0k")
    denied = lambda *a: Response(403, '{"error":{"code":"PermissionDenied"}}')  # noqa: E731
    assert whatif_brief.main(["--endpoint", "https://agon-x.openai.azure.com/", "--seeds", "1", "--out", str(tmp_path)], transport=denied) == 1


def _committed(tmp_path: Path, whatif: dict, text_for, sha: str | None = None) -> Path:
    rows = [{"scenario_id": sc["scenario_id"], "status": "accepted", "text": text_for(sc), "refused_draft": None,
             "reasons": [], "model": "m", "latency_ms": 1, "prompt_sha256": "0" * 64} for sc in whatif["scenarios"]]
    rows.append({"scenario_id": "red_home_30", "status": "refused", "text": None, "refused_draft": "secret 99 draft",
                 "reasons": ["x"], "model": "m", "latency_ms": 1, "prompt_sha256": "0" * 64})
    body = {"kind": "brief", "schema": "agon.webintel.brief/1", "seed": 1, "deployment": "gpt-5.6-luna",
            "whatif_sha256": sha or sha256_hex(SET_FILE.read_bytes()), "label": BRIEF_LABEL, "briefs": rows}
    (tmp_path / "brief-1.json").write_bytes(canonical_bytes(body) + b"\n")
    return tmp_path


def test_webintel_publishes_only_accepted_briefs_after_rechecking_them(tmp_path, whatif):
    raw = SET_FILE.read_bytes()
    web = webintel.committed_brief(1, raw, str(_committed(tmp_path, whatif, _good)))
    assert len(web["briefs"]) == 7 and "secret 99 draft" not in json.dumps(web)
    assert webintel.validate_payload("brief-1.json", web) == []
    assert web["events_sha256"] == whatif["events_sha256"]
    assert webintel.committed_brief(1, raw, str(tmp_path / "nothing-here")) is None


def test_a_failing_brief_is_withheld_and_the_deploy_goes_on(tmp_path, whatif):
    from agon.webdata import write_web_data

    data = tmp_path / "data"
    write_web_data(str(data), [1])
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    _committed(briefs, whatif, lambda sc: _good(sc).replace("paired replays", "4321 paired replays"))
    manifest, files = webintel.build_intel_web(str(data), [1], brief_dir=str(briefs))
    assert "whatif-1.json" in files and "brief-1.json" not in files
    [held] = manifest["withheld"]
    assert held["name"] == "brief-1.json" and "4321" in held["detail"] and held["reason"].startswith("The analyst brief")
    good = tmp_path / "good"
    good.mkdir()
    _committed(good, whatif, _good)
    manifest, files = webintel.build_intel_web(str(data), [1], brief_dir=str(good))
    assert "brief-1.json" in files and manifest["withheld"] == []


@pytest.mark.parametrize("payload", [
    b"[]\n",
    b'{"briefs": [null], "whatif_sha256": "WHATIF"}\n',
    b'{"briefs": [{"scenario_id": "red_home_30", "status": "accepted", "text": 5}], "whatif_sha256": "WHATIF"}\n',
    b'{"briefs": "nope", "whatif_sha256": "WHATIF"}\n',
])
def test_a_malformed_brief_is_withheld_not_a_stopped_deploy(tmp_path, payload):
    from agon.webdata import write_web_data

    data = tmp_path / "data"
    write_web_data(str(data), [1])
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    (briefs / "brief-1.json").write_bytes(payload.replace(b"WHATIF", sha256_hex(SET_FILE.read_bytes()).encode("ascii")))
    manifest, files = webintel.build_intel_web(str(data), [1], brief_dir=str(briefs))
    assert "brief-1.json" not in files and "whatif-1.json" in files
    assert [w["name"] for w in manifest["withheld"]] == ["brief-1.json"]


def test_webintel_refuses_a_brief_with_an_unchecked_number_or_another_set(tmp_path, whatif):
    raw = SET_FILE.read_bytes()
    bad = _committed(tmp_path, whatif, lambda sc: _good(sc).replace("paired replays", "4321 paired replays"))
    with pytest.raises(ValueError, match="4321"):
        webintel.committed_brief(1, raw, str(bad))
    other = _committed(tmp_path, whatif, _good, sha="f" * 64)
    with pytest.raises(ValueError, match="another What-If file"):
        webintel.committed_brief(1, raw, str(other))
