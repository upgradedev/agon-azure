"""The beat sheet of the submission video, its timing model and its caption files.

One module shared by narrate.py, compose.py, sync_gate.py and the tests, so the
frame grid, the caption band and the cue arithmetic exist once.

Timing model (per-beat audio lock). Every beat's narration is synthesised on its
own and its decoded length measured. The beat's window is

    lead-in + measured narration + tail, rounded UP to whole frames at 25 fps,
    raised to the beat's min_seconds, and refused above its max_seconds.

Beats follow each other on that frame grid, so a beat's start is a whole number
of frames and every later beat moves only when an earlier window changes. A
frame is 40 ms and 1920 audio samples at 48 kHz, so video and audio lengths are
exact integers. Caption cues are never authored: each beat's caption text is
split into short chunks that share the measured speech time in proportion to
their length, so the SRT is a consequence of the audio.

Picture layout: the visual (web capture, Teams clip, still or card) is scaled to
fit 1920 x 930 at the top of the frame, and captions are burned into the
150-pixel band below it, white on a solid dark band. Captions never sit on top
of a light page, and the sync gate can read them back from the pixels.

Usage: python ci/video/beatsheet.py --check video/beats.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FPS = 25
FRAME_MS = 1000 // FPS  # 40
WIDTH = 1920
HEIGHT = 1080
VISUAL_HEIGHT = 930
BAND_HEIGHT = HEIGHT - VISUAL_HEIGHT  # 150
SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS  # 1920
LEAD_IN_MS = 320  # 8 frames of quiet picture before the voice starts
TAIL_MS = 480  # 12 frames after it ends, so no beat cuts on the last syllable
CUE_HOLD_MS = 200  # the last cue stays up a little after the voice stops
CAPTION_MAX_CHARS = 84  # two lines of 42, the usual subtitle limit
BACKGROUND = "0x0b0f14"  # behind the visual and in the caption band
CARD_BACKGROUND = "0x16202c"  # placeholder cards
WORDS_PER_SECOND_ESTIMATE = 2.5  # ESTIMATE for warnings only, never for timing

BEATS_FORMAT = "agon.video-beats/1"
TIMING_FORMAT = "agon.video-timing/1"
CLIP_FORMAT = "agon.video-clip/1"
RECEIPT_FORMAT = "agon.video-receipt/1"

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
DASHES = (chr(0x2014), chr(0x2013))  # em and en dash, named by code point so this file has none
SOURCE_KINDS = ("web", "teams", "still")
VIEWPORTS = {"desktop": (1920, 1080), "phone": (375, 812)}


class BeatSheetError(ValueError):
    """The beat sheet, the timing or a caption file breaks a rule."""


# --------------------------------------------------------------------- beat sheet


@dataclass(frozen=True)
class Part:
    kind: str  # web, teams or still
    target: str  # web route, Teams clip name or still name
    seconds: float | None  # every part but the last names its length
    viewport: str
    trim_start: float
    facts: tuple[dict[str, str], ...]
    actions: str


@dataclass(frozen=True)
class Beat:
    index: int  # 1-based position in the full sheet
    id: str
    narration: str
    speech: str
    parts: tuple[Part, ...]
    min_seconds: float
    max_seconds: float

    @property
    def stem(self) -> str:
        return f"{self.index:02d}-{self.id}"

    def part(self, kind: str) -> Part | None:
        return next((p for p in self.parts if p.kind == kind), None)


@dataclass(frozen=True)
class Sheet:
    voice: str
    locale: str
    rate: str
    site: str
    run_id: str
    theme: str
    target_seconds: tuple[float, float]
    beats: tuple[Beat, ...]

    def select(self, only: str | None) -> list[Beat]:
        """Beats named in a comma-separated list, in sheet order; all for None, '' or 'all'."""
        if not only or only.strip() == "all":
            return list(self.beats)
        wanted = [name.strip() for name in only.split(",") if name.strip()]
        known = {b.id for b in self.beats}
        unknown = [name for name in wanted if name not in known]
        if unknown:
            raise BeatSheetError(f"unknown beat ids: {', '.join(unknown)}; the sheet has {', '.join(sorted(known))}")
        return [b for b in self.beats if b.id in wanted]


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BeatSheetError(f"{where} must be a number")
    return float(value)


def _text(value: Any, where: str, *, limit: int = 600) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BeatSheetError(f"{where} must be non-empty text")
    if any(d in value for d in DASHES):
        raise BeatSheetError(f"{where} contains an em or en dash")
    if len(value) > limit:
        raise BeatSheetError(f"{where} is longer than {limit} characters")
    return value.strip()


def check_voice(voice: str) -> str:
    """A standard neural voice name; never one labelled HD (standard voices are not HD)."""
    if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural", voice) or "HD" in voice:
        raise BeatSheetError(f"voice {voice!r} must be a standard neural voice such as en-GB-SoniaNeural, never an HD name")
    return voice


def _part(raw: Any, where: str, last: bool) -> Part:
    if isinstance(raw, str):
        raw = {"source": raw}
    if not isinstance(raw, dict):
        raise BeatSheetError(f"{where} must be a source string or an object")
    source = _text(raw.get("source"), f"{where}.source")
    kind, _, target = source.partition(":")
    if kind not in SOURCE_KINDS or not target:
        raise BeatSheetError(f"{where}.source {source!r} must be web:<route>, teams:<clip> or still:<name>")
    if kind == "web" and not target.startswith("#/"):
        raise BeatSheetError(f"{where}.source: a web route starts with #/ ({target!r})")
    if kind in ("teams", "still") and not SLUG.match(target):
        raise BeatSheetError(f"{where}.source: {kind} name {target!r} must be a lowercase slug")
    seconds = raw.get("seconds")
    if last and seconds is not None:
        raise BeatSheetError(f"{where}: the last part takes the rest of the beat, so it names no seconds")
    if not last:
        seconds = _number(seconds, f"{where}.seconds")
        if seconds <= 0:
            raise BeatSheetError(f"{where}.seconds must be positive")
    viewport = raw.get("viewport", "desktop")
    if viewport not in VIEWPORTS:
        raise BeatSheetError(f"{where}.viewport must be one of {', '.join(VIEWPORTS)}")
    trim = _number(raw.get("trim_start", 0), f"{where}.trim_start")
    if trim < 0:
        raise BeatSheetError(f"{where}.trim_start must not be negative")
    facts = raw.get("facts", [])
    if not isinstance(facts, list):
        raise BeatSheetError(f"{where}.facts must be a list")
    clean_facts = []
    for n, fact in enumerate(facts):
        if not isinstance(fact, dict) or set(fact) - {"text", "within"}:
            raise BeatSheetError(f"{where}.facts[{n}] must be an object with text and an optional within")
        clean = {"text": _text(fact.get("text"), f"{where}.facts[{n}].text", limit=200)}
        if "within" in fact:
            clean["within"] = _text(fact["within"], f"{where}.facts[{n}].within", limit=200)
        try:
            re.compile(clean["text"])
        except re.error as exc:
            raise BeatSheetError(f"{where}.facts[{n}].text is not a valid pattern: {exc}") from exc
        clean_facts.append(clean)
    if facts and kind != "web":
        raise BeatSheetError(f"{where}: only a web part can carry facts the capture checks on the page")
    actions = raw.get("actions", "")
    if not isinstance(actions, str) or any(d in actions for d in DASHES):
        raise BeatSheetError(f"{where}.actions must be text without em or en dashes")
    return Part(kind, target, seconds, viewport, trim, tuple(clean_facts), actions)


def parse_sheet(data: Any) -> Sheet:
    """Validate a decoded beats.json and return it typed; raises BeatSheetError on the first problem."""
    if not isinstance(data, dict) or data.get("format") != BEATS_FORMAT:
        raise BeatSheetError(f"the beat sheet must be an object with format {BEATS_FORMAT}")
    voice = check_voice(_text(data.get("voice"), "voice"))
    locale = _text(data.get("locale"), "locale")
    if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", locale) or not voice.startswith(locale + "-"):
        raise BeatSheetError(f"locale {locale!r} must match the voice {voice!r}")
    rate = data.get("rate", "0%")
    if not isinstance(rate, str) or not re.fullmatch(r"[+-]?\d{1,2}%", rate):
        raise BeatSheetError("rate must be a percentage such as 0% or +5%")
    site = _text(data.get("site"), "site")
    if not re.fullmatch(r"https://[a-z0-9.-]+", site):
        raise BeatSheetError("site must be an https origin with no path")
    run_id = _text(data.get("run_id"), "run_id")
    if not RUN_ID.match(run_id):
        raise BeatSheetError("run_id must be a run slug such as run-c8a19020c98a")
    theme = data.get("theme", "dark")
    if theme not in ("dark", "light"):
        raise BeatSheetError("theme must be dark or light")
    target = data.get("target_seconds")
    if not isinstance(target, list) or len(target) != 2:
        raise BeatSheetError("target_seconds must be [low, high]")
    low, high = (_number(v, "target_seconds") for v in target)
    raw_beats = data.get("beats")
    if not isinstance(raw_beats, list) or not raw_beats:
        raise BeatSheetError("beats must be a non-empty list")
    beats: list[Beat] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_beats, start=1):
        where = f"beats[{index - 1}]"
        if not isinstance(raw, dict):
            raise BeatSheetError(f"{where} must be an object")
        beat_id = raw.get("id")
        if not isinstance(beat_id, str) or not SLUG.match(beat_id) or beat_id in seen:
            raise BeatSheetError(f"{where}.id must be a unique lowercase slug")
        seen.add(beat_id)
        narration = _text(raw.get("narration"), f"{beat_id}.narration")
        speech = _text(raw.get("speech", narration), f"{beat_id}.speech")
        visual = raw.get("visual")
        if isinstance(visual, (str, dict)):
            visual = [visual]
        if not isinstance(visual, list) or not visual:
            raise BeatSheetError(f"{beat_id}.visual must be a source or a list of parts")
        parts = tuple(_part(p, f"{beat_id}.visual[{n}]", n == len(visual) - 1) for n, p in enumerate(visual))
        for kind in ("web", "teams"):
            if sum(p.kind == kind for p in parts) > 1:
                raise BeatSheetError(f"{beat_id}: at most one {kind} part per beat (its clip is named after the beat)")
        lo = _number(raw.get("min_seconds"), f"{beat_id}.min_seconds")
        hi = _number(raw.get("max_seconds"), f"{beat_id}.max_seconds")
        if not 0 < lo <= hi:
            raise BeatSheetError(f"{beat_id}: need 0 < min_seconds <= max_seconds")
        fixed = sum(p.seconds or 0.0 for p in parts)
        if fixed >= lo:
            raise BeatSheetError(f"{beat_id}: the named part lengths ({fixed} s) leave nothing for the last part within min_seconds")
        beats.append(Beat(index, beat_id, narration, speech, parts, lo, hi))
    return Sheet(voice, locale, rate, site, run_id, theme, (low, high), tuple(beats))


def load_sheet(path: Path) -> Sheet:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BeatSheetError(f"{path}: {exc}") from exc
    return parse_sheet(data)


def sheet_warnings(sheet: Sheet, cap_seconds: float) -> tuple[list[str], list[str]]:
    """(errors, warnings) that need the whole sheet. Word-rate figures are ESTIMATES."""
    errors: list[str] = []
    warnings: list[str] = []
    floor_total = sum(b.min_seconds for b in sheet.beats)
    if floor_total >= cap_seconds:
        errors.append(f"the min_seconds alone add up to {floor_total:.2f} s, not under the cap of {cap_seconds} s")
    estimate_total = 0.0
    for b in sheet.beats:
        words = len(b.speech.split())
        estimate = (LEAD_IN_MS + TAIL_MS) / 1000 + words / WORDS_PER_SECOND_ESTIMATE
        window = max(b.min_seconds, estimate)
        estimate_total += window
        if estimate > b.max_seconds:
            warnings.append(
                f"ESTIMATE: {b.id} has {words} words, about {estimate:.1f} s at {WORDS_PER_SECOND_ESTIMATE} words a second, "
                f"over its max_seconds {b.max_seconds}; the measured narration decides"
            )
    low, high = sheet.target_seconds
    if not low <= estimate_total <= high:
        warnings.append(f"ESTIMATE: the sheet runs about {estimate_total:.1f} s, outside the target {low} to {high} s")
    return errors, warnings


# --------------------------------------------------------------------- captions


def _split_long(text: str, limit: int) -> list[str]:
    """Split one sentence at the clause or word boundary nearest its middle until each piece fits."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    middle = len(text) / 2
    clause = [m.end() for m in re.finditer(r"[,;:] ", text)]
    spaces = [m.start() for m in re.finditer(r" ", text)]
    candidates = [(abs(i - middle), i) for i in clause if 0.25 * len(text) <= i <= 0.75 * len(text)]
    if not candidates:
        candidates = [(abs(i - middle), i) for i in spaces]
    if not candidates:
        return [text]  # one unbreakable word; leave it whole
    _, at = min(candidates)
    return _split_long(text[:at], limit) + _split_long(text[at:], limit)


