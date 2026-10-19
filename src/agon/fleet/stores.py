"""Storage the fleet writes besides checkpoints: governance state, artifacts, the publish queue, the run ledger.

* `FileStore` implements the governance `Store` protocol (read, create-only, replace by etag)
  over a directory, safe across processes, so a run started in one process and resumed in
  another sees the same nonces and approvals. Tests and the kill-and-resume driver use it; the
  production store is the Cosmos adapter with the same three operations.
* `ArtifactStore` keeps bytes that are too big for a checkpoint (audio, captions, editions),
  content addressed and create-only. Checkpoints carry only their sha256.
* `PublishQueue` stands in for the `publish-requests` Storage queue. It is create-only keyed by
  the approval id, a second line of defence behind the verifier's consume-once.
* `RunLedger` records the run's status with etag compare-and-set and an explicit transition table.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from agon.fleet.contracts import PublishRequest
from agon.governance.store import Conflict, Document, NotFound, PreconditionFailed, Store

LOCK_TIMEOUT_SECONDS = 30.0
SHARING_RETRY_SECONDS = 5.0


def _snapshot(body: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(body, sort_keys=True, ensure_ascii=False))


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    deadline = time.monotonic() + SHARING_RETRY_SECONDS
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows refuses to replace a file another process has open for reading. Under many
            # concurrent readers half a second was not enough (a flake seen in the full suite),
            # so the retry is bounded by time, not by a count.
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def create_only(path: Path, data: bytes) -> bool:
    """Write `data` at `path` only if nothing is there. True when written, False when it existed."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


class _DirLock:
    """A cross-process lock: mkdir is atomic on every OS this runs on."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> "_DirLock":
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                os.mkdir(self.path)
                return self
            except (FileExistsError, PermissionError):
                # PermissionError: on Windows a lock directory that is being removed refuses mkdir.
                if time.monotonic() > deadline:
                    raise TimeoutError(f"lock {self.path} held for over {LOCK_TIMEOUT_SECONDS:g} s") from None
                time.sleep(0.005)

    def __exit__(self, *exc: Any) -> None:
        os.rmdir(self.path)


class FileStore:
    """The governance Store protocol over a directory. One JSON file per document."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, container: str) -> Path:
        if not container.isidentifier():
            raise ValueError(f"container name {container!r} is not a plain identifier")
        path = self.root / container
        path.mkdir(exist_ok=True)
        return path

    def _file(self, container: str, key: str) -> Path:
        return self._dir(container) / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:40] + ".json")

    def _lock(self, container: str) -> _DirLock:
        return _DirLock(self._dir(container) / ".lock")

    @staticmethod
    def _load(path: Path) -> Document | None:
        deadline = time.monotonic() + SHARING_RETRY_SECONDS
        while True:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                return Document(record["body"], record["etag"])
            except FileNotFoundError:
                return None
            except PermissionError:
                # Windows: the file is being replaced by another writer at this instant.
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.01)

    @staticmethod
    def _dump(key: str, body: dict[str, Any]) -> tuple[bytes, str]:
        etag = f'"{uuid.uuid4().hex}"'
        record = {"key": key, "etag": etag, "body": body}
        return json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8"), etag

    def read(self, container: str, key: str) -> Document | None:
        doc = self._load(self._file(container, key))
        return None if doc is None else Document(copy.deepcopy(doc.body), doc.etag)

    def create(self, container: str, key: str, body: dict[str, Any]) -> Document:
        snap = _snapshot(body)
        path = self._file(container, key)
        with self._lock(container):
            if path.exists():
                raise Conflict(f"{container}/{key} already exists")
            data, etag = self._dump(key, snap)
            _atomic_write(path, data)
        return Document(copy.deepcopy(snap), etag)

    def replace(self, container: str, key: str, body: dict[str, Any], if_match: str) -> Document:
        snap = _snapshot(body)
        path = self._file(container, key)
        with self._lock(container):
            current = self._load(path)
            if current is None:
                raise NotFound(f"{container}/{key} does not exist")
            if current.etag != if_match:
                raise PreconditionFailed(f"{container}/{key} changed since it was read")
            data, etag = self._dump(key, snap)
            _atomic_write(path, data)
        return Document(copy.deepcopy(snap), etag)

    def keys(self, container: str) -> list[str]:
        """Inspection helper; not part of the protocol."""
        out = []
        for path in self._dir(container).glob("*.json"):
            out.append(json.loads(path.read_text(encoding="utf-8"))["key"])
        return sorted(out)


# ----------------------------------------------------------------- artifacts


class ArtifactStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, sha256: str) -> bytes | None: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._data.setdefault(sha, bytes(data))
        return sha

    def get(self, sha256: str) -> bytes | None:
        with self._lock:
            return self._data.get(sha256)


