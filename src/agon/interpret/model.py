"""The frozen interpret model: coefficients, the xT grid, goal rates and band thresholds.

`model.json` is written once by `python -m agon.interpret.fit --seeds 1-500` and committed with its
sha256 in `model.lock`. Loading refuses a file whose bytes do not hash to the lock, so a hand edit
fails loudly instead of quietly changing every xG, xT and win probability. Everything inside is an
integer (canonical JSON has no floats); units are written next to each key in docs/INTERPRET.md.

A generator bump (a new GENERATOR_VERSION) means one refit command and a new lock; a test fails
until that happens, because the model records the generator it was fitted on.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from typing import Any

MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model.json")
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model.lock")
MODEL_SCHEMA = "agon.interpret.model/1"


class ModelError(RuntimeError):
    """The frozen model is missing, does not match its lock, or has the wrong schema."""


def read_lock(path: str = LOCK_FILE) -> str:
    with open(path, "rb") as fh:
        text = fh.read().decode("ascii").strip()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ModelError(f"{path} must hold one lowercase sha256")
    return text


def file_sha256(path: str = MODEL_FILE) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_model_file(path: str = MODEL_FILE, lock_path: str = LOCK_FILE) -> dict[str, Any]:
    """Read and verify a model file against its lock. Raises ModelError on any mismatch."""
    if not os.path.exists(path):
        raise ModelError(f"model file missing: {path}; run python -m agon.interpret.fit --seeds 1-500")
    with open(path, "rb") as fh:
        data = fh.read()
    digest = hashlib.sha256(data).hexdigest()
    expected = read_lock(lock_path)
    if digest != expected:
        raise ModelError(f"model sha256 {digest} does not match the lock {expected}")
    model = json.loads(data.decode("ascii"))
    if model.get("schema") != MODEL_SCHEMA:
        raise ModelError(f"model schema {model.get('schema')!r}, expected {MODEL_SCHEMA!r}")
    return model


@lru_cache(maxsize=1)
def load_model() -> dict[str, Any]:
    """The committed, verified model. Cached: the file is read and hashed once per process."""
    return load_model_file()


def model_sha256() -> str:
    """The sha256 of the committed model bytes (verified against the lock)."""
    load_model()
    return read_lock()