def split_caption(text: str, limit: int = CAPTION_MAX_CHARS) -> list[str]:
    """Caption chunks of at most `limit` characters, in order, covering every word once."""
    words = " ".join(text.split())
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", words) if s]
    pieces: list[str] = []
    for sentence in sentences:
        pieces.extend(_split_long(sentence, limit))
    merged: list[str] = []
    for piece in pieces:
        if merged and (len(merged[-1]) < 24 or len(piece) < 16) and len(merged[-1]) + 1 + len(piece) <= limit:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def allocate_cues(chunks: list[str], start_ms: int, end_ms: int) -> list[tuple[int, int, str]]:
    """Contiguous cues over [start_ms, end_ms], each lasting in proportion to its characters."""
    if not chunks or end_ms <= start_ms:
        raise BeatSheetError("a cue window needs text and a positive length")
    weights = [max(len(c), 1) for c in chunks]
    total = sum(weights)
    span = end_ms - start_ms
    cues = []
    at = start_ms
    running = 0
    for chunk, weight in zip(chunks, weights):
        running += weight
        stop = start_ms + round(span * running / total)
        cues.append((at, stop, chunk))
        at = stop
    return cues


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def _srt_time(ms: int) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_srt(cues: Iterable[Cue]) -> str:
    blocks = []
    for n, cue in enumerate(cues, start=1):
        blocks.append(f"{n}\n{_srt_time(cue.start_ms)} --> {_srt_time(cue.end_ms)}\n{cue.text}\n")
    return "\n".join(blocks)


