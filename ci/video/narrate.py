"""Synthesise each beat's narration on its own, measure it, and write the timeline and captions.

Engines:
  azure   Azure AI Speech text to speech, keyless: a Microsoft Entra token for the CI
          identity (AGON_SPEECH_TOKEN, from `az account get-access-token --resource
          https://cognitiveservices.azure.com`) sent as `Bearer aad#<resource id>#<token>`
          to <endpoint>tts/cognitiveservices/v1, the form src/agon/fleet/azure_ports.py
          uses for the fleet. Needs AGON_SPEECH_ENDPOINT and AGON_SPEECH_RESOURCE_ID
          (the speechEndpoint and speechResourceId outputs of agon-ai). The voice is the
          sheet's standard neural voice; it is never called HD.
  espeak  espeak-ng on PATH. A robotic stand-in for pull-request rehearsals, so caption
          sync can be checked by ear without Azure. Never the submitted narration.
  tone    a quiet sine tone as long as the words would take. Offline self-tests only.

For every selected beat it writes audio/NN-<id>.mp3 and a sidecar NN-<id>.json whose
key hashes the engine, voice, rate and spoken text. A beat whose key is unchanged is
reused from --reuse DIR or from the output folder itself instead of synthesised again,
so editing one beat's words re-synthesises that beat only and every other measured
length stays put.

Each mp3 is measured twice, by ffprobe and by decoding every sample, and the longer
figure (in whole milliseconds) sets the beat. timing.json and captions.srt follow from
those numbers through beatsheet.build_timing; nothing downstream invents a time.

An HTTP 401 or 403 from Speech is reported as the missing role grant it almost always
is, in the console and in the step summary, and the run fails.

Usage:
  python ci/video/narrate.py --beats video/beats.json --out DIR [--only verify] \
      [--engine azure|espeak|tone] [--reuse DIR] [--summary FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))

import beatsheet as bs  # noqa: E402
from sync_gate import CAP_SECONDS  # noqa: E402

OUTPUT_FORMAT = "audio-48khz-192kbitrate-mono-mp3"
TONE_WORDS_PER_SECOND = 2.6
RETRY_STATUSES = {429, 500, 502, 503, 504}
RBAC_PR = "https://github.com/upgradedev/agon-azure/pull/37"


class SpeechAuthError(RuntimeError):
    """Speech refused the token: the CI identity lacks its role on the Speech account."""


def ssml(text: str, voice: str, locale: str, rate: str) -> str:
    body = escape(text)
    if rate not in ("0%", "+0%", "-0%"):
        body = f"<prosody rate={quoteattr(rate)}>{body}</prosody>"
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang={quoteattr(locale)}>'
        f"<voice name={quoteattr(voice)}>{body}</voice></speak>"
    )


def cache_key(engine: str, sheet: bs.Sheet, beat: bs.Beat) -> str:
    material = json.dumps(
        {"engine": engine, "voice": sheet.voice, "locale": sheet.locale, "rate": sheet.rate,
         "format": OUTPUT_FORMAT, "speech": beat.speech},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------- engines


def azure_tts(text: str, sheet: bs.Sheet, env: dict[str, str]) -> bytes:
    endpoint = env.get("AGON_SPEECH_ENDPOINT", "").rstrip("/")
    resource_id = env.get("AGON_SPEECH_RESOURCE_ID", "")
    token = env.get("AGON_SPEECH_TOKEN", "")
    if not endpoint.startswith("https://") or not resource_id.startswith("/subscriptions/") or not token:
        raise SystemExit(
            "azure engine needs AGON_SPEECH_ENDPOINT (https), AGON_SPEECH_RESOURCE_ID (/subscriptions/...) "
            "and AGON_SPEECH_TOKEN (an Entra token for https://cognitiveservices.azure.com)"
        )
    url = env.get("AGON_SPEECH_TTS_URL") or f"{endpoint}/tts/cognitiveservices/v1"
    body = ssml(text, sheet.voice, sheet.locale, sheet.rate).encode("utf-8")
    for attempt in range(1, 5):
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer aad#{resource_id}#{token}",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
                "User-Agent": "agon-video",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                audio = response.read()
            if not audio:
                raise RuntimeError("Speech returned empty audio")
            return audio
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace").strip()
            code = exc.headers.get("x-ms-error-code", "") if exc.headers else ""
            if exc.code in (401, 403):
                raise SpeechAuthError(f"HTTP {exc.code} {code} {detail}".strip()) from exc
            if exc.code in RETRY_STATUSES and attempt < 4:
                wait = float(exc.headers.get("Retry-After", 2 ** attempt)) if exc.headers else 2 ** attempt
                print(f"  Speech HTTP {exc.code}, retry {attempt} in {wait:.0f} s")
                time.sleep(min(wait, 30))
                continue
            raise RuntimeError(f"Speech HTTP {exc.code} {code} {detail}".strip()) from exc
        except urllib.error.URLError as exc:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Speech unreachable: {exc.reason}") from exc
    raise RuntimeError("Speech: retries exhausted")


def to_mp3(source: Path, target: Path) -> None:
    bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", "-i", str(source), "-ac", "1", "-ar", str(bs.SAMPLE_RATE),
            "-c:a", "libmp3lame", "-b:a", "192k", str(target)])


def espeak_tts(text: str, target: Path) -> None:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise SystemExit("espeak engine needs espeak-ng on PATH (apt-get install espeak-ng)")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "speech.wav"
        subprocess.run([exe, "-v", "en-gb", "-s", "160", "-w", str(wav), text], check=True, capture_output=True)
        to_mp3(wav, target)


def tone_tts(text: str, target: Path) -> None:
    seconds = max(1.0, len(text.split()) / TONE_WORDS_PER_SECOND)
    bs.run([bs.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
            f"sine=frequency=330:sample_rate={bs.SAMPLE_RATE}:duration={seconds:.3f}",
            "-af", "volume=0.15", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "192k", str(target)])


# --------------------------------------------------------------------- measuring


def measure_ms(path: Path) -> tuple[int, float]:
    """(decoded length in whole ms rounded up, ffprobe stream duration in s); the beat uses the larger."""
    decoded = bs.decoded_samples(path) * 1000 / bs.SAMPLE_RATE
    data = bs.probe(path, "-select_streams", "a:0", "-show_entries", "stream=duration:format=duration")
    probed = 0.0
    for value in [*(s.get("duration") for s in data.get("streams", [])), data.get("format", {}).get("duration")]:
        try:
            probed = max(probed, float(value))
        except (TypeError, ValueError):
            continue
    return math.ceil(max(decoded, probed * 1000) - 1e-6), probed


def summary_line(path: Path | None, text: str) -> None:
    if path:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Narrate each beat, measure it, write timing.json and captions.srt.")
    parser.add_argument("--beats", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", default="all", help="comma-separated beat ids, or all")
    parser.add_argument("--engine", choices=("azure", "espeak", "tone"), default="azure")
    parser.add_argument("--reuse", type=Path, help="a previous narration folder; unchanged beats are copied from it")
    parser.add_argument("--summary", type=Path, help="append markdown here (GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)

    sheet = bs.load_sheet(args.beats)
    beats = sheet.select(args.only)
    audio_dir = args.out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    stand_in = args.engine != "azure"
    summary_line(args.summary, f"\n### Narration ({args.engine}{', stand-in, not the submitted voice' if stand_in else ''})\n")

    measured: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    synthesised = reused = 0
    for beat in beats:
        mp3 = audio_dir / f"{beat.stem}.mp3"
        side = audio_dir / f"{beat.stem}.json"
        key = cache_key(args.engine, sheet, beat)
        source = None
        for folder in [d for d in (args.out, args.reuse) if d]:
            cand_mp3, cand_side = folder / "audio" / mp3.name, folder / "audio" / side.name
            if cand_mp3.is_file() and cand_side.is_file():
                meta = json.loads(cand_side.read_text(encoding="utf-8"))
                if meta.get("key") == key and meta.get("sha256") == bs.sha256_file(cand_mp3):
                    source = cand_mp3
                    break
        if source is not None:
            if source.resolve() != mp3.resolve():
                shutil.copyfile(source, mp3)
            reused += 1
            how = "reused"
        else:
            try:
                if args.engine == "azure":
                    mp3.write_bytes(azure_tts(beat.speech, sheet, dict(os.environ)))
                elif args.engine == "espeak":
                    espeak_tts(beat.speech, mp3)
                else:
                    tone_tts(beat.speech, mp3)
            except SpeechAuthError as exc:
                message = (
                    f"Azure Speech refused the CI identity ({exc}). This is the expected failure until the owner deploys "
                    f"infra/rbac.bicep with the Cognitive Services User grant for the CI identity on the Speech account "
                    f"({RBAC_PR}) and it propagates, usually a few minutes. Then dispatch this workflow again."
                )
                print(f"FAIL {beat.id}: {message}")
                summary_line(args.summary, f"**Narration failed on beat `{beat.id}`: auth error.** {message}")
                return 1
            synthesised += 1
            how = "synthesised"
        audio_ms, probed = measure_ms(mp3)
        measured[beat.id] = audio_ms
        side.write_text(json.dumps({"key": key, "engine": args.engine, "voice": sheet.voice, "rate": sheet.rate,
                                    "sha256": bs.sha256_file(mp3), "bytes": mp3.stat().st_size}, indent=2) + "\n",
                        encoding="utf-8")
        rows.append({"id": beat.id, "how": how, "audio_ms": audio_ms, "ffprobe_seconds": round(probed, 3)})
        print(f"{beat.stem}: {how}, {audio_ms} ms (ffprobe {probed:.3f} s)")

    try:
        timing = bs.build_timing(beats, measured)
    except bs.BeatSheetError as exc:
        print(f"FAIL: {exc}")
        summary_line(args.summary, f"**Timing failed:** {exc}")
        return 1
    timing.update({"engine": args.engine, "stand_in": stand_in, "voice": sheet.voice if args.engine == "azure" else args.engine,
                   "output_format": OUTPUT_FORMAT, "run_id": sheet.run_id})
    for row, info in zip(timing["beats"], rows):
        row["audio"] = f"audio/{row['stem']}.mp3"
        row["audio_sha256"] = bs.sha256_file(args.out / row["audio"])
        row["ffprobe_seconds"] = info["ffprobe_seconds"]
    (args.out / "timing.json").write_text(json.dumps(timing, indent=2) + "\n", encoding="utf-8")
    (args.out / "captions.srt").write_text(bs.write_srt(bs.timing_cues(timing)), encoding="utf-8")

    total_s = timing["total_ms"] / 1000
    print(f"narrated {len(beats)} beats: {synthesised} synthesised, {reused} reused; total {total_s:.3f} s "
          f"({timing['total_frames']} frames at {bs.FPS} fps)")
    summary_line(args.summary, "| Beat | Narration | Window | How |\n|---|---|---|---|")
    for row, info in zip(timing["beats"], rows):
        summary_line(args.summary, f"| {row['id']} | {row['audio_ms'] / 1000:.3f} s | {row['window_ms'] / 1000:.2f} s | {info['how']} |")
    summary_line(args.summary, f"\nTotal {total_s:.3f} s; the cap is under {CAP_SECONDS} s.")
    if not total_s < CAP_SECONDS:
        print(f"FAIL: the narrated timeline is {total_s:.3f} s, not under the cap of {CAP_SECONDS} s")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
