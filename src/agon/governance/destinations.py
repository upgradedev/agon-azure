"""Publish destinations: the fixed list an officer ticks on the approval, by id and text label.

Owner decision 2026-10-19, with the design the build session settled. The officer ticks AFTER the
bundle is sealed, so the ticked set cannot be inside `bundle_sha256`. Instead:

* the sealed manifest carries the OFFERED destinations, each with the sha256 of its rendered
  payload (`seal.seal(destinations=...)`); the payload is an ordinary sealed artifact named
  `destination-<id>.json` with role "destination";
* the decision carries the ticked subset (`verifier.ApprovalDecision.destinations`), and
  `verifier.verify` refuses any id outside the sealed offered set and an approve with nothing
  ticked when something was offered;
* the approval record stores the approved subset; the Publisher and every destination adapter
  refuse a destination that record does not list.

Labels are plain text, never logos or platform marks. How each destination receives the approved
bytes in this build (`DELIVERY`):

* "written": the bytes land in the public container (club website files, the feeds);
* "outbox": the exact payload and its queue message are recorded; no code path sends them yet (the
  newsroom Teams post flow is deployed but nothing calls it, and the journalist email send flow is
  not built), so nothing has reached the newsroom or a journalist;
* "preview": the exact platform payload, validated against the platform's published limits and
  never posted, because posting needs Meta and Google app approval.

Standard library only.
"""

from __future__ import annotations

DESTINATIONS: tuple[tuple[str, str], ...] = (
    ("club_site", "Club website (CMS feed)"),
    ("feeds", "RSS and JSON Feed"),
    ("instagram", "Instagram"),
    ("youtube", "YouTube"),
    ("newsroom_teams", "Internal newsroom (Teams)"),
    ("journalist_email", "Journalist email digest"),
)
DESTINATION_IDS: tuple[str, ...] = tuple(dest_id for dest_id, _ in DESTINATIONS)
LABELS: dict[str, str] = dict(DESTINATIONS)

WRITTEN = "written"
OUTBOX = "outbox"
PREVIEW = "preview"
DELIVERY: dict[str, str] = {
    "club_site": WRITTEN,
    "feeds": WRITTEN,
    "instagram": PREVIEW,
    "youtube": PREVIEW,
    "newsroom_teams": OUTBOX,
    "journalist_email": OUTBOX,
}

# The one sentence every Instagram and YouTube preview carries, wherever it is shown.
PREVIEW_LABEL = "Preview: not posted; posting needs Meta and Google app approval."
# The one sentence every newsroom and journalist payload carries until the send flow exists.
OUTBOX_LABEL = "Not sent yet: the payload and its queue message are recorded; no code path sends them yet."

ROLE = "destination"
PAYLOAD_SCHEMA = "agon.destination/1"
PAYLOAD_MEDIA_TYPE = "application/json"
_ARTIFACT_PREFIX = "destination-"


def artifact_name(dest_id: str) -> str:
    """The sealed artifact that holds a destination's rendered payload."""
    if dest_id not in LABELS:
        raise ValueError(f"unknown destination {dest_id!r}")
    return f"{_ARTIFACT_PREFIX}{dest_id}.json"


def in_order(ids: object) -> tuple[str, ...]:
    """Known ids from `ids`, in registry order, each once. Unknown ids are dropped."""
    wanted = set(ids) if isinstance(ids, (set, frozenset, list, tuple, dict)) else set()
    return tuple(dest_id for dest_id in DESTINATION_IDS if dest_id in wanted)
