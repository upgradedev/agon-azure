"""Gate C2 evidence: one chat completion against a Foundry model deployment.

Why: spec 11.2 S3 and gate C2 need a Foundry model call from CI in Sweden
Central on a Data Zone deployment, with the deployment name recorded. This
script makes exactly one call with a Microsoft Entra token, prints the model,
the deployment, the latency in milliseconds and the token usage, and fails on
anything but HTTP 200 with a usage block. It uses the Python standard library
only, so CI needs no SDK install.

Inference path: the Azure OpenAI v1 API, POST <endpoint>openai/v1/chat/completions
with the deployment name as "model" and no api-version parameter
(learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle, read
2026-10-19). gpt-5.6 models reason before they answer, so the request sends
max_completion_tokens with headroom for reasoning tokens and no temperature.

The token comes from the environment variable named by --token-env (default
AGON_AI_TOKEN), never from the command line, and is never printed.

Usage:
    AGON_AI_TOKEN=... python ci/foundry_smoke.py \\
        --endpoint https://<name>.openai.azure.com/ --deployment gpt-5.6-luna
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

# The only hosts a Foundry or Azure OpenAI inference call may go to, so a bad
# deployment output can never send the token anywhere else.
ALLOWED_HOST_SUFFIXES = (
    ".openai.azure.com",
    ".cognitiveservices.azure.com",
    ".services.ai.azure.com",
)
API_PATH = "openai/v1/chat/completions"
DEFAULT_PROMPT = "Reply with one short sentence: what does a football referee's full-time whistle mean?"
BODY_PREVIEW = 600


@dataclass(frozen=True)
class Response:
    """What one HTTP call returned."""

    status: int
    body: str


Transport = Callable[[str, dict[str, str], bytes, float], Response]


def chat_url(endpoint: str) -> str:
    """The v1 chat completions URL for an endpoint, refusing any non-Azure host."""
    parts = urlsplit(endpoint.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise ValueError(f"endpoint must be https: {endpoint!r}")
    if not host.endswith(ALLOWED_HOST_SUFFIXES):
        raise ValueError(f"endpoint host is not an Azure AI inference host: {host!r}")
    if parts.query or parts.fragment or (parts.path not in ("", "/")):
        raise ValueError(f"endpoint must be a bare origin with no path or query: {endpoint!r}")
    return f"https://{host}/{API_PATH}"


def request_body(deployment: str, prompt: str, max_completion_tokens: int) -> bytes:
    """The JSON body: one user message, no temperature (reasoning models reject it)."""
    payload = {
        "model": deployment,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": max_completion_tokens,
    }
    return json.dumps(payload).encode("utf-8")


def urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> Response:
    """POST with urllib; an HTTP error status is returned, not raised."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (host checked in chat_url)
            return Response(resp.status, resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.read().decode("utf-8", errors="replace"))


def hint(status: int) -> str:
    """A next step for the statuses this smoke is expected to meet."""
    if status == 401:
        return ("the token was refused: check its audience (this workflow asks for "
                "https://cognitiveservices.azure.com; the v1 API page also documents https://ai.azure.com) "
                "and that the token is for the tenant that holds the Foundry account")
    if status == 403:
        return ("the CI identity holds no inference role on this account yet: deploy infra/rbac.bicep "
                "(docs/OPERATIONS.md, owner step 3) and allow a few minutes for the grant to propagate")
    if status == 404:
        return "no such deployment on this account, or the v1 path is not served on this host"
    if status == 429:
        return "rate limited: the deployment capacity is too small for this burst, or quota is exhausted"
    return "see the response body above"


def run(endpoint: str, deployment: str, token: str, prompt: str, max_completion_tokens: int,
        timeout: float, transport: Transport) -> tuple[bool, list[str], dict[str, object]]:
    """Make the one call. Returns (passed, printable lines, evidence row)."""
    url = chat_url(endpoint)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    started = time.perf_counter()
    response = transport(url, headers, request_body(deployment, prompt, max_completion_tokens), timeout)
    latency_ms = round((time.perf_counter() - started) * 1000)

    lines = [f"POST {url}", f"deployment: {deployment}", f"http status: {response.status}",
             f"latency ms: {latency_ms}"]
    evidence: dict[str, object] = {"deployment": deployment, "status": response.status,
                                   "latency_ms": latency_ms, "url": url}
    if response.status != 200:
        lines.append(f"body: {response.body[:BODY_PREVIEW]}")
        lines.append(f"FAIL: expected HTTP 200; {hint(response.status)}")
        return False, lines, evidence

    try:
        data = json.loads(response.body)
    except json.JSONDecodeError:
        lines.append(f"FAIL: HTTP 200 but the body is not JSON: {response.body[:BODY_PREVIEW]}")
        return False, lines, evidence

    model = data.get("model") if isinstance(data, dict) else None
    usage = data.get("usage") if isinstance(data, dict) else None
    if not model or not isinstance(usage, dict) or "total_tokens" not in usage:
        lines.append("FAIL: HTTP 200 but the response names no model or carries no token usage")
        lines.append(f"body: {response.body[:BODY_PREVIEW]}")
        return False, lines, evidence

    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    choices = data.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    content = str(message.get("content") or "")
    finish = (choices[0] or {}).get("finish_reason")
    evidence.update({
        "model": model,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": reasoning,
        "total_tokens": usage.get("total_tokens"),
        "finish_reason": finish,
    })
    lines.extend([
        f"model: {model}",
        f"usage: prompt {usage.get('prompt_tokens')}, completion {usage.get('completion_tokens')} "
        f"(reasoning {reasoning}), total {usage.get('total_tokens')}",
        f"finish reason: {finish}",
        f"reply: {content[:200]!r}",
        "PASS: HTTP 200 with a model and token usage",
    ])
    return True, lines, evidence


def summary_markdown(evidence: dict[str, object], passed: bool) -> str:
    """A small table for the GitHub job summary."""
    rows = "\n".join(f"| {key} | `{value}` |" for key, value in evidence.items())
    verdict = "PASS" if passed else "FAIL"
    return f"### Foundry smoke (gate C2): {verdict}\n\n| field | value |\n|---|---|\n{rows}\n"


def main(argv: list[str] | None = None, transport: Transport = urllib_transport) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", required=True, help="bare origin, for example https://<name>.openai.azure.com/")
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--token-env", default="AGON_AI_TOKEN", help="environment variable that holds the token")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-completion-tokens", type=int, default=800)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--summary", default="", help="file to append a markdown summary to")
    parser.add_argument("--evidence", default="", help="file to write the evidence row to, as JSON")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"FAIL: environment variable {args.token_env} is empty; no token, no call")
        return 1
    try:
        passed, lines, evidence = run(args.endpoint, args.deployment, token, args.prompt,
                                      args.max_completion_tokens, args.timeout, transport)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    except OSError as exc:
        print(f"FAIL: the call did not complete: {exc.__class__.__name__}: {exc}")
        return 1
    print("\n".join(line.replace(token, "***") for line in lines))
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(summary_markdown(evidence, passed))
    if args.evidence:
        with open(args.evidence, "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, indent=2, sort_keys=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
