"""Compose the submission video from the measured narration, the web clips and the Teams clips.

Per beat, in order, it renders one segment of exactly the beat's window in frames:
each visual part is trimmed (web capture, Teams clip) or held (still, card) to its
share of the window, scaled to fit 1920 x 930, padded onto a 1920 x 1080 frame at
25 fps, and that beat's captions are burned into the band below. The segments are
joined without re-encoding. The narration track puts each beat's mp3 at its measured
offset (lead-in after the beat start), pads it to the window in samples, joins the
beats, and applies two-pass loudnorm at -16 LUFS. The mp4 and a receipt come out:
release SHA, per-beat windows, the total, and the sha256 of the file.

No -shortest anywhere. Before a clip is used, its usable length after the lead or
trim is measured and must cover the part; after a segment is rendered, its decoded
frame count must equal the window. A short clip fails with the seconds it lacks.

Modes:
  preview  (CI) every Teams part becomes a labelled card: "Teams clip recorded by the
           club media office goes here".
  final    (on the owner's machine, ffmpeg 8.1.1, Python standard library only)
           --teams-dir names a local folder with the owner's Teams recordings, never
           committed; see video/OWNER_CLIPS.txt for the clip names and trims.
           --blur card-arrives:X,Y,W,H blurs a box of a Teams clip, in that clip's own
           pixels, for example the owner's name. The owner decides whether to use it.

Inputs are held to the capture receipts: every clip and still must have been made
for this release on a page serving it, all of them must show the same run, and a
clip whose narrated facts did not hold is refused unless --rehearsal (the pull
request rehearsal only) composes it and lists the facts in the receipt. A still
is clips/still-<name>.png from the capture (or video/stills/<name>.png); a still
or clip receipt with a placeholder becomes a labelled card, never a stand-in picture.

Segments are cached by a hash of their inputs and filter graph, so changing one
beat's words re-renders that segment only, then the join and the audio.

Usage:
  python ci/video/compose.py --beats video/beats.json --narration NARR --clips CLIPS \
      --release-sha SHA --out OUT [--teams-dir DIR] [--blur ID:X,Y,W,H ...] [--trim CLIP=SECONDS ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import beatsheet as bs  # noqa: E402

TEAMS_PLACEHOLDER = "Teams clip recorded by the club media office goes here"
TEAMS_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")
LOUDNESS = {"I": -16.0, "TP": -1.5, "LRA": 11.0}
SEGMENT_CRF = "18"
FIT = (
    f"scale={bs.WIDTH}:{bs.VISUAL_HEIGHT}:force_original_aspect_ratio=decrease:flags=lanczos,"
    f"pad={bs.WIDTH}:{bs.VISUAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color={bs.BACKGROUND},setsar=1"
)


class ComposeError(RuntimeError):
    """An input is missing, too short or inconsistent; the message says which and by how much."""


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int


# --------------------------------------------------------------------- ASS captions


def _ass_time(ms: int) -> str:
    cs = max(0, round(ms / 10))
    hours, rest = divmod(cs, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, cents = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cents:02d}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {bs.WIDTH}
PlayResY: {bs.HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,120,120,26,1
Style: Card,DejaVu Sans,56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,160,160,0,1
Style: CardNote,DejaVu Sans,32,&H00D0C4B8,&H00D0C4B8,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,160,160,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def segment_ass(cues: list[dict[str, Any]], start_ms: int, cards: list[tuple[int, int, str, str]]) -> str:
    """Captions of one beat in segment time, plus centred card text for placeholder parts."""
    lines = [ASS_HEADER.rstrip("\n")]
    for cue in cues:
        lines.append(
            f"Dialogue: 0,{_ass_time(cue['start_ms'] - start_ms)},{_ass_time(cue['end_ms'] - start_ms)},Caption,,0,0,0,,{_ass_text(cue['text'])}"
        )
    centre = bs.VISUAL_HEIGHT // 2
    for a, b, title, note in cards:
        lines.append(f"Dialogue: 1,{_ass_time(a)},{_ass_time(b)},Card,,0,0,0,,{{\\an2\\pos({bs.WIDTH // 2},{centre + 10})}}{_ass_text(title)}")
        lines.append(f"Dialogue: 1,{_ass_time(a)},{_ass_time(b)},CardNote,,0,0,0,,{{\\an8\\pos({bs.WIDTH // 2},{centre + 36})}}{_ass_text(note)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- inputs


def clip_receipt(clips: Path, name: str, release_sha: str, *, media: str = ".webm") -> tuple[Path, dict[str, Any]]:
    """The capture receipt <name>.json and its media file; the release and the served commit must be this compose's."""
    meta_path = clips / f"{name}.json"
    if not meta_path.is_file():
        raise ComposeError(f"{name}: no capture receipt {meta_path.name} in {clips}; run the capture for this beat")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("format") != bs.CLIP_FORMAT or meta.get("beat") != name:
        raise ComposeError(f"{name}: {meta_path.name} is not a capture receipt for it")
    if meta.get("release_sha") != release_sha or meta.get("data_commit") != release_sha:
        raise ComposeError(
            f"{name}: the clip was recorded with release {meta.get('release_sha')} on a page serving "
            f"{meta.get('data_commit')}; this compose is for {release_sha}"
        )
    video = clips / f"{name}{media}"
    if not meta.get("placeholder") and not video.is_file():
        raise ComposeError(f"{name}: no {video.name} in {clips}")
    return video, meta


