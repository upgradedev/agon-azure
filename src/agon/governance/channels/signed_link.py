"""Signed single-use web link: works from any phone, and takes over if the Teams leg fails.

Token format, URL-safe and without padding:

    v1.<key id>.<base64url(canonical JSON payload)>.<base64url(HMAC-SHA256)>

The payload binds everything: approver, bundle hash, decision, expiry and nonce, as
``{"a", "b", "d", "e", "n"}`` with exactly those keys. The MAC covers the version, the key id and
the payload, and is compared in constant time (`hmac.compare_digest`). The key comes from an
injected secret provider (Key Vault in Azure), never from code or configuration.

Single use is NOT a second mechanism here: the token carries the card's nonce, and `verify()`
consumes that nonce once across every channel. A replayed link is refused by the verifier.
The Confirm rows the officer ticks on the approve page are posted alongside the token; they are
the officer's own attestation and need no signature.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from datetime import datetime
from typing import Any, Iterable, Protocol

from agon.governance import verifier
from agon.governance.channels._common import PayloadError, refused_payload

CHANNEL = "signed_link"
VERSION = "v1"
MIN_KEY_BYTES = 32
_PAYLOAD_KEYS = frozenset({"a", "b", "d", "e", "n"})
_KID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")


class SecretProvider(Protocol):
    def signing_key(self, key_id: str) -> bytes: ...


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if not _B64.match(text):
        raise PayloadError("token part is not base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise PayloadError("token part does not decode") from exc


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _key(secrets: SecretProvider, key_id: str) -> bytes:
    try:
        key = secrets.signing_key(key_id)
    except KeyError as exc:
        raise PayloadError(f"unknown signing key id {key_id!r}") from exc
    if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
        raise PayloadError(f"signing key {key_id!r} is shorter than {MIN_KEY_BYTES} bytes")
    return key


def _mac(key: bytes, key_id: str, payload_b64: str) -> bytes:
    message = f"agon-signed-link|{VERSION}|{key_id}|{payload_b64}".encode("ascii")
    return hmac.new(key, message, hashlib.sha256).digest()


def issue_token(
    *,
    secrets: SecretProvider,
    key_id: str,
    approver: str,
    bundle_sha256: str,
    nonce: str,
    decision: str,
    expires_at: datetime,
) -> str:
    """Mint one link token. Called by the API when it sends the officer a link, never by a page."""
    if not _KID.match(key_id):
        raise PayloadError("key id must be 1 to 32 URL-safe characters")
    if decision not in verifier.DECISIONS:
        raise PayloadError(f"unknown decision {decision!r}")
    payload = {"a": approver, "b": bundle_sha256, "d": decision, "e": int(expires_at.timestamp()), "n": nonce}
    payload_b64 = _b64e(_canonical(payload))
    return f"{VERSION}.{key_id}.{payload_b64}.{_b64e(_mac(_key(secrets, key_id), key_id, payload_b64))}"


def parse(
    token: Any,
    *,
    secrets: SecretProvider,
    received_at: datetime,
    confirmed_rows: Iterable[str] = (),
) -> verifier.ApprovalDecision:
    if not isinstance(token, str) or len(token) > 2048:
        raise PayloadError("token missing or too long")
    parts = token.split(".")
    if len(parts) != 4:
        raise PayloadError("token must have four parts")
    version, key_id, payload_b64, mac_b64 = parts
    if version != VERSION:
        raise PayloadError(f"unknown token version {version!r}")
    if not _KID.match(key_id):
        raise PayloadError("malformed key id")
    expected = _mac(_key(secrets, key_id), key_id, payload_b64)
    if not hmac.compare_digest(expected, _b64d(mac_b64)):
        raise PayloadError("token signature does not verify")
    try:
        payload = json.loads(_b64d(payload_b64).decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadError("token payload is not JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise PayloadError("token payload has missing or unknown fields")
    if _canonical(payload) != _b64d(payload_b64):
        raise PayloadError("token payload is not canonical")
    if not isinstance(payload["e"], int) or isinstance(payload["e"], bool):
        raise PayloadError("token expiry must be an integer")
    if int(received_at.timestamp()) >= payload["e"]:
        raise PayloadError("link has expired")
    rows = list(confirmed_rows)
    if not all(isinstance(r, str) for r in rows):
        raise PayloadError("confirmed rows must be strings")
    for key in ("a", "b", "d", "n"):
        if not isinstance(payload[key], str):
            raise PayloadError(f"token field {key} must be a string")
    return verifier.ApprovalDecision(
        bundle_sha256=payload["b"],
        nonce=payload["n"],
        decision=payload["d"],
        approver=payload["a"],
        channel=CHANNEL,
        received_at=received_at,
        confirmed_rows=frozenset(rows),
    )


def handle(
    token: Any,
    store: Any,
    now: datetime,
    *,
    secrets: SecretProvider,
    confirmed_rows: Iterable[str] = (),
) -> verifier.VerifyResult:
    try:
        decision = parse(token, secrets=secrets, received_at=now, confirmed_rows=confirmed_rows)
    except PayloadError as exc:
        return refused_payload(CHANNEL, str(exc))
    return verifier.verify(decision, store, now)
