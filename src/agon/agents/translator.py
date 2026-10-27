"""Azure AI Translator (NMT) behind `TranslatorPort`, standard library HTTP only.

Names are protected the documented way for the Text API: the text is sent as HTML
(``textType=html``) and every club, player and league name is wrapped in
``<span class="notranslate">``, so NMT cannot transliterate "Pellmoor Harriers" into Greek and
break the per-language name check. The wrapper is removed and the HTML unescaped on the way back.

Authentication is keyless: a Microsoft Entra token for ``https://cognitiveservices.azure.com``,
sent to the resource's custom-domain endpoint. The deployed fleet job has used this request shape
(v3.0 ``/translate``) against our resource since 2026-10-19: every recorded run in web/runs carries
the el, es and fr editions (web/runs/run-*/report-{el,es,fr}.json), which exist only when this call
returns, and its card has no `edition_unavailable` flag. The spec names Text API 2026-06-06, so the
path and version stay constructor arguments, not literals in the call.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Sequence

from agon.agents.ports import TranslatorUnavailable

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
_UNWRAP = re.compile(r"<span[^>]*notranslate[^>]*>(.*?)</span>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def protect(text: str, names: Sequence[str]) -> str:
    """HTML-escape `text` and wrap every protected name, longest first, in a notranslate span."""
    escaped = html.escape(text, quote=False)
    wanted = sorted({html.escape(n, quote=False) for n in names if n}, key=lambda n: (-len(n), n))
    if not wanted:
        return escaped
    pattern = re.compile("|".join(re.escape(n) for n in wanted))
    return pattern.sub(lambda m: f'<span class="notranslate">{m.group(0)}</span>', escaped)


def unprotect(text: str) -> str:
    return html.unescape(_TAG.sub("", _UNWRAP.sub(r"\1", text)))


def entra_token_provider(scope: str = COGNITIVE_SCOPE) -> Callable[[], str]:
    """A sync token getter over DefaultAzureCredential (managed identity in Container Apps)."""
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    return lambda: credential.get_token(scope).token


class AzureTranslator:
    def __init__(
        self,
        endpoint: str,
        token: Callable[[], str],
        *,
        path: str = "/translator/text/v3.0/translate",
        api_version: str = "3.0",
        timeout: float = 30.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("the Translator endpoint must be https")
        self.endpoint = endpoint.rstrip("/")
        self.path = path
        self.api_version = api_version
        self.timeout = timeout
        self._token = token
        self._open = opener

    def build_request(self, texts: Sequence[str], target: str, protected: Sequence[str]) -> urllib.request.Request:
        query = urllib.parse.urlencode({"api-version": self.api_version, "from": "en", "to": target, "textType": "html"})
        body = json.dumps([{"Text": protect(t, protected)} for t in texts], ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(
            f"{self.endpoint}{self.path}?{query}",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=UTF-8"},
        )

    def _translate(self, texts: Sequence[str], target: str, protected: Sequence[str]) -> list[str]:
        request = self.build_request(texts, target, protected)
        try:
            with self._open(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - every failure means "not in this bundle"
            raise TranslatorUnavailable(f"Translator {target}: {type(exc).__name__}: {exc}") from exc
        try:
            out = [unprotect(item["translations"][0]["text"]) for item in data]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslatorUnavailable(f"Translator {target}: unexpected response shape") from exc
        if len(out) != len(texts):
            raise TranslatorUnavailable(f"Translator {target}: {len(out)} lines back for {len(texts)}")
        return out

    async def translate(self, texts: Sequence[str], target: str, protected: Sequence[str]) -> list[str]:
        return await asyncio.to_thread(self._translate, list(texts), target, list(protected))
