"""Seal: one canonical JSON manifest over everything a bundle publishes, and its sha256.

The manifest lists the seed hash, the event-log hash, the charter hash, every artifact (each
edition and each audio file) with its sha256 and byte length, the gate verdicts, the author tag
of every sentence, the attested scope, and for an erratum the parent bundle hash. The bundle hash
is sha256 over the canonical manifest bytes; the manifest never contains its own hash.

Canonical form: sorted keys, no whitespace, UTF-8 (Greek text stays Greek, not \\u escapes),
integers only (no floats, no NaN). `verify_manifest` refuses bytes that are not in canonical form,
so one bundle has exactly one byte representation and one hash. The visitor's browser recomputes
the same sha256 over the served bytes with WebCrypto.

`seal` hashes artifacts from their bytes; nothing is taken on trust from a caller-supplied hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from agon.governance.claims import AUTHOR_TAGS

MANIFEST_SCHEMA = "agon.bundle/1"
BUNDLE_KINDS: tuple[str, ...] = ("charter", "fulltime", "erratum")
GATE_PASS = "pass"
# Every bundle carries a verdict from each of these gates, whatever it reads. A bundle that never
# ran one of them cannot be sealed, cannot get a nonce, cannot be approved and cannot be published.
# Pinned as a literal in tests/governance/test_seal.py. `charter_check` and `easy_read` join this
# tuple when those gates exist; adding a name here is the only way a new gate becomes mandatory.
REQUIRED_GATES: tuple[str, ...] = ("factgate", "factgate_lang", "rights")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# The publisher writes the manifest beside the artifacts at <target>/<hash>/manifest.json, so no
# artifact may take that name: it would land where the visitor's browser fetches the manifest.
MANIFEST_NAME = "manifest.json"
RESERVED_ARTIFACT_NAMES: frozenset[str] = frozenset({MANIFEST_NAME})


class SealError(ValueError):
    """Raised when a manifest cannot be built from the inputs given."""


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return
    if isinstance(value, float):
        raise SealError(f"float at {path}: the manifest is integers only")
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _reject_floats(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise SealError(f"non-string key at {path}")
            _reject_floats(v, f"{path}.{k}")
        return
    raise SealError(f"unsupported type {type(value).__name__} at {path}")


def canonical_utf8(value: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8, no floats."""
    _reject_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX64.match(value))


def seed_sha256(seed: int, match_index: int, generator: str) -> str:
    """Identity of the generator input: the seed, the fixture index and the generator version."""
    return sha256_hex(canonical_utf8({"generator": generator, "match_index": match_index, "seed": seed}))


def event_log_sha256(events: Iterable[Any]) -> str:
    """sha256 over the NDJSON event log: one canonical event per line, LF terminated."""
    lines = []
    for e in events:
        d = e.to_dict() if hasattr(e, "to_dict") else e
        lines.append(canonical_utf8(d) + b"\n")
    return sha256_hex(b"".join(lines))


@dataclass(frozen=True)
class Artifact:
    name: str
    data: bytes
    media_type: str
    role: str  # "edition" or "audio" or "feed"
    language: str | None = None


@dataclass(frozen=True)
class SealedBundle:
    manifest: dict[str, Any]
    manifest_bytes: bytes
    bundle_sha256: str