SRT_TIME = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$")


def _parse_srt_time(text: str) -> int:
    m = SRT_TIME.match(text.strip())
    if not m:
        raise BeatSheetError(f"bad SRT time {text!r}")
    h, mi, s, ms = (int(g) for g in m.groups())
    if mi > 59 or s > 59:
        raise BeatSheetError(f"bad SRT time {text!r}")
    return ((h * 60 + mi) * 60 + s) * 1000 + ms


def parse_srt(text: str) -> list[Cue]:
    """Cues in file order. Malformed blocks raise; order and overlap are the gate's to judge."""
    cues = []
    blocks = [b for b in re.split(r"\r?\n\s*\r?\n", text.strip()) if b.strip()]
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit() or "-->" not in lines[1]:
            raise BeatSheetError(f"bad SRT block: {block[:80]!r}")
        start, _, end = lines[1].partition("-->")
        cues.append(Cue(_parse_srt_time(start), _parse_srt_time(end), " ".join(l.strip() for l in lines[2:])))
    return cues


# --------------------------------------------------------------------- timing


def frames_up(ms: float) -> int:
    return math.ceil(ms / FRAME_MS - 1e-9)


def build_timing(beats: list[Beat], audio_ms: dict[str, int]) -> dict[str, Any]:
    """The timeline for the selected beats from their measured narration lengths (ms)."""
    rows = []
    at_frames = 0
    for beat in beats:
        if beat.id not in audio_ms or audio_ms[beat.id] <= 0:
            raise BeatSheetError(f"{beat.id}: no measured narration length")
        speech_ms = audio_ms[beat.id]
        need = frames_up(LEAD_IN_MS + speech_ms + TAIL_MS)
        least = frames_up(beat.min_seconds * 1000)
        most = math.floor(beat.max_seconds * FPS + 1e-9)
        window = max(need, least)
        if window > most:
            raise BeatSheetError(
                f"{beat.id}: the narration measures {speech_ms} ms, so the beat needs {window * FRAME_MS} ms with lead-in "
                f"and tail, over its max_seconds {beat.max_seconds}; shorten the narration or raise max_seconds"
            )
        start_ms = at_frames * FRAME_MS
        speech_start = start_ms + LEAD_IN_MS
        speech_end = speech_start + speech_ms
        cues = allocate_cues(split_caption(beat.narration), speech_start, speech_end)
        last_start, last_end, last_text = cues[-1]
        cues[-1] = (last_start, min(last_end + CUE_HOLD_MS, start_ms + window * FRAME_MS - FRAME_MS), last_text)
        rows.append(
            {
                "id": beat.id,
                "index": beat.index,
                "stem": beat.stem,
                "narration": beat.narration,
                "audio_ms": speech_ms,
                "start_frames": at_frames,
                "window_frames": window,
                "start_ms": start_ms,
                "window_ms": window * FRAME_MS,
                "speech_start_ms": speech_start,
                "speech_end_ms": speech_end,
                "cues": [{"start_ms": a, "end_ms": b, "text": t} for a, b, t in cues],
            }
        )
        at_frames += window
    return {
        "format": TIMING_FORMAT,
        "fps": FPS,
        "sample_rate": SAMPLE_RATE,
        "lead_in_ms": LEAD_IN_MS,
        "tail_ms": TAIL_MS,
        "beats": rows,
        "total_frames": at_frames,
        "total_ms": at_frames * FRAME_MS,
    }