def teams_clip(teams_dir: Path, name: str) -> Path:
    for suffix in TEAMS_SUFFIXES:
        path = teams_dir / f"{name}{suffix}"
        if path.is_file():
            return path
    raise ComposeError(f"teams clip {name} not found in {teams_dir} (tried {', '.join(TEAMS_SUFFIXES)}); see video/OWNER_CLIPS.txt")


def usable_seconds(path: Path, start: float) -> float:
    """Seconds of picture after `start`, from the last packet, since browser webm often lacks a duration."""
    return bs.last_video_pts(path) + 1 / bs.FPS - start


def blur_chain(boxes: list[Box], label: str, tag: str) -> tuple[str, str]:
    """Filters that blur each box of the stream `label`; returns (filters, output label)."""
    parts, current = [], label
    for n, box in enumerate(boxes):
        radius = max(1, min(30, min(box.w, box.h) // 5))
        a, b, c, out = f"[{tag}{n}a]", f"[{tag}{n}b]", f"[{tag}{n}c]", f"[{tag}{n}o]"
        parts.append(
            f"{current}split=2{a}{b};{b}crop={box.w}:{box.h}:{box.x}:{box.y},boxblur=luma_radius={radius}:luma_power=2{c};"
            f"{a}{c}overlay={box.x}:{box.y}{out}"
        )
        current = out
    return ";".join(parts), current


def parse_boxes(values: list[str]) -> dict[str, list[Box]]:
    out: dict[str, list[Box]] = {}
    for value in values:
        m = re.fullmatch(r"([a-z0-9-]+):(\d+),(\d+),(\d+),(\d+)", value.strip())
        if not m or int(m.group(4)) < 8 or int(m.group(5)) < 8:
            raise ComposeError(f"--blur {value!r} must be CLIP:X,Y,W,H in that clip's pixels, at least 8 x 8")
        out.setdefault(m.group(1), []).append(Box(*(int(g) for g in m.groups()[1:])))
    return out


def parse_trims(values: list[str]) -> dict[str, float]:
    out = {}
    for value in values:
        m = re.fullmatch(r"([a-z0-9-]+)=(\d+(?:\.\d+)?)", value.strip())
        if not m:
            raise ComposeError(f"--trim {value!r} must be CLIP=SECONDS")
        out[m.group(1)] = float(m.group(2))
    return out


# --------------------------------------------------------------------- segments


def part_frames(beat: bs.Beat, window: int) -> list[int]:
    fixed = [round((p.seconds or 0) * bs.FPS) for p in beat.parts[:-1]]
    last = window - sum(fixed)
    if last < bs.FPS:
        raise ComposeError(f"{beat.id}: the named parts leave {last} frames for the last part, under one second")
    return [*fixed, last]


def render_segment(
    beat: bs.Beat,
    row: dict[str, Any],
    *,
    sheet: bs.Sheet,
    clips: Path,
    teams_dir: Path | None,
    stills: Path,
    release_sha: str,
    boxes: dict[str, list[Box]],
    trims: dict[str, float],
    seg_dir: Path,
    rehearsal: bool = False,
) -> dict[str, Any]:
    window = int(row["window_frames"])
    frames = part_frames(beat, window)
    inputs: list[str] = []
    graph: list[str] = []
    cards: list[tuple[int, int, str, str]] = []
    hashed: list[str] = []
    parts_out: list[dict[str, Any]] = []
    at_ms = 0
    for k, (part, count) in enumerate(zip(beat.parts, frames)):
        index = len([a for a in inputs if a == "-i"])
        seconds = count / bs.FPS
        info: dict[str, Any] = {"source": f"{part.kind}:{part.target}", "frames": count, "clip": None, "placeholder": None}
        card: tuple[str, str] | None = None
        meta: dict[str, Any] = {}
        if part.kind == "web":
            video, meta = clip_receipt(clips, beat.id, release_sha)
            if meta.get("placeholder"):
                card = (str(meta["placeholder"]), f"placeholder: not on the live site at {release_sha[:7]}")
                info["placeholder"] = meta["placeholder"]
            else:
                lead = float(meta.get("lead_seconds", 0))
                have = usable_seconds(video, lead)
                if have + 1e-6 < seconds:
                    raise ComposeError(
                        f"{beat.id}: the web clip has {have:.2f} s after its {lead:.2f} s lead, the part needs {seconds:.2f} s; "
                        "record a longer clip (the capture holds max_seconds plus a margin)"
                    )
                inputs += ["-ss", f"{lead:.3f}", "-i", str(video.resolve())]
                graph.append(f"[{index}:v]fps={bs.FPS},trim=end_frame={count},setpts=PTS-STARTPTS,{FIT}[p{k}]")
                hashed.append(bs.sha256_file(video))
                info["clip"] = video.name
        elif part.kind == "teams":
            if teams_dir is None:
                card = (TEAMS_PLACEHOLDER, f"{beat.id}: teams:{part.target} (preview; the final cut uses the owner's recording)")
                info["placeholder"] = TEAMS_PLACEHOLDER
            else:
                video = teams_clip(teams_dir, part.target)
                start = trims.get(part.target, part.trim_start)
                have = usable_seconds(video, start)
                if have + 1e-6 < seconds:
                    raise ComposeError(
                        f"{beat.id}: Teams clip {video.name} has {have:.2f} s after a {start:.2f} s trim, the part needs "
                        f"{seconds:.2f} s; record or keep a longer clip (video/OWNER_CLIPS.txt)"
                    )
                inputs += ["-ss", f"{start:.3f}", "-i", str(video.resolve())]
                filters, label = blur_chain(boxes.get(part.target, []), f"[{index}:v]", f"b{k}_")
                if filters:
                    graph.append(filters)
                graph.append(f"{label}fps={bs.FPS},trim=end_frame={count},setpts=PTS-STARTPTS,{FIT}[p{k}]")
                hashed.append(bs.sha256_file(video))
                info.update({"clip": video.name, "trim_start": start, "blur_boxes": len(boxes.get(part.target, []))})
        else:
            still: Path | None = None
            if (clips / f"still-{part.target}.json").is_file():
                still, meta = clip_receipt(clips, f"still-{part.target}", release_sha, media=".png")
                if meta.get("placeholder"):
                    card = (str(meta["placeholder"]), f"placeholder: not built at {release_sha[:7]}")
                    info["placeholder"] = meta["placeholder"]
                    still = None
            elif (stills / f"{part.target}.png").is_file():
                still = stills / f"{part.target}.png"
            else:
                raise ComposeError(f"{beat.id}: no still-{part.target}.json in {clips} and no {part.target}.png in {stills}")
            if still is not None:
                inputs += ["-loop", "1", "-framerate", str(bs.FPS), "-i", str(still.resolve())]
                graph.append(f"[{index}:v]fps={bs.FPS},trim=end_frame={count},setpts=PTS-STARTPTS,{FIT}[p{k}]")
                hashed.append(bs.sha256_file(still))
                info["clip"] = still.name
        info["run_id"] = meta.get("run_id")
        info["facts_checked"] = meta.get("facts_checked", [])
        info["facts_failed"] = meta.get("facts_failed", [])
        if info["facts_failed"] and not rehearsal:
            raise ComposeError(
                f"{beat.id}: narrated facts did not hold on the live site: {'; '.join(info['facts_failed'])}; "
                "only a rehearsal may compose such a clip"
            )
        if card is not None:
            inputs += ["-f", "lavfi", "-i", f"color=c={bs.CARD_BACKGROUND}:s={bs.WIDTH}x{bs.VISUAL_HEIGHT}:r={bs.FPS}:d={seconds + 1:.3f}"]
            graph.append(f"[{index}:v]trim=end_frame={count},setpts=PTS-STARTPTS,setsar=1[p{k}]")
            cards.append((at_ms, at_ms + count * bs.FRAME_MS, card[0], card[1]))
        parts_out.append(info)
        at_ms += count * bs.FRAME_MS
    joined = "".join(f"[p{k}]" for k in range(len(frames)))
    graph.append(f"{joined}concat=n={len(frames)}:v=1:a=0[pv]" if len(frames) > 1 else "[p0]null[pv]")
    ass_name = f"{beat.stem}.ass"
    ass_text = segment_ass(row["cues"], int(row["start_ms"]), cards)
    graph.append(f"[pv]pad={bs.WIDTH}:{bs.HEIGHT}:0:0:color={bs.BACKGROUND},ass={ass_name},format=yuv420p[v]")
    out = seg_dir / f"{beat.stem}.mp4"
    encode = ["-map", "[v]", "-frames:v", str(window), "-c:v", "libx264", "-preset", "medium", "-crf", SEGMENT_CRF,
              "-pix_fmt", "yuv420p", "-r", str(bs.FPS), "-g", str(2 * bs.FPS), "-bf", "0", "-an",
              "-video_track_timescale", str(bs.FPS * 512)]
    filter_graph = ";".join(graph)
    key = hashlib.sha256(json.dumps([inputs, filter_graph, encode, ass_text, hashed]).encode("utf-8")).hexdigest()
    side = seg_dir / f"{beat.stem}.json"
    reused = out.is_file() and side.is_file() and json.loads(side.read_text(encoding="utf-8")).get("key") == key
    if not reused:
        (seg_dir / ass_name).write_text(ass_text, encoding="utf-8")
        # ffmpeg runs inside the segment folder so the ass filter gets a bare file name:
        # a Windows drive colon breaks the filter's path parsing.
        bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", *inputs, "-filter_complex", filter_graph, *encode, out.name], cwd=seg_dir)
        got = bs.counted_frames(out)
        if got != window:
            raise ComposeError(f"{beat.id}: the segment has {got} frames, the window is {window}; an input ran short")
        side.write_text(json.dumps({"key": key, "frames": window}) + "\n", encoding="utf-8")
    return {"file": out.name, "parts": parts_out, "reused": reused}


# --------------------------------------------------------------------- audio


def _loudnorm_json(stderr: str) -> dict[str, Any]:
    start, end = stderr.rfind("{"), stderr.rfind("}")
    if start == -1 or end < start:
        raise ComposeError("loudnorm printed no measurement")
    return json.loads(stderr[start : end + 1])


def build_audio(timing: dict[str, Any], narration: Path, work: Path) -> dict[str, Any]:
    rows = timing["beats"]
    total_samples = int(timing["total_frames"]) * bs.SAMPLES_PER_FRAME
    inputs, graph = [], []
    for k, row in enumerate(rows):
        inputs += ["-i", str((narration / row["audio"]).resolve())]
        samples = int(row["window_frames"]) * bs.SAMPLES_PER_FRAME
        graph.append(
            f"[{k}:a]aresample={bs.SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=mono,"
            f"adelay=delays={bs.LEAD_IN_MS}:all=1,apad=whole_len={samples},atrim=end_sample={samples},asetpts=N/SR/TB[a{k}]"
        )
    graph.append("".join(f"[a{k}]" for k in range(len(rows))) + f"concat=n={len(rows)}:v=0:a=1[cat]")
    raw = work / "narration-raw.wav"
    bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[cat]",
            "-c:a", "pcm_s16le", "-ar", str(bs.SAMPLE_RATE), "-ac", "1", str(raw)])
    if bs.decoded_samples(raw) != total_samples:
        raise ComposeError(f"the joined narration has {bs.decoded_samples(raw)} samples, the timeline {total_samples}")
    target = ":".join(f"{k}={v}" for k, v in LOUDNESS.items())
    first = bs.run([bs.tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(raw), "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"])
    measured = _loudnorm_json(first.stderr)
    final = work / "narration.wav"
    tail = f"aresample={bs.SAMPLE_RATE},apad=whole_len={total_samples},atrim=end_sample={total_samples}"
    if measured.get("input_i") in ("-inf", "inf") or float(measured.get("input_i", "-99")) < -70:
        bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", "-i", str(raw), "-af", tail, "-c:a", "pcm_s16le", "-ac", "2", str(final)])
        return {"target_i": LOUDNESS["I"], "input_i": measured.get("input_i"), "output_i": None, "applied": False}
    second = bs.run([
        bs.tool("ffmpeg"), "-hide_banner", "-nostats", "-y", "-i", str(raw), "-af",
        f"loudnorm={target}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:"
        f"linear=true:print_format=json,{tail}",
        "-c:a", "pcm_s16le", "-ac", "2", str(final),
    ])
    applied = _loudnorm_json(second.stderr)
    if bs.decoded_samples(final) != total_samples:
        raise ComposeError("loudnorm changed the narration length")
    return {"target_i": LOUDNESS["I"], "input_i": measured.get("input_i"), "output_i": applied.get("output_i"),
            "normalization_type": applied.get("normalization_type"), "applied": True}