def seal(
    *,
    kind: str,
    match_id: str,
    seed_hash: str,
    event_log_hash: str,
    charter_hash: str | None,
    artifacts: Iterable[Artifact],
    gate_verdicts: Mapping[str, str],
    author_tags: Mapping[str, str],
    attested: Iterable[str],
    gate_cleared: Iterable[str],
    parent_bundle_sha256: str | None = None,
    deployment_ids: Mapping[str, str] | None = None,
) -> SealedBundle:
    """Build the canonical manifest and its bundle hash. Pure function of its inputs."""
    if kind not in BUNDLE_KINDS:
        raise SealError(f"unknown bundle kind {kind!r}")
    for label, value in (("seed_hash", seed_hash), ("event_log_hash", event_log_hash)):
        if not is_sha256_hex(value):
            raise SealError(f"{label} must be 64 lowercase hex characters")
    if charter_hash is not None and not is_sha256_hex(charter_hash):
        raise SealError("charter_hash must be 64 lowercase hex characters or None")
    if kind == "erratum" and not is_sha256_hex(parent_bundle_sha256):
        raise SealError("an erratum must name its parent bundle hash")
    if kind != "erratum" and parent_bundle_sha256 is not None:
        raise SealError("only an erratum names a parent bundle")
    if not gate_verdicts:
        raise SealError("a bundle must carry gate verdicts")
    missing_gates = missing_required_gates(gate_verdicts)
    if missing_gates:
        raise SealError(f"a bundle must carry a verdict from every required gate; missing {missing_gates}")
    entries: dict[str, Any] = {}
    for art in artifacts:
        if not _ARTIFACT_NAME.match(art.name):
            raise SealError(f"artifact name {art.name!r} is not a safe lowercase file name")
        if art.name in RESERVED_ARTIFACT_NAMES:
            raise SealError(f"artifact name {art.name!r} is reserved for the bundle manifest")
        if art.name in entries:
            raise SealError(f"duplicate artifact {art.name!r}")
        entry: dict[str, Any] = {
            "bytes": len(art.data),
            "media_type": art.media_type,
            "role": art.role,
            "sha256": sha256_hex(art.data),
        }
        if art.language is not None:
            entry["language"] = art.language
        entries[art.name] = entry
    if not entries:
        raise SealError("a bundle must contain at least one artifact")
    bad_tags = {sid: tag for sid, tag in author_tags.items() if tag not in AUTHOR_TAGS}
    if bad_tags:
        raise SealError(f"unknown author tags {bad_tags}")
    attested_l, cleared_l = sorted(set(attested)), sorted(set(gate_cleared))
    if set(attested_l) & set(cleared_l):
        raise SealError("an item cannot be both attested and gate-cleared only")
    known = set(entries) | set(author_tags)
    unknown = (set(attested_l) | set(cleared_l)) - known
    if unknown:
        raise SealError(f"attested scope names unknown items {sorted(unknown)}")
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "kind": kind,
        "match_id": match_id,
        "seed_sha256": seed_hash,
        "event_log_sha256": event_log_hash,
        "charter_sha256": charter_hash,
        "artifacts": entries,
        "gate_verdicts": dict(gate_verdicts),
        "author_tags": dict(author_tags),
        "attested_scope": {"attested": attested_l, "gate_cleared": cleared_l},
        "parent_bundle_sha256": parent_bundle_sha256,
        "deployment_ids": dict(deployment_ids or {}),
    }
    problems = manifest_shape_problems(manifest)
    if problems:
        raise SealError("; ".join(problems))
    data = canonical_utf8(manifest)
    return SealedBundle(manifest=manifest, manifest_bytes=data, bundle_sha256=sha256_hex(data))


MANIFEST_KEYS = frozenset({
    "schema", "kind", "match_id", "seed_sha256", "event_log_sha256", "charter_sha256", "artifacts",
    "gate_verdicts", "author_tags", "attested_scope", "parent_bundle_sha256", "deployment_ids",
})
ARTIFACT_KEYS = frozenset({"bytes", "media_type", "role", "sha256"})


def manifest_shape_problems(manifest: Any) -> list[str]:
    """Every way a parsed manifest differs from what `seal()` builds. Empty means well formed.

    One validator for both directions: `seal()` runs it on what it built, and `verify_manifest`
    runs it on what it was handed, so a hand-built canonical manifest with an unsafe artifact name
    or a missing field is refused before any consumer indexes into it.
    """
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]
    problems: list[str] = []
    keys = set(manifest)
    if keys != MANIFEST_KEYS:
        problems.append(f"manifest keys differ: missing {sorted(MANIFEST_KEYS - keys)}, unexpected {sorted(keys - MANIFEST_KEYS)}")
        return problems
    if manifest["schema"] != MANIFEST_SCHEMA:
        problems.append(f"unknown manifest schema {manifest['schema']!r}")
    kind = manifest["kind"]
    if kind not in BUNDLE_KINDS:
        problems.append(f"unknown bundle kind {kind!r}")
    if not isinstance(manifest["match_id"], str) or not manifest["match_id"]:
        problems.append("match_id must be a non-empty string")
    for key in ("seed_sha256", "event_log_sha256"):
        if not is_sha256_hex(manifest[key]):
            problems.append(f"{key} must be 64 lowercase hex characters")
    if manifest["charter_sha256"] is not None and not is_sha256_hex(manifest["charter_sha256"]):
        problems.append("charter_sha256 must be 64 lowercase hex characters or null")
    parent = manifest["parent_bundle_sha256"]
    if kind == "erratum" and not is_sha256_hex(parent):
        problems.append("an erratum must name its parent bundle hash")
    if kind != "erratum" and parent is not None:
        problems.append("only an erratum names a parent bundle")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or not artifacts:
        problems.append("manifest needs a non-empty artifact table")
        artifacts = {}
    for name, entry in artifacts.items():
        if not _ARTIFACT_NAME.match(name):
            problems.append(f"artifact name {name!r} is not a safe lowercase file name")
        if name in RESERVED_ARTIFACT_NAMES:
            problems.append(f"artifact name {name!r} is reserved for the bundle manifest")
        if not isinstance(entry, dict):
            problems.append(f"artifact {name!r} entry is not an object")
            continue
        extra = set(entry) - ARTIFACT_KEYS - {"language"}
        missing = ARTIFACT_KEYS - set(entry)
        if extra or missing:
            problems.append(f"artifact {name!r} fields: missing {sorted(missing)}, unexpected {sorted(extra)}")
            continue
        if not is_sha256_hex(entry["sha256"]):
            problems.append(f"artifact {name!r} sha256 must be 64 lowercase hex characters")
        if not isinstance(entry["bytes"], int) or isinstance(entry["bytes"], bool) or entry["bytes"] < 0:
            problems.append(f"artifact {name!r} bytes must be a non-negative int")
        for field_name in ("media_type", "role"):
            if not isinstance(entry[field_name], str) or not entry[field_name]:
                problems.append(f"artifact {name!r} {field_name} must be a non-empty string")
    verdicts = manifest["gate_verdicts"]
    if not isinstance(verdicts, dict) or not verdicts or not all(isinstance(k, str) and isinstance(v, str) for k, v in verdicts.items()):
        problems.append("gate_verdicts must be a non-empty map of strings")
    else:
        missing_gates = missing_required_gates(verdicts)
        if missing_gates:
            problems.append(f"gate_verdicts lacks required gates {missing_gates}")
    tags = manifest["author_tags"]
    if not isinstance(tags, dict) or not all(t in AUTHOR_TAGS for t in tags.values()):
        problems.append("author_tags must map sentence ids to model, template or officer")
        tags = {}
    scope = manifest["attested_scope"]
    if not isinstance(scope, dict) or set(scope) != {"attested", "gate_cleared"}:
        problems.append("attested_scope must hold exactly attested and gate_cleared")
    else:
        att, clr = scope["attested"], scope["gate_cleared"]
        if not all(isinstance(x, list) and all(isinstance(i, str) for i in x) for x in (att, clr)):
            problems.append("attested_scope lists must be lists of strings")
        else:
            if set(att) & set(clr):
                problems.append("an item cannot be both attested and gate-cleared only")
            unknown = (set(att) | set(clr)) - set(artifacts) - set(tags)
            if unknown:
                problems.append(f"attested scope names unknown items {sorted(unknown)}")
    deployment = manifest["deployment_ids"]
    if not isinstance(deployment, dict) or not all(isinstance(v, str) for v in deployment.values()):
        problems.append("deployment_ids must be a map of strings")
    return problems


