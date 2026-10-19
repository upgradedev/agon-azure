"""Versioned prompts, each pinned by sha256 in `prompts/manifest.json`.

A prompt is loaded only if its bytes hash to the manifest entry; otherwise loading fails, so an
edited prompt cannot reach a model until the manifest says so, and the manifest's own hash (which
the seal records in `deployment_ids["prompts"]`) changes with it.

Regenerate the manifest after editing a prompt:

    python -m agon.agents.prompts --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from agon.agents.ports import MODEL_ROLES, PROMPT_AGENT_ROLES, Prompt

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MANIFEST_PATH = PROMPT_DIR / "manifest.json"
MANIFEST_SCHEMA = "agon.prompts/1"
PROMPT_FILES: dict[str, str] = {role: f"{role}.v1.md" for role in MODEL_ROLES}


class PromptError(RuntimeError):
    """A prompt file or the manifest does not match what is pinned."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(prompt_dir: Path = PROMPT_DIR) -> dict[str, Any]:
    entries = {}
    for role in MODEL_ROLES:
        name = PROMPT_FILES[role]
        data = (prompt_dir / name).read_bytes()
        if b"\r" in data:
            raise PromptError(f"{name} contains a carriage return; prompts are LF only")
        entries[role] = {
            "file": name,
            "version": name.split(".")[1],
            "kind": "prompt_agent" if role in PROMPT_AGENT_ROLES else "structured",
            "sha256": _sha256(data),
        }
    return {"schema": MANIFEST_SCHEMA, "prompts": entries}


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptError(f"cannot read the prompt manifest: {exc}") from exc
    if manifest.get("schema") != MANIFEST_SCHEMA or set(manifest.get("prompts", {})) != set(MODEL_ROLES):
        raise PromptError("prompt manifest schema or role list differs from the code")
    return manifest


def manifest_sha256(path: Path = MANIFEST_PATH) -> str:
    """Identity of the whole prompt set, recorded in every sealed bundle."""
    return _sha256(path.read_bytes())


def load_prompt(role: str, prompt_dir: Path = PROMPT_DIR, manifest_path: Path = MANIFEST_PATH) -> Prompt:
    if role not in MODEL_ROLES:
        raise PromptError(f"unknown role {role!r}")
    entry = read_manifest(manifest_path)["prompts"][role]
    data = (prompt_dir / entry["file"]).read_bytes()
    actual = _sha256(data)
    if actual != entry["sha256"]:
        raise PromptError(f"prompt {entry['file']} hashes to {actual}, the manifest pins {entry['sha256']}")
    return Prompt(role=role, version=entry["version"], text=data.decode("utf-8"), sha256=actual)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or rewrite the prompt manifest.")
    parser.add_argument("--write", action="store_true", help="rewrite prompts/manifest.json from the files")
    args = parser.parse_args(argv)
    built = manifest_bytes(build_manifest())
    if args.write:
        MANIFEST_PATH.write_bytes(built)
        print(f"wrote {MANIFEST_PATH.name} sha256 {_sha256(built)}")
        return 0
    if not MANIFEST_PATH.exists() or MANIFEST_PATH.read_bytes() != built:
        print("prompt manifest is stale: run python -m agon.agents.prompts --write", file=sys.stderr)
        return 1
    print(f"prompt manifest ok, {len(MODEL_ROLES)} prompts, sha256 {_sha256(built)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
