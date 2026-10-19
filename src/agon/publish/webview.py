"""The `destinations` key of the agon.web-run/1 run format: what the console shows per destination.

    destinations: [{"id", "label", "status", "preview"}], all six destinations in registry order

status, one of:

* "delivered": approved, and the Publisher's receipt shows the bytes written (club_site, feeds);
* "preview_not_posted": approved and rendered, not posted or sent. Instagram and YouTube always
  (the note is `PREVIEW_LABEL`); the newsroom and journalist outbox until their send flow exists
  (the note is `OUTBOX_LABEL`); the club website and feeds between approval and the publish;
* "not_selected": offered by the sealed bundle and not approved for it. On a run that is not
  approved (yet), every offered destination reads not_selected;
* "not_offered": the sealed bundle does not offer it.

preview: null for not_offered; otherwise
`{"kind": "written" | "outbox" | "preview", "note": str or null, "title": str, "text": str,
"payload_sha256": the sealed payload hash, "path": the published path or null}`. `text` is the
exact Instagram caption, the exact YouTube title and description, and a plain excerpt for the
others.

`destinations_for_run` builds the key from the sealed manifest, the payload bytes, the approval
record and the receipt; `destination_problems` is the checker `agon.webrun.validate_run` runs
when the key is present. Standard library only.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from agon.governance import destinations as dest_mod
from agon.governance.seal import is_sha256_hex, offered_destinations

STATUSES: tuple[str, ...] = ("delivered", "preview_not_posted", "not_selected", "not_offered")
ROW_KEYS = frozenset({"id", "label", "status", "preview"})
PREVIEW_KEYS = frozenset({"kind", "note", "title", "text", "payload_sha256", "path"})
WAITING_NOTE = "Approved; not written yet: the Publisher job has not published this bundle."
TEXT_MAX_CHARS = 6000


def _excerpt(dest_id: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    """(title, text) a person reads for one destination's payload."""
    if dest_id == "instagram":
        return "Instagram caption", str(payload.get("caption", ""))
    if dest_id == "youtube":
        video = payload.get("video") if isinstance(payload.get("video"), dict) else {}
        return str(video.get("title", "")), str(video.get("description", ""))
    if dest_id == "newsroom_teams":
        card = payload.get("card") if isinstance(payload.get("card"), dict) else {}
        blocks = [b.get("text", "") for b in card.get("body", []) if isinstance(b, dict)]
        return (blocks[0] if blocks else "Newsroom notification"), "\n".join(blocks[1:])
    if dest_id == "journalist_email":
        return str(payload.get("subject", "")), str(payload.get("text", ""))
    if dest_id == "feeds":
        return str(payload.get("title", "")), str(payload.get("summary", ""))
    edition = payload.get("edition") if isinstance(payload.get("edition"), dict) else {}
    sentences = [s.get("text", "") for s in edition.get("sentences", []) if isinstance(s, dict)]
    return str(payload.get("title", "")), " ".join(sentences)


def _published_path(entry: Mapping[str, Any]) -> str | None:
    for key in ("page", "preview", "message"):
        if isinstance(entry.get(key), str):
            return entry[key]
    files = entry.get("files")
    if isinstance(files, dict) and files:
        return sorted(files)[0]
    return None