def missing_required_gates(verdicts: Any) -> list[str]:
    """The required gates with no verdict in `verdicts`, sorted. Everything when it is not a map."""
    if not isinstance(verdicts, Mapping):
        return sorted(REQUIRED_GATES)
    return sorted(set(REQUIRED_GATES) - set(verdicts))


def gates_all_pass(manifest: Mapping[str, Any]) -> bool:
    """True only when every required gate is listed and every listed verdict is "pass"."""
    verdicts = manifest.get("gate_verdicts")
    return (
        isinstance(verdicts, dict)
        and bool(verdicts)
        and not missing_required_gates(verdicts)
        and all(v == GATE_PASS for v in verdicts.values())
    )


@dataclass(frozen=True)
class ManifestCheck:
    ok: bool
    bundle_sha256: str
    manifest: dict[str, Any] | None
    reasons: tuple[str, ...]

    def refusal_code(self, expected_sha256: str | None) -> str:
        """One truthful code for a failed check, shared by the verifier and the publisher."""
        if expected_sha256 is not None and self.bundle_sha256 != expected_sha256:
            return "sealed_hash_mismatch"
        if isinstance(self.manifest, dict) and missing_required_gates(self.manifest.get("gate_verdicts")):
            return "required_gate_missing"
        return "manifest_invalid"


def verify_manifest(
    manifest_bytes: bytes,
    artifacts: Mapping[str, bytes] | None = None,
    expected_sha256: str | None = None,
) -> ManifestCheck:
    """Recompute everything from bytes: canonical form, bundle hash, and each artifact's hash.

    With `artifacts` given, a missing or an extra artifact is a failure, as is any byte change.
    The manifest's shape is checked by the same validator `seal()` uses, before anything reads it.
    """
    actual = sha256_hex(manifest_bytes)
    reasons: list[str] = []
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ManifestCheck(False, actual, None, (f"manifest is not UTF-8 JSON: {exc}",))
    if not isinstance(manifest, dict):
        return ManifestCheck(False, actual, None, ("manifest is not a JSON object",))
    try:
        if canonical_utf8(manifest) != manifest_bytes:
            reasons.append("manifest bytes are not in canonical form")
    except SealError as exc:
        reasons.append(f"manifest is not canonical: {exc}")
    shape = manifest_shape_problems(manifest)
    reasons.extend(shape)
    if expected_sha256 is not None and actual != expected_sha256:
        reasons.append(f"bundle sha256 {actual} does not equal the expected {expected_sha256}")
    listed = manifest.get("artifacts")
    if shape or not isinstance(listed, dict):
        pass  # a malformed manifest is already refused; never index into it
    elif artifacts is not None:
        for name in sorted(set(listed) - set(artifacts)):
            reasons.append(f"artifact {name} is listed but missing")
        for name in sorted(set(artifacts) - set(listed)):
            reasons.append(f"artifact {name} is present but not listed")
        for name in sorted(set(listed) & set(artifacts)):
            entry, data = listed[name], artifacts[name]
            if not isinstance(entry, dict) or entry.get("sha256") != sha256_hex(data):
                reasons.append(f"artifact {name} sha256 does not match its bytes")
            elif entry.get("bytes") != len(data):
                reasons.append(f"artifact {name} length does not match its bytes")
    return ManifestCheck(not reasons, actual, manifest, tuple(reasons))
