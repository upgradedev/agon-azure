"""Prove the sync gate fails, for the right reason, on every defect it claims to catch.

A gate that has never failed proves nothing. This renders tiny synthetic mp4s with
ffmpeg, one per case in ci/fixtures/video/gate_cases.json, runs the real gate on
each and asserts that the set of failing checks is exactly the one the case
expects: empty for a good file, {"total"} for a file of exactly 115.000 s, and so
on. The case "composed_good" is built end to end by narrate.py (tone engine) and
compose.py from a synthetic beat sheet and clip, so the composer is held to the
gate too. The CLI is also run once per exit code (0 pass, 1 fail, 2 cap mismatch).

The fixture's cap is its own literal. This script refuses to run unless the
fixture, the gate and the cap CI passes in (--cap-seconds) agree.

Needs ffmpeg and ffprobe with libx264, libvpx, libmp3lame and libass. No network,
no Azure, no key.

Usage: python ci/video/sync_gate_selftest.py --cap-seconds 115 --work DIR
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import beatsheet as bs  # noqa: E402
import compose  # noqa: E402
import sync_gate  # noqa: E402

ROOT = HERE.parent.parent
FIXTURE = ROOT / "ci" / "fixtures" / "video" / "gate_cases.json"
FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"


def _ffmpeg(*args: str, cwd: Path | None = None) -> None:
    bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", *args], cwd=cwd)


def render_case(work: Path, case: dict[str, Any]) -> tuple[Path, Path, Path]:
    """A two-beat synthetic video, its SRT and its receipt, broken the way the case says."""
    folder = work / case["name"]
    folder.mkdir(parents=True, exist_ok=True)
    seconds = float(case.get("seconds", 4.0))
    fps = int(case.get("fps", bs.FPS))
    width, height = case.get("size", [bs.WIDTH, bs.HEIGHT])
    frames = round(seconds * fps)
    total_ms = round(frames * 1000 / fps)
    half = (round(seconds * bs.FPS) // 2) * bs.FRAME_MS
    beats = [("a", 0, half), ("b", half, total_ms - half)]
    cues = [
        {"start_ms": 320, "end_ms": half - 480, "text": "The first beat says one thing on screen."},
        {"start_ms": half + 320, "end_ms": total_ms - 480, "text": "The second beat says another."},
    ]
    burned = list(cues)
    srt_cues = list(cues)
    mode = case.get("srt", "ok")
    if mode == "overlap":
        mid = (cues[1]["start_ms"] + cues[1]["end_ms"]) // 2
        srt_cues = [cues[0], {**cues[1], "end_ms": mid + 200}, {"start_ms": mid, "end_ms": cues[1]["end_ms"], "text": "Overlapping."}]
        burned = srt_cues
    elif mode == "past_end":
        srt_cues = [*cues, {"start_ms": total_ms - 300, "end_ms": total_ms + 500, "text": "Past the end."}]
    elif mode == "drop_b":
        srt_cues = [cues[0]]
    (folder / "case.ass").write_text(compose.segment_ass(burned if case.get("burn", True) else [], 0, []), encoding="utf-8")
    audio_seconds = seconds - int(case.get("audio_trim_frames", 0)) / bs.FPS
    video = folder / "case.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c={bs.BACKGROUND}:s={width}x{height}:r={fps}:d={seconds + 1:.3f}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={bs.SAMPLE_RATE}:duration={audio_seconds:.3f}",
        "-filter_complex", "[0:v]ass=case.ass,format=yuv420p[v];[1:a]volume=0.1[a]",
        "-map", "[v]", "-map", "[a]", "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-r", str(fps), "-c:a", "aac", "-b:a", "64k", "-ar", str(bs.SAMPLE_RATE), "-movflags", "+faststart", "case.mp4",
        cwd=folder,
    )
    srt = folder / "captions.srt"
    srt.write_text(bs.write_srt(bs.Cue(c["start_ms"], c["end_ms"], c["text"]) for c in srt_cues), encoding="utf-8")
    receipt = {
        "format": bs.RECEIPT_FORMAT,
        "release_sha": FAKE_SHA,
        "beats": [
            {"id": name, "start_ms": start, "window_ms": length, "narrated": True,
             "captions_burned": case.get("receipt_burned", True) or name == "a"}
            for name, start, length in beats
        ],
        "total_ms": total_ms,
        "sha256": "f" * 64 if case.get("receipt_sha256") == "tampered" else bs.sha256_file(video),
    }
    receipt_path = folder / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return video, srt, receipt_path


def compose_case(work: Path) -> tuple[Path, Path, Path]:
    """The good case built by the real narrate.py and compose.py from a synthetic sheet and clip."""
    folder = work / "composed_good"
    clips = folder / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    sheet = {
        "format": bs.BEATS_FORMAT, "voice": "en-GB-SoniaNeural", "locale": "en-GB", "rate": "0%",
        "site": "https://agon.example", "run_id": "run-selftest", "theme": "dark", "target_seconds": [1, 30],
        "beats": [
            {"id": "teams-beat", "narration": "A placeholder card stands in for a Teams clip.",
             "visual": [{"source": "teams:card-arrives"}], "min_seconds": 3, "max_seconds": 6},
            {"id": "web-beat", "narration": "A web clip, trimmed after its lead, then held to the narration.",
             "visual": [{"source": "web:#/verify", "facts": [{"text": "anything"}]}], "min_seconds": 3, "max_seconds": 6},
        ],
    }
    beats_path = folder / "beats.json"
    beats_path.write_text(json.dumps(sheet, indent=2), encoding="utf-8")
    lead = 0.6
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=7.5", "-c:v", "libvpx", "-b:v", "600k",
            "-deadline", "realtime", "-cpu-used", "8", str(clips / "web-beat.webm"))
    (clips / "web-beat.json").write_text(json.dumps({
        "format": bs.CLIP_FORMAT, "beat": "web-beat", "release_sha": FAKE_SHA, "data_commit": FAKE_SHA,
        "lead_seconds": lead, "placeholder": None,
    }), encoding="utf-8")
    narration, out = folder / "narration", folder / "out"
    for cmd in (
        [sys.executable, str(HERE / "narrate.py"), "--beats", str(beats_path), "--out", str(narration), "--engine", "tone"],
        [sys.executable, str(HERE / "compose.py"), "--beats", str(beats_path), "--narration", str(narration),
         "--clips", str(clips), "--release-sha", FAKE_SHA, "--out", str(out)],
    ):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        print(proc.stdout.rstrip())
        if proc.returncode != 0:
            raise RuntimeError(f"{Path(cmd[1]).name} failed: {proc.stdout[-600:]} {proc.stderr[-600:]}")
    return out / "agon-video-preview.mp4", out / "captions.srt", out / "receipt.json"


def cli_exit(video: Path, srt: Path, receipt: Path, cap: float) -> int:
    return subprocess.run(
        [sys.executable, str(HERE / "sync_gate.py"), str(video), "--receipt", str(receipt), "--srt", str(srt),
         "--cap-seconds", str(cap)],
        capture_output=True, text=True,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prove the sync gate fails for the right reason on each broken case.")
    parser.add_argument("--cap-seconds", type=float, required=True, help="the cap CI pins (VIDEO_CAP_SECONDS)")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    caps = {"fixture": fixture["cap_seconds"], "gate": sync_gate.CAP_SECONDS, "ci": args.cap_seconds}
    if len({float(v) for v in caps.values()}) != 1:
        print(f"FAIL: the cap differs between copies: {caps}")
        return 1
    print(f"cap agrees in the fixture, the gate and CI: {sync_gate.CAP_SECONDS} s; ffmpeg: {bs.ffmpeg_version()}")
    args.work.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, list[str], list[str], bool]] = []
    files: dict[str, tuple[Path, Path, Path]] = {}
    for case in fixture["cases"]:
        name = case["name"]
        try:
            video, srt, receipt = compose_case(args.work) if case.get("compose") else render_case(args.work, case)
            checks = sync_gate.run_gate(video, receipt, srt, sync_gate.CAP_SECONDS)
            failed = sorted(c.name for c in checks if not c.ok)
            for c in checks:
                if not c.ok:
                    print(f"  {name}: FAIL {c.name}: {c.detail}")
        except Exception as exc:  # noqa: BLE001 - a case that cannot even run is a self-test failure
            failed = [f"error: {type(exc).__name__}: {exc}"]
            video = srt = receipt = Path()
        files[name] = (video, srt, receipt)
        expected = sorted(case["expect"])
        ok = failed == expected
        results.append((name, expected, failed, ok))
        print(f"{'ok  ' if ok else 'BAD '} {name}: expected {expected or 'pass'}, gate said {failed or 'pass'}")

    exits = []
    if "good" in files and "at_cap" in files:
        good, broken = files["good"], files["at_cap"]
        exits = [
            ("exit 0 on a good file", cli_exit(good[0], good[1], good[2], sync_gate.CAP_SECONDS), 0),
            ("exit 1 on a broken file", cli_exit(broken[0], broken[1], broken[2], sync_gate.CAP_SECONDS), 1),
            ("exit 2 when CI passes another cap", cli_exit(good[0], good[1], good[2], sync_gate.CAP_SECONDS + 5), 2),
        ]
        for label, got, want in exits:
            print(f"{'ok  ' if got == want else 'BAD '} CLI {label}: got {got}")

    bad = [r for r in results if not r[3]] + [e for e in exits if e[1] != e[2]]
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write("\n### Sync gate self-test\n\n| Case | Expected | Gate | Result |\n|---|---|---|---|\n")
            for name, expected, failed, ok in results:
                fh.write(f"| {name} | {', '.join(expected) or 'pass'} | {', '.join(failed) or 'pass'} | {'ok' if ok else 'BAD'} |\n")
            for label, got, want in exits:
                fh.write(f"| CLI {label} | {want} | {got} | {'ok' if got == want else 'BAD'} |\n")
    print(f"self-test: {len(results) - len([r for r in results if not r[3]])} of {len(results)} cases as expected; "
          f"{len(exits) - len([e for e in exits if e[1] != e[2]])} of {len(exits)} exit codes as expected")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
