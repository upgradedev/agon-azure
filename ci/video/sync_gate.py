"""The sync gate of the submission video: it measures the shipped mp4, never the build inputs.

The rules say the video must be less than two minutes and judges need not watch
past two minutes. Our cap is 115 seconds, pinned here (CAP_SECONDS), in the
self-test fixture ci/fixtures/video/gate_cases.json, and in the workflows
(VIDEO_CAP_SECONDS); tests/test_video_beats.py greps that the three agree, and
this gate refuses to run when the cap CI passes in differs from its own.

Checks, each with the reason code the self-test expects:

  dimensions        the video stream is 1920 x 1080
  frame_rate        the video stream is 25 fps (r_frame_rate and avg_frame_rate)
  total             the decoded length is under the cap; 115.000 s or more fails
  av_sync           decoded video and decoded audio differ by at most one frame
  captions_bounds   every SRT cue starts at or after 0, ends after it starts and
                    ends inside the video
  captions_overlap  no cue starts before the previous one ends
  captions_missing  every narrated beat in the receipt says its captions were
                    burned, has at least one SRT cue inside its window, and at
                    the middle of each such cue the caption band of the shipped
                    pixels holds text; the band must be empty at the first frame,
                    before any cue, or text could not be told from background
  receipt           the receipt names a 40-hex release SHA, its sha256 is the
                    file's, and its beat windows add up to its total

The video length is the decoded frame count over the measured frame rate, and the
audio length is the decoded sample count over 48 kHz, so a container header that
claims a duration proves nothing here. Pixel checks are skipped, and say so, when
the dimensions are wrong, so a broken file fails for the reason that broke it.

Usage:
  python ci/video/sync_gate.py VIDEO.mp4 --receipt receipt.json --srt captions.srt \
      --cap-seconds 115 [--report gate.json] [--summary FILE]
Exit 0 when every check passes, 1 when any fails, 2 on a usage or configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import beatsheet as bs  # noqa: E402

CAP_SECONDS = 115
FRAME_SECONDS = 1 / bs.FPS
TEXT_LUMA = 160  # a pixel this bright in the dark band is caption text
TEXT_MIN_FRACTION = 0.002  # at a cue: at least this share of the band is text
EMPTY_MAX_FRACTION = 0.0005  # before any cue: at most this share


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


# --------------------------------------------------------------------- pure rules


def total_ok(video_seconds: float, cap_seconds: float = CAP_SECONDS) -> bool:
    """Strictly under the cap: 115.000 s fails, 114.960 s (one frame less) passes."""
    return video_seconds < cap_seconds - 1e-9


def av_sync_ok(video_seconds: float, audio_seconds: float, fps: float = bs.FPS) -> bool:
    """At most one frame apart; one frame and a millisecond fails."""
    return abs(video_seconds - audio_seconds) <= 1 / fps + 1e-6


def cue_problems(cues: list[bs.Cue], video_ms: int) -> tuple[list[str], list[str]]:
    """(out of bounds, overlapping) descriptions, in file order."""
    bounds, overlap = [], []
    for n, cue in enumerate(cues, start=1):
        if cue.start_ms < 0 or cue.end_ms <= cue.start_ms or cue.end_ms > video_ms:
            bounds.append(f"cue {n} {cue.start_ms}..{cue.end_ms} ms is outside 0..{video_ms} ms or empty")
        if n > 1 and cue.start_ms < cues[n - 2].end_ms:
            overlap.append(f"cue {n} starts at {cue.start_ms} ms, before cue {n - 1} ends at {cues[n - 2].end_ms} ms")
    return bounds, overlap


def band_text_fraction(gray: bytes) -> float:
    """Share of caption-band pixels bright enough to be text (8-bit luma)."""
    if not gray:
        return 0.0
    table = bytes(1 if v >= TEXT_LUMA else 0 for v in range(256))
    return gray.translate(table).count(1) / len(gray)


# --------------------------------------------------------------------- measuring


def _rate(text: str) -> Fraction | None:
    try:
        value = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def band_pixels(video: Path, at_seconds: float) -> bytes:
    """The caption band of the frame shown at `at_seconds`, as 8-bit luma."""
    proc = bs.run(
        [
            bs.tool("ffmpeg"), "-v", "error", "-ss", f"{at_seconds:.3f}", "-i", str(video), "-frames:v", "1",
            "-vf", f"crop={bs.WIDTH}:{bs.BAND_HEIGHT}:0:{bs.VISUAL_HEIGHT},format=gray", "-f", "rawvideo", "pipe:1",
        ],
        binary=True,
    )
    return proc.stdout


def run_gate(video: Path, receipt_path: Path, srt_path: Path, cap_seconds: float = CAP_SECONDS) -> list[Check]:
    checks: list[Check] = []
    receipt: dict[str, Any] = json.loads(receipt_path.read_text(encoding="utf-8"))
    cues = bs.parse_srt(srt_path.read_text(encoding="utf-8"))

    meta = bs.probe(video, "-show_entries", "stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate")
    vstreams = [s for s in meta.get("streams", []) if s.get("codec_type") == "video"]
    astreams = [s for s in meta.get("streams", []) if s.get("codec_type") == "audio"]
    if len(vstreams) != 1 or len(astreams) != 1:
        checks.append(Check("streams", False, f"{len(vstreams)} video and {len(astreams)} audio streams, need one of each"))
        return checks
    v = vstreams[0]
    width, height = int(v.get("width", 0)), int(v.get("height", 0))
    dims_ok = (width, height) == (bs.WIDTH, bs.HEIGHT)
    checks.append(Check("dimensions", dims_ok, f"{width}x{height}, need {bs.WIDTH}x{bs.HEIGHT}"))

    r_rate, avg_rate = _rate(v.get("r_frame_rate", "")), _rate(v.get("avg_frame_rate", ""))
    rate_ok = r_rate == bs.FPS and avg_rate == bs.FPS
    checks.append(Check("frame_rate", rate_ok, f"r_frame_rate {v.get('r_frame_rate')}, avg_frame_rate {v.get('avg_frame_rate')}, need 25/1"))

    frames = bs.counted_frames(video)
    fps = float(avg_rate or r_rate or bs.FPS)
    video_seconds = frames / fps
    audio_seconds = bs.decoded_samples(video) / bs.SAMPLE_RATE
    checks.append(Check(
        "total", total_ok(video_seconds, cap_seconds),
        f"{video_seconds:.3f} s ({frames} frames), must be under {cap_seconds} s",
    ))
    checks.append(Check(
        "av_sync", av_sync_ok(video_seconds, audio_seconds, fps),
        f"video {video_seconds:.3f} s, audio {audio_seconds:.3f} s, difference {abs(video_seconds - audio_seconds) * 1000:.1f} ms, "
        f"limit one frame ({1000 / fps:.0f} ms)",
    ))

    video_ms = round(video_seconds * 1000)
    bounds, overlap = cue_problems(cues, video_ms)
    checks.append(Check("captions_bounds", not bounds, "; ".join(bounds[:4]) or f"{len(cues)} cues inside 0..{video_ms} ms"))
    checks.append(Check("captions_overlap", not overlap, "; ".join(overlap[:4]) or f"{len(cues)} cues, none overlapping"))

    missing: list[str] = []
    narrated = [b for b in receipt.get("beats", []) if b.get("narrated")]
    sampled = 0
    for beat in narrated:
        lo, hi = int(beat.get("start_ms", 0)), int(beat.get("start_ms", 0)) + int(beat.get("window_ms", 0))
        inside = [c for c in cues if lo <= c.start_ms and c.end_ms <= hi]
        if not beat.get("captions_burned"):
            missing.append(f"beat {beat.get('id')}: the receipt says its captions were not burned")
        if not inside:
            missing.append(f"beat {beat.get('id')}: no SRT cue inside {lo}..{hi} ms")
        if not dims_ok:
            continue
        for cue in inside:
            middle = (cue.start_ms + cue.end_ms) / 2000
            fraction = band_text_fraction(band_pixels(video, middle))
            sampled += 1
            if fraction < TEXT_MIN_FRACTION:
                missing.append(
                    f"beat {beat.get('id')}: no burned caption in the band at {middle:.2f} s "
                    f"({fraction * 100:.3f}% text pixels, need {TEXT_MIN_FRACTION * 100:.1f}%)"
                )
    if not narrated:
        missing.append("the receipt lists no narrated beat")
    if dims_ok:
        first_cue = min((c.start_ms for c in cues), default=video_ms)
        if first_cue >= 2 * bs.FRAME_MS:
            empty = band_text_fraction(band_pixels(video, 0.0))
            if empty > EMPTY_MAX_FRACTION:
                missing.append(
                    f"the caption band already holds {empty * 100:.3f}% bright pixels at 0 s, before any cue, "
                    "so burned text cannot be told apart from the background"
                )
        else:
            missing.append("the first cue starts at 0 s, so the empty band cannot be checked")
        pixel_note = f"{sampled} cue midpoints read from the pixels"
    else:
        pixel_note = "pixel checks not run: wrong dimensions"
    checks.append(Check("captions_missing", not missing, "; ".join(missing[:4]) or f"{len(narrated)} narrated beats captioned; {pixel_note}"))

    problems = []
    if not bs.SHA40.match(str(receipt.get("release_sha", ""))):
        problems.append("release_sha is not 40 hex characters")
    if receipt.get("sha256") != bs.sha256_file(video):
        problems.append("its sha256 is not the file's")
    windows = sum(int(b.get("window_ms", 0)) for b in receipt.get("beats", []))
    if windows != int(receipt.get("total_ms", -1)):
        problems.append(f"beat windows add up to {windows} ms, total_ms says {receipt.get('total_ms')}")
    if abs(int(receipt.get("total_ms", 0)) - video_ms) > round(1000 / fps):
        problems.append(f"total_ms {receipt.get('total_ms')} is not the measured {video_ms} ms")
    checks.append(Check("receipt", not problems, "; ".join(problems) or f"release {receipt.get('release_sha')}, sha256 matches"))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the shipped video and fail on any sync, length or caption defect.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--cap-seconds", type=float, required=True, help="the cap CI pins; must equal this gate's")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--summary", type=Path, help="append a markdown table (GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)
    if args.cap_seconds != CAP_SECONDS:
        print(f"configuration error: CI passes a cap of {args.cap_seconds} s, the gate pins {CAP_SECONDS} s; change every copy together")
        return 2
    for path in (args.video, args.receipt, args.srt):
        if not path.is_file():
            print(f"usage error: {path} is not a file")
            return 2
    try:
        checks = run_gate(args.video, args.receipt, args.srt, args.cap_seconds)
    except (bs.MediaError, bs.BeatSheetError, json.JSONDecodeError, ValueError) as exc:
        checks = [Check("readable", False, str(exc))]
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'} {c.name}: {c.detail}")
    print(f"sync gate: {len(checks) - len(failed)} of {len(checks)} checks pass on {args.video.name}")
    if args.report:
        args.report.write_text(json.dumps({"video": args.video.name, "cap_seconds": CAP_SECONDS,
                                           "checks": [asdict(c) for c in checks]}, indent=2) + "\n", encoding="utf-8")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(f"\n### Sync gate on {args.video.name}\n\n| Check | Result | Detail |\n|---|---|---|\n")
            for c in checks:
                fh.write(f"| {c.name} | {'pass' if c.ok else 'FAIL'} | {c.detail.replace('|', '/')} |\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
