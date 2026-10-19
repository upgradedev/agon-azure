"""Shared translation helpers for channel adapters. No store access and no approvals here."""

from __future__ import annotations

from typing import Any

from agon.governance import verifier


class PayloadError(ValueError):
    """The payload cannot be translated into a decision."""


def refused_payload(channel: str, detail: str) -> verifier.VerifyResult:
    """The only result an adapter may build itself: a refusal, before the verifier is reached."""
    return verifier.VerifyResult(ok=False, code="malformed_payload", detail=detail, channel=channel)


def require_str(container: dict[str, Any], key: str, where: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def require_dict(container: Any, key: str, where: str) -> dict[str, Any]:
    if not isinstance(container, dict):
        raise PayloadError(f"{where} must be an object")
    value = container.get(key)
    if not isinstance(value, dict):
        raise PayloadError(f"{where}.{key} must be an object")
    return value