class FileArtifactStore:
    """Content-addressed, create-only. The same bytes always land at the same path."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        path = self.root / sha
        if not create_only(path, data) and path.read_bytes() != data:
            raise RuntimeError(f"artifact {sha} on disk does not match its bytes")
        return sha

    def get(self, sha256: str) -> bytes | None:
        path = self.root / sha256
        if not path.is_file():
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError(f"artifact {sha256} on disk does not hash to its name")
        return data


# ----------------------------------------------------------------- publish queue


class PublishQueue(Protocol):
    def enqueue(self, request: PublishRequest) -> bool: ...

    def items(self) -> list[dict[str, Any]]: ...


class InMemoryPublishQueue:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def enqueue(self, request: PublishRequest) -> bool:
        with self._lock:
            if request.approval_id in self._items:
                return False
            self._items[request.approval_id] = asdict(request)
            return True

    def items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for _, v in sorted(self._items.items())]


class FilePublishQueue:
    """One create-only file per approval id: a replayed request can never enqueue twice."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def enqueue(self, request: PublishRequest) -> bool:
        if not request.approval_id.isalnum():
            raise ValueError("approval id must be alphanumeric")
        data = (json.dumps(asdict(request), sort_keys=True) + "\n").encode("utf-8")
        return create_only(self.root / f"{request.approval_id}.json", data)

    def items(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*.json"))]


# ----------------------------------------------------------------- run ledger

RUNS = "runs"
DRAFT = "DRAFT"
CHECKED = "CHECKED"
SEALED = "SEALED"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVED = "APPROVED"
CHANGES_REQUESTED = "CHANGES_REQUESTED"
REFUSED = "REFUSED"
RUN_STATUSES: tuple[str, ...] = (DRAFT, CHECKED, SEALED, AWAITING_APPROVAL, APPROVED, CHANGES_REQUESTED, REFUSED)
TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({DRAFT}),
    DRAFT: frozenset({CHECKED, REFUSED}),
    CHECKED: frozenset({SEALED}),
    SEALED: frozenset({AWAITING_APPROVAL, REFUSED}),
    AWAITING_APPROVAL: frozenset({APPROVED, CHANGES_REQUESTED}),
}


class LedgerError(RuntimeError):
    """A status change the transition table does not allow."""


class RunLedger:
    """Run status, moved only along `TRANSITIONS` and only by etag compare-and-set.

    Moving to the status a run already has is a no-op, so a step that runs again after a resume
    does not fail; moving anywhere else from a later status raises.
    """

    def __init__(self, store: Store, container: str = RUNS) -> None:
        self.store = store
        self.container = container

    def status(self, run_id: str) -> str | None:
        doc = self.store.read(self.container, run_id)
        return None if doc is None else doc.body.get("status")

    def history(self, run_id: str) -> list[dict[str, Any]]:
        doc = self.store.read(self.container, run_id)
        return [] if doc is None else list(doc.body.get("history", []))

    def record(self, run_id: str) -> dict[str, Any] | None:
        doc = self.store.read(self.container, run_id)
        return None if doc is None else dict(doc.body)

    def claim_approval(self, run_id: str, bundle_sha256: str) -> str | None:
        """Claim the right to issue this run's one nonce, by etag compare-and-set.

        Returns None when this caller won the claim, otherwise the bundle hash that is already
        claimed. A second caller loses even for the same bundle: a crashed first attempt and a
        concurrent one cannot be told apart, and one claim must mean at most one nonce. The claim
        holds no nonce, so the runs document never carries an approval capability, only the fact
        that one was requested.
        """
        for _ in range(3):
            doc = self.store.read(self.container, run_id)
            if doc is None:
                raise LedgerError(f"run {run_id}: no ledger record to claim an approval on")
            claimed = doc.body.get("approval_claim")
            if claimed is not None:
                return str(claimed)
            body = {**doc.body, "approval_claim": bundle_sha256}
            try:
                self.store.replace(self.container, run_id, body, if_match=doc.etag)
                return None
            except PreconditionFailed:
                continue
        raise LedgerError(f"run {run_id}: could not claim the approval after three attempts")

    def advance(self, run_id: str, to: str, detail: str = "", **fields: Any) -> None:
        if to not in RUN_STATUSES:
            raise LedgerError(f"unknown status {to!r}")
        for _ in range(3):
            doc = self.store.read(self.container, run_id)
            current = None if doc is None else doc.body.get("status")
            if current == to:
                return
            if to not in TRANSITIONS.get(current, frozenset()):
                raise LedgerError(f"run {run_id}: {current} -> {to} is not an allowed transition")
            history = [] if doc is None else list(doc.body.get("history", []))
            history.append({"status": to, "detail": detail})
            body = {**({} if doc is None else doc.body), **fields, "run_id": run_id, "status": to, "history": history}
            try:
                if doc is None:
                    self.store.create(self.container, run_id, body)
                else:
                    self.store.replace(self.container, run_id, body, if_match=doc.etag)
                return
            except (Conflict, PreconditionFailed):
                continue
        raise LedgerError(f"run {run_id}: could not record {to} after three attempts")
