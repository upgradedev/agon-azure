"""Proof that the gate C2 smoke passes only on HTTP 200 with a model and usage.

The network is replaced by a fake transport, so these tests make no call.
CI makes the real call in .github/workflows/foundry-smoke.yml.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import foundry_smoke

ENDPOINT = "https://aif-agon-example.openai.azure.com/"
TOKEN = "eyJ.fake.token"
OK_BODY = json.dumps({
    "model": "gpt-5.6-luna-2026-07-09",
    "choices": [{"message": {"role": "assistant", "content": "The match is over."}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 21, "completion_tokens": 40, "total_tokens": 61,
              "completion_tokens_details": {"reasoning_tokens": 30}},
})


class Recorder:
    """A fake transport that records the request and returns a fixed response."""

    def __init__(self, status: int, body: str) -> None:
        self.response = foundry_smoke.Response(status, body)
        self.calls: list[tuple[str, dict[str, str], dict]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> foundry_smoke.Response:
        self.calls.append((url, headers, json.loads(body)))
        return self.response


def call(monkeypatch: pytest.MonkeyPatch, recorder: Recorder, *extra: str) -> int:
    monkeypatch.setenv("AGON_AI_TOKEN", TOKEN)
    return foundry_smoke.main(["--endpoint", ENDPOINT, "--deployment", "gpt-5.6-luna", *extra], transport=recorder)


def test_200_with_usage_passes_and_prints_the_evidence(monkeypatch, capsys) -> None:
    recorder = Recorder(200, OK_BODY)
    assert call(monkeypatch, recorder) == 0
    out = capsys.readouterr().out
    for needle in ("deployment: gpt-5.6-luna", "model: gpt-5.6-luna-2026-07-09", "latency ms:",
                   "total 61", "reasoning 30", "PASS"):
        assert needle in out
    assert TOKEN not in out


def test_request_shape(monkeypatch) -> None:
    recorder = Recorder(200, OK_BODY)
    call(monkeypatch, recorder)
    url, headers, body = recorder.calls[0]
    assert url == "https://aif-agon-example.openai.azure.com/openai/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert body["model"] == "gpt-5.6-luna"
    assert "max_completion_tokens" in body
    assert "max_tokens" not in body and "temperature" not in body


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_any_non_200_fails(monkeypatch, capsys, status: int) -> None:
    assert call(monkeypatch, Recorder(status, '{"error": {"code": "x"}}')) == 1
    assert "FAIL: expected HTTP 200" in capsys.readouterr().out


def azure_error(code: str) -> str:
    return json.dumps({"error": {"code": code, "message": "fixture message"}})


@pytest.mark.parametrize("status", [401, 403])
def test_permission_denied_points_at_rbac_whatever_the_status(status: int) -> None:
    # A missing data-action role on an Entra token is often reported as 401, not 403.
    text = foundry_smoke.hint(status, azure_error("PermissionDenied"))
    assert text == foundry_smoke.RBAC_HINT
    assert "audience" not in text


@pytest.mark.parametrize("body", [azure_error("InvalidAuthenticationToken"), "not json", "",
                                  json.dumps({"error": "flat string"}), json.dumps(["list"])])
def test_401_without_permission_denied_points_at_the_audience(body: str) -> None:
    assert foundry_smoke.hint(401, body) == foundry_smoke.AUDIENCE_HINT


def test_403_without_a_code_still_points_at_rbac() -> None:
    assert foundry_smoke.hint(403, "not json").startswith(foundry_smoke.RBAC_HINT)


def test_the_printed_failure_carries_the_rbac_hint_for_a_401_permission_denied(monkeypatch, capsys) -> None:
    assert call(monkeypatch, Recorder(401, azure_error("PermissionDenied"))) == 1
    out = capsys.readouterr().out
    assert "infra/rbac.bicep" in out and "audience" not in out


@pytest.mark.parametrize("body", [
    "not json",
    json.dumps({"model": "m", "choices": []}),
    json.dumps({"usage": {"total_tokens": 3}}),
])
def test_200_without_model_or_usage_fails(monkeypatch, body: str) -> None:
    assert call(monkeypatch, Recorder(200, body)) == 1


def test_missing_token_fails_without_calling(monkeypatch) -> None:
    monkeypatch.delenv("AGON_AI_TOKEN", raising=False)
    recorder = Recorder(200, OK_BODY)
    assert foundry_smoke.main(["--endpoint", ENDPOINT, "--deployment", "d"], transport=recorder) == 1
    assert recorder.calls == []


@pytest.mark.parametrize("endpoint", [
    "http://aif-agon-example.openai.azure.com/",
    "https://example.com/",
    "https://aif-agon-example.openai.azure.com.evil.test/",
    "https://aif-agon-example.openai.azure.com/openai/deployments/x",
    "https://aif-agon-example.openai.azure.com/?redirect=https://example.com",
])
def test_endpoint_outside_azure_is_refused_before_any_call(monkeypatch, endpoint: str) -> None:
    monkeypatch.setenv("AGON_AI_TOKEN", TOKEN)
    recorder = Recorder(200, OK_BODY)
    assert foundry_smoke.main(["--endpoint", endpoint, "--deployment", "d"], transport=recorder) == 1
    assert recorder.calls == []


def test_other_azure_inference_hosts_are_accepted() -> None:
    for host in ("x.cognitiveservices.azure.com", "x.services.ai.azure.com"):
        assert foundry_smoke.chat_url(f"https://{host}") == f"https://{host}/openai/v1/chat/completions"


def test_summary_and_evidence_files(monkeypatch, tmp_path: Path) -> None:
    summary, evidence = tmp_path / "summary.md", tmp_path / "evidence.json"
    assert call(monkeypatch, Recorder(200, OK_BODY), "--summary", str(summary), "--evidence", str(evidence)) == 0
    assert "gate C2): PASS" in summary.read_text(encoding="utf-8")
    row = json.loads(evidence.read_text(encoding="utf-8"))
    assert row["status"] == 200 and row["total_tokens"] == 61 and row["deployment"] == "gpt-5.6-luna"
    assert TOKEN not in summary.read_text(encoding="utf-8") + evidence.read_text(encoding="utf-8")
