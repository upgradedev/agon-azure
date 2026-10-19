"""Azure AI Speech text to speech behind `SpeechPort`, standard library HTTP only.

The REST endpoint returns audio but no word-boundary events, so `word_timings` is None and the
captions carry sentence text without timings; word timings need the Speech SDK and are a later
step. The voices below are standard neural voices that are a safe default; the spec wants
DragonHD voices for EN, ES and FR, whose exact names are confirmed at spike S4 and then set
through `AGON_SPEECH_VOICES` (JSON, language to voice) without a code change.

Authentication is injected (`auth_header`), because the Entra header form for Speech REST is
UNVERIFIED for our resource until spike S4.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Any, Callable
from xml.sax.saxutils import escape, quoteattr

from agon.agents.ports import SpeechResult, SpeechUnavailable

DEFAULT_VOICES: dict[str, str] = {
    "en": "en-GB-SoniaNeural",
    "el": "el-GR-AthinaNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
}
LOCALES: dict[str, str] = {"en": "en-GB", "el": "el-GR", "es": "es-ES", "fr": "fr-FR"}
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"


def voices_from_env(env: dict[str, str] | None = None) -> dict[str, str]:
    raw = (os.environ if env is None else env).get("AGON_SPEECH_VOICES", "")
    voices = dict(DEFAULT_VOICES)
    if raw:
        override = json.loads(raw)
        if not isinstance(override, dict) or not set(override) <= set(DEFAULT_VOICES):
            raise ValueError("AGON_SPEECH_VOICES must map a subset of en, el, es, fr to voice names")
        voices.update({k: str(v) for k, v in override.items()})
    return voices


def ssml(text: str, language: str, voice: str) -> str:
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang={quoteattr(LOCALES[language])}>'
        f"<voice name={quoteattr(voice)}>{escape(text)}</voice></speak>"
    )


class AzureSpeech:
    def __init__(
        self,
        endpoint_url: str,
        auth_header: Callable[[], str],
        *,
        voices: dict[str, str] | None = None,
        timeout: float = 60.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not endpoint_url.startswith("https://"):
            raise ValueError("the Speech endpoint must be https")
        self.endpoint_url = endpoint_url
        self.voices = dict(voices or DEFAULT_VOICES)
        self.timeout = timeout
        self._auth = auth_header
        self._open = opener

    def build_request(self, text: str, language: str) -> urllib.request.Request:
        return urllib.request.Request(
            self.endpoint_url,
            data=ssml(text, language, self.voices[language]).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": self._auth(),
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
                "User-Agent": "agon-fleet",
            },
        )

    def _synthesize(self, text: str, language: str) -> SpeechResult:
        if language not in self.voices:
            raise SpeechUnavailable(f"no voice configured for {language}")
        try:
            with self._open(self.build_request(text, language), timeout=self.timeout) as response:
                audio = response.read()
        except Exception as exc:  # noqa: BLE001 - every failure leaves captions only
            raise SpeechUnavailable(f"Speech {language}: {type(exc).__name__}: {exc}") from exc
        if not audio:
            raise SpeechUnavailable(f"Speech {language}: empty audio")
        return SpeechResult(audio=audio, media_type="audio/mpeg", voice=self.voices[language], word_timings=None)

    async def synthesize(self, text: str, language: str) -> SpeechResult:
        return await asyncio.to_thread(self._synthesize, text, language)
