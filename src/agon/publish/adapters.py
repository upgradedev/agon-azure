"""One least-privilege adapter per publish destination.

Each adapter only PLANS: it reads the sealed payload and, for the feeds, the current pointer and
feed version; it never writes. `agon.governance.publisher.Publisher` writes what it planned, after
every refusal is decided, and only under the adapter's own folder (`prefix`) inside the one
container the job's identity may write. No adapter holds a store or a writable sink. Every
adapter starts with `require_approved`, so it refuses a destination the approval record does not
list even if a caller skipped the Publisher's own check.

Files are dated with the approval time on the record (`DeliveryContext.approved_at`), never with
the publish clock, so a publish that stopped half way plans the same bytes when it is retried.

Layout under `<target>/` (the container: `feed`, or `sandbox` for the sandbox publisher):

    site/<bundle>/index.html            club_site: the recap page (sealed bytes)
    site/<bundle>/edition.json          club_site: the English edition (sealed bytes)
    site/<parent>/superseded-by.json    club_site erratum: marks the parent superseded, create-only
    feeds/v/<version>/feed.json         feeds: JSON Feed 1.1, a new version per bundle, create-only
    feeds/v/<version>/rss.xml           feeds: RSS 2.0, same items
    feeds/latest.json                   feeds: the stable pointer to the newest version (see below)
    previews/<id>/<bundle>/payload.json instagram, youtube: the exact payload, never posted
    outbox/<id>/<bundle>/payload.json   newsroom_teams, journalist_email: the exact payload
    outbox/<id>/<bundle>/message.json   the queue message a send flow would read (`OUTBOX_QUEUES`)

The feed pointer. Every feed version is create-only and content addressed (`<version>` is derived
from the sha256 of both files), so no published feed byte is ever overwritten. A reader finds the
newest version through `feeds/latest.json`:

    {"schema": "agon.feed-pointer/1", "version", "feed_json", "rss_xml", "feed_json_sha256",
     "rss_xml_sha256", "latest_bundle_sha256", "items"}

with paths relative to the container. It is the one blob that is replaced, and only by
compare-and-set on the etag read with it (If-Match), created the first time with If-None-Match: *.
A feed reader needs a stable feed URL, so the site resolves the pointer; the pointer itself is JSON,
not a feed document.

Errata: club_site writes the corrected page at its own hash and a create-only marker beside the
parent; feeds put the correction first and keep the parent's item, marked superseded, in the new
version; instagram and youtube payloads are edit previews; newsroom_teams is a threaded reply; the
journalist digest names the report it corrects. The parent's files are never touched.

The newsroom and journalist sends are not wired: the adapter writes the payload and the queue
message (`OUTBOX_QUEUES` names the queue a Logic App would drain; that flow and those queues are
not deployed yet), and the receipt says "not sent".
"""

from __future__ import annotations

import json
from datetime import datetime
from email.utils import format_datetime
from typing import Any, Mapping
from xml.sax.saxutils import escape

from agon.governance import destinations as dest_mod
from agon.governance.publisher import (
    BlobReader,
    DeliveryContext,
    DeliveryPlan,
    DestinationRefused,
    PlannedFile,
    PointerWrite,
    require_approved,
)
from agon.governance.seal import canonical_utf8, sha256_hex
from agon.publish import render

POINTER_NAME = "latest.json"
POINTER_SCHEMA = "agon.feed-pointer/1"
JSON_FEED_VERSION = "https://jsonfeed.org/version/1.1"
FEED_TITLE = "Agon match reports (synthetic matches)"
FEED_DESCRIPTION = "Approved match reports from a fictional club media office. Every match is synthetic."
FEED_MAX_ITEMS = 50
SUPERSEDED_PREFIX = "Superseded: "
OUTBOX_QUEUES: dict[str, str] = {
    "newsroom_teams": "outbox-newsroom-teams",
    "journalist_email": "outbox-journalist-email",
}
OUTBOX_SCHEMA = "agon.outbox/1"
SUPERSEDED_SCHEMA = "agon.superseded/1"
HTML = "text/html; charset=utf-8"
JSON = "application/json"
FEED_JSON = "application/feed+json"
RSS = "application/rss+xml"