def timing_cues(timing: dict[str, Any]) -> list[Cue]:
    return [Cue(c["start_ms"], c["end_ms"], c["text"]) for row in timing["beats"] for c in row["cues"]]


# --------------------------------------------------------------------- media tools


def tool(name: str) -> str:
    """ffmpeg or ffprobe from FFMPEG / FFPROBE, else from PATH."""
    return os.environ.get(name.upper()) or name


class MediaError(RuntimeError):
    """An ffmpeg or ffprobe call failed or returned something unusable."""


def run(cmd: list[str], *, cwd: Path | None = None, binary: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=not binary)
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        tail = "\n".join(err.strip().splitlines()[-12:])
        raise MediaError(f"{Path(cmd[0]).name} exited {proc.returncode}: {' '.join(cmd[1:])[:400]}\n{tail}")
    return proc


def probe(path: Path, *args: str) -> dict[str, Any]:
    proc = run([tool("ffprobe"), "-v", "error", "-of", "json", *args, str(path)])
    return json.loads(proc.stdout or "{}")


def decoded_samples(path: Path, stream: str = "a:0", rate: int = SAMPLE_RATE) -> int:
    """Samples per channel after decoding the whole stream, resampled to `rate`, mono."""
    proc = subprocess.Popen(
        [tool("ffmpeg"), "-v", "error", "-i", str(path), "-map", f"0:{stream}", "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    total = 0
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise MediaError(f"decoding {path.name} failed: {err.strip()[-400:]}")
    return total // 2


def counted_frames(path: Path) -> int:
    data = probe(path, "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames")
    streams = data.get("streams") or []
    if not streams or not str(streams[0].get("nb_read_frames", "")).isdigit():
        raise MediaError(f"{path.name}: no countable video stream")
    return int(streams[0]["nb_read_frames"])


def last_video_pts(path: Path) -> float:
    """Timestamp of the last video packet, for clips whose container carries no duration."""
    proc = run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)])
    times = [float(line.split(",")[0]) for line in proc.stdout.splitlines() if line.strip() and line.split(",")[0] not in ("", "N/A")]
    if not times:
        raise MediaError(f"{path.name}: no video packets")
    return max(times)


def ffmpeg_version() -> str:
    try:
        first = run([tool("ffmpeg"), "-hide_banner", "-version"]).stdout.splitlines()[0]
    except (MediaError, OSError, IndexError):
        return "unknown"
    return first.strip()


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the video beat sheet.")
    parser.add_argument("--check", type=Path, required=True, help="path to video/beats.json")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sync_gate import CAP_SECONDS  # the cap lives in the gate only

    try:
        sheet = load_sheet(args.check)
    except BeatSheetError as exc:
        print(f"FAIL {args.check}: {exc}")
        return 1
    errors, warnings = sheet_warnings(sheet, CAP_SECONDS)
    for line in warnings:
        print(f"warning: {line}")
    for line in errors:
        print(f"FAIL: {line}")
    kinds = {}
    for b in sheet.beats:
        for p in b.parts:
            kinds[p.kind] = kinds.get(p.kind, 0) + 1
    print(
        f"{args.check}: {len(sheet.beats)} beats, voice {sheet.voice}, run {sheet.run_id}, parts "
        + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
        + f"; min_seconds total {sum(b.min_seconds for b in sheet.beats):.1f} s, cap {CAP_SECONDS} s"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