def destinations_for_run(
    manifest: Mapping[str, Any],
    payloads: Mapping[str, bytes],
    *,
    approval: Mapping[str, Any] | None = None,
    receipt: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The six rows for one run. `payloads` maps each offered destination id to its sealed bytes."""
    offered = offered_destinations(manifest)
    approved: tuple[str, ...] = ()
    if approval is not None and approval.get("decision") == "approve":
        approved = dest_mod.in_order(approval.get("destinations", []))
    if receipt is not None:
        approved = dest_mod.in_order(receipt.get("destinations_approved", []))
    entries = receipt.get("destinations", {}) if receipt is not None else {}
    rows: list[dict[str, Any]] = []
    for dest_id in dest_mod.DESTINATION_IDS:
        row: dict[str, Any] = {"id": dest_id, "label": dest_mod.LABELS[dest_id], "status": "not_offered", "preview": None}
        if dest_id not in offered:
            rows.append(row)
            continue
        payload = json.loads(payloads[dest_id].decode("utf-8"))
        title, text = _excerpt(dest_id, payload)
        kind = dest_mod.DELIVERY[dest_id]
        entry = entries.get(dest_id) if isinstance(entries, dict) else None
        note: str | None = None
        path: str | None = None
        if dest_id not in approved:
            status = "not_selected"
        elif kind == dest_mod.PREVIEW:
            status, note = "preview_not_posted", dest_mod.PREVIEW_LABEL
        elif kind == dest_mod.OUTBOX:
            status, note = "preview_not_posted", dest_mod.OUTBOX_LABEL
        elif isinstance(entry, dict):
            status = "delivered"
        else:
            status, note = "preview_not_posted", WAITING_NOTE
        if isinstance(entry, dict):
            path = _published_path(entry)
        row["status"] = status
        row["preview"] = {
            "kind": kind,
            "note": note,
            "title": title,
            "text": text[:TEXT_MAX_CHARS],
            "payload_sha256": manifest["destinations"][dest_id]["sha256"],
            "path": path,
        }
        rows.append(row)
    return rows


def destination_problems(rows: Any, *, approved: bool, state: str) -> list[str]:
    """Problems with a run's `destinations` key; empty means the console may show it."""
    if not isinstance(rows, list) or len(rows) != len(dest_mod.DESTINATION_IDS):
        return [f"destinations must list all {len(dest_mod.DESTINATION_IDS)} destinations"]
    problems: list[str] = []
    for i, (row, dest_id) in enumerate(zip(rows, dest_mod.DESTINATION_IDS)):
        where = f"destinations[{i}]"
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            problems.append(f"{where} keys must be exactly id, label, preview and status")
            continue
        if row["id"] != dest_id:
            problems.append(f"{where}.id must be {dest_id} (registry order)")
            continue
        if row["label"] != dest_mod.LABELS[dest_id]:
            problems.append(f"{where}.label must be {dest_mod.LABELS[dest_id]!r}")
        status, preview = row["status"], row["preview"]
        if status not in STATUSES:
            problems.append(f"{where}.status must be one of {', '.join(STATUSES)}")
            continue
        if (status == "not_offered") != (preview is None):
            problems.append(f"{where}: preview is null exactly when the status is not_offered")
            continue
        if not approved and status not in ("not_offered", "not_selected"):
            problems.append(f"{where}: a run that is not approved has no delivered or previewed destination")
        if preview is None:
            continue
        if not isinstance(preview, dict) or set(preview) != PREVIEW_KEYS:
            problems.append(f"{where}.preview keys must be exactly kind, note, path, payload_sha256, text and title")
            continue
        kind = preview["kind"]
        if kind != dest_mod.DELIVERY[dest_id]:
            problems.append(f"{where}.preview.kind must be {dest_mod.DELIVERY[dest_id]}")
        if not is_sha256_hex(preview["payload_sha256"]):
            problems.append(f"{where}.preview.payload_sha256 must be 64 lowercase hex characters")
        if not isinstance(preview["title"], str) or not isinstance(preview["text"], str):
            problems.append(f"{where}.preview title and text must be strings")
        if status == "delivered" and (kind != dest_mod.WRITTEN or state != "PUBLISHED" or not isinstance(preview["path"], str)):
            problems.append(f"{where}: delivered means written bytes on a PUBLISHED run, with their path")
        if status == "preview_not_posted":
            expected = {dest_mod.PREVIEW: dest_mod.PREVIEW_LABEL, dest_mod.OUTBOX: dest_mod.OUTBOX_LABEL}.get(kind, WAITING_NOTE)
            if preview["note"] != expected:
                problems.append(f"{where}.preview.note must say {expected!r}")
    return problems