def _load(payload: bytes, dest_id: str) -> dict[str, Any]:
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DestinationRefused("payload_invalid", f"{dest_id} payload is not UTF-8 JSON") from exc
    if not isinstance(body, dict) or body.get("destination") != dest_id or body.get("schema") != dest_mod.PAYLOAD_SCHEMA:
        raise DestinationRefused("payload_invalid", f"payload is not a {dest_id} destination payload")
    return body


def _str(body: Mapping[str, Any], key: str, dest_id: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise DestinationRefused("payload_invalid", f"{dest_id} payload field {key} must be a string")
    return value


class _Base:
    destination: str = ""
    prefix: str = ""

    def __init__(self, base_url: str = "") -> None:
        self.base_url = base_url.rstrip("/")

    def url(self, path: str) -> str:
        """A public URL for a `<container>/<blob>` path; site-relative when no base is configured."""
        return f"{self.base_url}/{path}" if self.base_url else f"/{path}"

    def _site_page(self, ctx: DeliveryContext) -> str | None:
        if "club_site" not in ctx.approved_destinations:
            return None
        return self.url(ctx.path("site", ctx.bundle_sha256, "index.html"))


class ClubSiteAdapter(_Base):
    destination = "club_site"
    prefix = "site"

    def plan(self, ctx: DeliveryContext, payload: bytes, reader: BlobReader) -> DeliveryPlan:
        require_approved(ctx, self.destination)
        body = _load(payload, self.destination)
        edition = body.get("edition")
        if not isinstance(edition, dict):
            raise DestinationRefused("payload_invalid", "club_site payload field edition must be an object")
        page = ctx.path(self.prefix, ctx.bundle_sha256, "index.html")
        files = [
            PlannedFile(page, _str(body, "recap_html", self.destination).encode("utf-8"), HTML),
            PlannedFile(ctx.path(self.prefix, ctx.bundle_sha256, "edition.json"), canonical_utf8(edition), JSON),
        ]
        parent = ctx.manifest.get("parent_bundle_sha256")
        if parent is not None:
            marker = {
                "schema": SUPERSEDED_SCHEMA,
                "superseded_bundle_sha256": parent,
                "by_bundle_sha256": ctx.bundle_sha256,
                "by_page": self.url(page),
                "at": ctx.approved_at,
            }
            files.append(PlannedFile(ctx.path(self.prefix, parent, "superseded-by.json"), canonical_utf8(marker), JSON))
        entry = {"delivery": dest_mod.WRITTEN, "page": page, "supersedes_bundle_sha256": parent}
        return DeliveryPlan(self.destination, tuple(files), entry)


def _rfc822(moment: str) -> str:
    return format_datetime(datetime.fromisoformat(moment))


def rss_bytes(items: list[dict[str, Any]], channel_link: str) -> bytes:
    """RSS 2.0 over the same items as the JSON Feed version, newest first."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>',
        f"<title>{escape(FEED_TITLE)}</title><link>{escape(channel_link)}</link>",
        f"<description>{escape(FEED_DESCRIPTION)}</description><language>en</language>",
    ]
    if items:
        parts.append(f"<lastBuildDate>{_rfc822(items[0]['date_published'])}</lastBuildDate>")
    for item in items:
        parts.append("<item>")
        parts.append(f"<title>{escape(item['title'])}</title>")
        if item.get("url"):
            parts.append(f"<link>{escape(item['url'])}</link>")
        parts.append(f"<description>{escape(item['content_text'])}</description>")
        parts.append(f'<guid isPermaLink="false">{escape(item["id"])}</guid>')
        parts.append(f"<pubDate>{_rfc822(item['date_published'])}</pubDate>")
        parts.append("</item>")
    parts.append("</channel></rss>\n")
    return "".join(parts).encode("utf-8")


class FeedsAdapter(_Base):
    destination = "feeds"
    prefix = "feeds"

    def _previous(self, ctx: DeliveryContext, reader: BlobReader) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
        current = reader.read_pointer(ctx.path(self.prefix, POINTER_NAME))
        if current is None:
            return [], None, None
        data, etag = current
        try:
            pointer = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DestinationRefused("feed_pointer_invalid", "feeds/latest.json is not JSON") from exc
        if not isinstance(pointer, dict) or pointer.get("schema") != POINTER_SCHEMA or not isinstance(pointer.get("feed_json"), str):
            raise DestinationRefused("feed_pointer_invalid", "feeds/latest.json is not an agon.feed-pointer/1")
        feed = reader.get(f"{ctx.target}/{pointer['feed_json']}")
        if feed is None or sha256_hex(feed) != pointer.get("feed_json_sha256"):
            raise DestinationRefused("feed_pointer_mismatch", "the feed version the pointer names is missing or does not match its hash")
        items = json.loads(feed.decode("utf-8")).get("items", [])
        if not isinstance(items, list):
            raise DestinationRefused("feed_pointer_invalid", "the current feed version has no item list")
        return items, etag, pointer

    def plan(self, ctx: DeliveryContext, payload: bytes, reader: BlobReader) -> DeliveryPlan:
        require_approved(ctx, self.destination)
        body = _load(payload, self.destination)
        parent = ctx.manifest.get("parent_bundle_sha256")
        previous, etag, pointer = self._previous(ctx, reader)
        item_id = f"agon:{ctx.target}:{ctx.bundle_sha256}"
        entry: dict[str, Any] = {"delivery": dest_mod.WRITTEN, "item_id": item_id, "supersedes_bundle_sha256": parent}
        if pointer is not None and any(isinstance(i, dict) and i.get("id") == item_id for i in previous):
            # A retry after the pointer already advanced: the item is live, nothing to write.
            return DeliveryPlan(self.destination, (), {**entry, "version": pointer.get("version"), "already_listed": True})
        tags = body.get("tags", [])
        item: dict[str, Any] = {
            "id": item_id,
            "title": _str(body, "title", self.destination),
            "summary": _str(body, "summary", self.destination),
            "content_text": _str(body, "content_text", self.destination),
            "content_html": _str(body, "content_html", self.destination),
            "date_published": ctx.approved_at,
            "language": "en",
            "tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
            "_agon": {
                "bundle_sha256": ctx.bundle_sha256,
                "match_id": ctx.manifest["match_id"],
                "payload_sha256": ctx.manifest["destinations"][self.destination]["sha256"],
                "supersedes_bundle_sha256": parent,
                "superseded_by_bundle_sha256": None,
            },
        }
        page = self._site_page(ctx)
        if page is not None:
            item["url"] = page
        kept: list[dict[str, Any]] = []
        for old in previous:
            if not isinstance(old, dict):
                continue
            meta = old.get("_agon") if isinstance(old.get("_agon"), dict) else {}
            if parent is not None and meta.get("bundle_sha256") == parent and meta.get("superseded_by_bundle_sha256") is None:
                title = str(old.get("title", ""))
                old = {
                    **old,
                    "title": title if title.startswith(SUPERSEDED_PREFIX) else SUPERSEDED_PREFIX + title,
                    "_agon": {**meta, "superseded_by_bundle_sha256": ctx.bundle_sha256},
                }
            kept.append(old)
        items = [item, *kept][:FEED_MAX_ITEMS]
        feed = {
            "version": JSON_FEED_VERSION,
            "title": FEED_TITLE,
            "description": FEED_DESCRIPTION,
            "language": "en",
            "items": items,
        }
        if self.base_url:
            feed["home_page_url"] = self.base_url
        feed_data = canonical_utf8(feed)
        rss_data = rss_bytes(items, self.url(ctx.path(self.prefix, POINTER_NAME)))
        version = sha256_hex(canonical_utf8({"feed.json": sha256_hex(feed_data), "rss.xml": sha256_hex(rss_data)}))[:32]
        feed_rel = f"{self.prefix}/v/{version}/feed.json"
        rss_rel = f"{self.prefix}/v/{version}/rss.xml"
        new_pointer = {
            "schema": POINTER_SCHEMA,
            "version": version,
            "feed_json": feed_rel,
            "rss_xml": rss_rel,
            "feed_json_sha256": sha256_hex(feed_data),
            "rss_xml_sha256": sha256_hex(rss_data),
            "latest_bundle_sha256": ctx.bundle_sha256,
            "items": len(items),
        }
        files = (
            PlannedFile(f"{ctx.target}/{feed_rel}", feed_data, FEED_JSON),
            PlannedFile(f"{ctx.target}/{rss_rel}", rss_data, RSS),
        )
        pointer_write = PointerWrite(ctx.path(self.prefix, POINTER_NAME), canonical_utf8(new_pointer), JSON, etag)
        return DeliveryPlan(self.destination, files, {**entry, "version": version}, pointer_write)


class PreviewAdapter(_Base):
    """Instagram and YouTube: the exact platform payload, re-checked against the published limits,
    written as a preview. Nothing is posted; posting needs Meta and Google app approval."""

    CHECKS = {"instagram": render.instagram_problems, "youtube": render.youtube_problems}

    def __init__(self, destination: str, base_url: str = "") -> None:
        if destination not in self.CHECKS:
            raise ValueError(f"{destination} is not a preview destination")
        super().__init__(base_url)
        self.destination = destination
        self.prefix = f"previews/{destination}"

    def plan(self, ctx: DeliveryContext, payload: bytes, reader: BlobReader) -> DeliveryPlan:
        require_approved(ctx, self.destination)
        body = _load(payload, self.destination)
        problems = self.CHECKS[self.destination](body)
        if problems:
            raise DestinationRefused("platform_limit", "; ".join(problems))
        path = ctx.path(self.prefix, ctx.bundle_sha256, "payload.json")
        entry = {"delivery": dest_mod.PREVIEW, "note": dest_mod.PREVIEW_LABEL, "mode": body.get("mode"), "posted": False, "preview": path}
        return DeliveryPlan(self.destination, (PlannedFile(path, payload, JSON),), entry)


class OutboxAdapter(_Base):
    """Newsroom Teams and journalist email: the exact payload plus the queue message a send flow
    reads. The send is a Logic App step that is not deployed yet, so the receipt says not sent."""

    def __init__(self, destination: str, base_url: str = "") -> None:
        if destination not in OUTBOX_QUEUES:
            raise ValueError(f"{destination} is not an outbox destination")
        super().__init__(base_url)
        self.destination = destination
        self.prefix = f"outbox/{destination}"

    def plan(self, ctx: DeliveryContext, payload: bytes, reader: BlobReader) -> DeliveryPlan:
        require_approved(ctx, self.destination)
        body = _load(payload, self.destination)
        payload_path = ctx.path(self.prefix, ctx.bundle_sha256, "payload.json")
        message_path = ctx.path(self.prefix, ctx.bundle_sha256, "message.json")
        parent = ctx.manifest.get("parent_bundle_sha256")
        message = {
            "schema": OUTBOX_SCHEMA,
            "queue": OUTBOX_QUEUES[self.destination],
            "destination": self.destination,
            "target": ctx.target,
            "bundle_sha256": ctx.bundle_sha256,
            "approval_id": ctx.approval_id,
            "approved_at": ctx.approved_at,
            "mode": body.get("mode"),
            "payload_path": payload_path,
            "payload_sha256": ctx.manifest["destinations"][self.destination]["sha256"],
            "links": {
                "manifest": self.url(ctx.path(ctx.bundle_sha256, "manifest.json")),
                "recap": self._site_page(ctx),
            },
            "parent_bundle_sha256": parent,
        }
        files = (PlannedFile(payload_path, payload, JSON), PlannedFile(message_path, canonical_utf8(message), JSON))
        entry = {
            "delivery": dest_mod.OUTBOX,
            "note": dest_mod.OUTBOX_LABEL,
            "queue": OUTBOX_QUEUES[self.destination],
            "message": message_path,
            "sent": False,
        }
        return DeliveryPlan(self.destination, files, entry)


def default_adapters(base_url: str = "") -> dict[str, Any]:
    """One adapter per destination, keyed by id."""
    return {
        "club_site": ClubSiteAdapter(base_url),
        "feeds": FeedsAdapter(base_url),
        "instagram": PreviewAdapter("instagram", base_url),
        "youtube": PreviewAdapter("youtube", base_url),
        "newsroom_teams": OutboxAdapter("newsroom_teams", base_url),
        "journalist_email": OutboxAdapter("journalist_email", base_url),
    }