# --------------------------------------------------------------------- main


def clip_runs(clips: Path) -> set[str]:
    """Every run id the capture receipts in the clips folder name."""
    runs = set()
    for path in clips.glob("*.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(meta, dict) and meta.get("format") == bs.CLIP_FORMAT and meta.get("run_id"):
            runs.add(str(meta["run_id"]))
    return runs


def check_teams_run(final: bool, teams_run: str | None, runs: set[str]) -> None:
    """The owner's Teams clips must show the run the web clips and stills show; a final cut names it."""
    if len(runs) > 1:
        raise ComposeError(f"the capture receipts show different runs: {', '.join(sorted(runs))}; capture them again together")
    if not final:
        return
    if not teams_run or not bs.RUN_ID.match(teams_run):
        raise ComposeError("--teams-dir needs --teams-run RUN_ID: the run whose approval the Teams clips recorded")
    if runs and teams_run not in runs:
        raise ComposeError(
            f"the Teams clips record run {teams_run}, the web clips and stills show {', '.join(sorted(runs))}; "
            "make that run the live site's default run and capture again before the final cut (video/OWNER_CLIPS.txt)"
        )


def compose(args: argparse.Namespace) -> dict[str, Any]:
    if not bs.SHA40.match(args.release_sha):
        raise ComposeError("--release-sha must be the 40-hex commit the live site serves")
    sheet = bs.load_sheet(args.beats)
    timing = json.loads((args.narration / "timing.json").read_text(encoding="utf-8"))
    if timing.get("format") != bs.TIMING_FORMAT or timing.get("fps") != bs.FPS:
        raise ComposeError("timing.json is not a narration timeline at 25 fps")
    by_id = {b.id: b for b in sheet.beats}
    for row in timing["beats"]:
        if row["id"] not in by_id or by_id[row["id"]].narration != row["narration"]:
            raise ComposeError(f"timing.json beat {row['id']} does not match the beat sheet's narration; narrate again")
    mode = "final" if args.teams_dir else "preview"
    check_teams_run(args.teams_dir is not None, args.teams_run, clip_runs(args.clips))
    out: Path = args.out
    seg_dir = out / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    boxes, trims = parse_boxes(args.blur), parse_trims(args.trim)

    segments, beats_out = [], []
    for row in timing["beats"]:
        beat = by_id[row["id"]]
        seg = render_segment(beat, row, sheet=sheet, clips=args.clips, teams_dir=args.teams_dir, stills=args.stills,
                             release_sha=args.release_sha, boxes=boxes, trims=trims, seg_dir=seg_dir,
                             rehearsal=args.rehearsal)
        segments.append(seg["file"])
        beats_out.append({
            "id": beat.id, "index": beat.index, "start_ms": row["start_ms"], "window_ms": row["window_ms"],
            "window_frames": row["window_frames"], "audio_ms": row["audio_ms"], "narrated": True,
            "captions_burned": bool(row["cues"]), "cues": len(row["cues"]), "parts": seg["parts"], "segment_reused": seg["reused"],
            "facts_failed": [f for p in seg["parts"] for f in p.get("facts_failed", [])],
        })
        print(f"{beat.stem}: {row['window_frames']} frames, {'reused' if seg['reused'] else 'rendered'}"
              + "".join(f"; {p['source']}{' (placeholder)' if p['placeholder'] else ''}" for p in seg["parts"]))

    run_ids = {p["run_id"] for b in beats_out for p in b["parts"] if p.get("run_id")}
    if len(run_ids) > 1:
        raise ComposeError(f"the clips show different runs: {', '.join(sorted(run_ids))}; capture them again together")
    run_id = run_ids.pop() if run_ids else sheet.run_id

    (seg_dir / "concat.txt").write_text("".join(f"file '{name}'\n" for name in segments), encoding="utf-8")
    video_only = out / "video-only.mp4"
    bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy",
            str(video_only.resolve())], cwd=seg_dir)
    frames = bs.counted_frames(video_only)
    if frames != timing["total_frames"]:
        raise ComposeError(f"the joined picture has {frames} frames, the timeline {timing['total_frames']}")

    loudness = build_audio(timing, args.narration, out)
    name = f"agon-video-{mode}.mp4"
    final = out / name
    bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", "-i", str(video_only), "-i", str(out / "narration.wav"),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(bs.SAMPLE_RATE),
            "-movflags", "+faststart", "-metadata", f"comment=agon release {args.release_sha}", str(final)])

    srt = out / "captions.srt"
    srt.write_text((args.narration / "captions.srt").read_text(encoding="utf-8"), encoding="utf-8")
    (out / "timing.json").write_text(json.dumps(timing, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "format": bs.RECEIPT_FORMAT,
        "file": name,
        "mode": mode,
        "release_sha": args.release_sha,
        "run_id": run_id,
        "teams_run": args.teams_run,
        "rehearsal": bool(args.rehearsal),
        "facts_failed": [f"{b['id']}: {f}" for b in beats_out for f in b["facts_failed"]],
        "site": sheet.site,
        "narration": {"engine": timing.get("engine"), "voice": timing.get("voice"), "stand_in": bool(timing.get("stand_in"))},
        "fps": bs.FPS, "width": bs.WIDTH, "height": bs.HEIGHT,
        "beats": beats_out,
        "total_frames": frames,
        "total_ms": frames * bs.FRAME_MS,
        "total_seconds": frames / bs.FPS,
        "loudness": loudness,
        "captions_srt_sha256": bs.sha256_file(srt),
        "ffmpeg": bs.ffmpeg_version(),
        "composed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": bs.sha256_file(final),
        "bytes": final.stat().st_size,
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    for leftover in (video_only, out / "narration-raw.wav"):
        leftover.unlink(missing_ok=True)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose the video from narration, clips and Teams recordings.")
    parser.add_argument("--beats", type=Path, required=True)
    parser.add_argument("--narration", type=Path, required=True, help="folder with timing.json, captions.srt and audio/")
    parser.add_argument("--clips", type=Path, required=True, help="folder with <beat>.webm and <beat>.json from the capture")
    parser.add_argument("--stills", type=Path, default=Path("video/stills"))
    parser.add_argument("--teams-dir", type=Path, help="local folder with the owner's Teams clips (final cut); never committed")
    parser.add_argument("--teams-run", help="the run whose approval the Teams clips recorded; required with --teams-dir")
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--blur", action="append", default=[], help="CLIP:X,Y,W,H, repeatable")
    parser.add_argument("--trim", action="append", default=[], help="CLIP=SECONDS, overrides the sheet's trim_start")
    parser.add_argument("--rehearsal", action="store_true",
                        help="pull request rehearsal only: compose clips whose narrated facts did not hold, and say so")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = compose(args)
    except (ComposeError, bs.BeatSheetError, bs.MediaError) as exc:
        print(f"FAIL: {exc}")
        if args.summary:
            with args.summary.open("a", encoding="utf-8") as fh:
                fh.write(f"\n**Compose failed:** {exc}\n")
        return 1
    print(f"{receipt['file']}: {receipt['total_seconds']:.3f} s, {receipt['total_frames']} frames, sha256 {receipt['sha256']}, "
          f"loudness {receipt['loudness'].get('output_i')} LUFS ({receipt['mode']}, narration {receipt['narration']['engine']})")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(f"\n### Composed {receipt['file']} ({receipt['mode']})\n\n"
                     f"{receipt['total_seconds']:.3f} s, {len(receipt['beats'])} beats, release `{receipt['release_sha']}`, "
                     f"sha256 `{receipt['sha256']}`, narration {receipt['narration']['engine']}"
                     f"{' (stand-in, not the submitted voice)' if receipt['narration']['stand_in'] else ''}.\n")
    if receipt["facts_failed"]:
        print("narrated facts not on the live site (rehearsal only):")
        for line in receipt["facts_failed"]:
            print(f"  {line}")
        if args.summary:
            with args.summary.open("a", encoding="utf-8") as fh:
                fh.write("\n**Narrated facts not on the live site (rehearsal only; video.yml refuses these):**\n\n")
                fh.write("".join(f"- {line}\n" for line in receipt["facts_failed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
