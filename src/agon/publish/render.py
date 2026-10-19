"""The exact payload of every publish destination, rendered from the checked English edition.

Rendered at seal time (`agon.fleet.steps.seal_step`) and sealed into the bundle: each offered
destination's payload is the artifact `destination-<id>.json`, and the manifest names its sha256
(`agon.governance.seal`). Nothing here knows the bundle hash, the publish time or any URL, because
the payload is inside the hash; the adapters add those at publish time and record them in the
receipt. Every payload is canonical JSON (`seal.canonical_utf8`) with
`{"schema": "agon.destination/1", "destination": <id>, ...}`.

Errata (a bundle that names its parent) render differently per destination:

* club_site and feeds: a correction that supersedes the parent; the parent's bytes stay where they
  are and are marked superseded by the adapters, never overwritten;
* instagram and youtube: an edited caption or description preview of the parent's post;
* newsroom_teams: a threaded reply under the parent's notification;
* journalist_email: a correction digest naming the report it corrects.

Instagram and YouTube payloads are previews and say so in one sentence (`PREVIEW_LABEL`). Their
platform limits are constants below, each the platform's published limit as read on 2026-10-19
at the URL given; `instagram_problems` and `youtube_problems` refuse a payload over any of them.

Standard library only. All names come from the synthetic, fictional world.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from agon.governance import destinations as dest_mod
from agon.governance.seal import canonical_utf8

SCHEMA = dest_mod.PAYLOAD_SCHEMA
SOCIAL_DISCLOSURE = "AI-drafted report of a synthetic match. Numbers and names checked by code."

# Instagram, read 2026-10-19 at
# https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
# "Maximum 2200 characters, 30 hashtags, and 20 @ tags." and "Alternative text, up to 1000
# character, for an image." Image specification on the same page: "Format: JPEG", "File size:
# 8 MB maximum.", "Aspect ratio: Must be within a 4:5 to 1.91:1 range", "Minimum width: 320",
# "Maximum width: 1440", "Color Space: sRGB". Characters are counted here as Unicode code points;
# how Instagram counts non-ASCII characters is not stated on that page (ESTIMATE).
INSTAGRAM_CAPTION_MAX_CHARS = 2200
INSTAGRAM_MAX_HASHTAGS = 30
INSTAGRAM_MAX_MENTIONS = 20
INSTAGRAM_ALT_TEXT_MAX_CHARS = 1000
INSTAGRAM_IMAGE_FORMAT = "JPEG"
INSTAGRAM_IMAGE_MAX_BYTES = 8 * 1024 * 1024
INSTAGRAM_MIN_WIDTH = 320
INSTAGRAM_MAX_WIDTH = 1440
INSTAGRAM_ASPECT_MIN = (4, 5)  # width:height, the tallest allowed
INSTAGRAM_ASPECT_MAX = (191, 100)  # 1.91:1, the widest allowed
INSTAGRAM_COLOR_SPACE = "sRGB"

# YouTube Data API v3, videos resource, read 2026-10-19 at
# https://developers.google.com/youtube/v3/docs/videos
# snippet.title: "a maximum length of 100 characters and may contain all valid UTF-8 characters
# except < and >"; snippet.description: "a maximum length of 5000 bytes" with the same exclusion;
# snippet.tags[]: "a maximum length of 500 characters", where commas between items count and a tag
# containing a space counts two more characters for the quotation marks the server adds.
# Community posts have no public API, so no Community post text is rendered.
YOUTUBE_TITLE_MAX_CHARS = 100
YOUTUBE_DESCRIPTION_MAX_BYTES = 5000
YOUTUBE_TAGS_MAX_CHARS = 500
YOUTUBE_FORBIDDEN_CHARS = ("<", ">")

FEED_SUMMARY_SENTENCES = 1


@dataclass(frozen=True)
class ReportView:
    """What every renderer reads: the checked English edition and the match it reports on."""

    match_id: str
    league: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    sentences: tuple[str, ...]
    edition: Mapping[str, Any]
    disclosure: str
    parent_bundle_sha256: str | None = None

    @property
    def is_erratum(self) -> bool:
        return self.parent_bundle_sha256 is not None

    @property
    def score_line(self) -> str:
        return f"{self.home} {self.home_goals}-{self.away_goals} {self.away}"

    @property
    def title(self) -> str:
        base = f"{self.score_line}: match report"
        return f"Correction: {base}" if self.is_erratum else base


def _envelope(dest_id: str, view: ReportView, body: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema": SCHEMA, "destination": dest_id, "match_id": view.match_id, **body}


def _hashtag(name: str) -> str | None:
    words = re.findall(r"[A-Za-z0-9]+", name)
    tag = "".join(w[:1].upper() + w[1:] for w in words)
    return f"#{tag}" if tag else None


def _slug(name: str) -> str:
    return "-".join(w.lower() for w in re.findall(r"[A-Za-z0-9]+", name))


def _strip_angles(text: str) -> str:
    return text.replace("<", "").replace(">", "")


# ----------------------------------------------------------------- club website and feeds


def club_site(view: ReportView) -> dict[str, Any]:
    paragraphs = "".join(f"<p>{html.escape(s)}</p>" for s in view.sentences)
    correction = (
        f'<p class="agon-correction">This corrected report replaces an earlier version (bundle {view.parent_bundle_sha256}).</p>'
        if view.is_erratum else ""
    )
    page = (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(view.title)}</title></head>"
        f"<body><main><article><h1>{html.escape(view.title)}</h1>"
        f"<p>{html.escape(view.league)}</p>{correction}{paragraphs}"
        f"<footer><p>{html.escape(view.disclosure)}</p></footer>"
        "</article></main></body></html>"
    )
    return _envelope("club_site", view, {
        "title": view.title,
        "language": "en",
        "recap_html": page,
        "edition": dict(view.edition),
        "supersedes_bundle_sha256": view.parent_bundle_sha256,
    })


def feeds(view: ReportView) -> dict[str, Any]:
    summary = " ".join(view.sentences[:FEED_SUMMARY_SENTENCES])
    body = list(view.sentences)
    if view.is_erratum:
        body.insert(0, "Correction: this report replaces an earlier version.")
    return _envelope("feeds", view, {
        "title": view.title,
        "summary": summary,
        "content_text": " ".join([*body, view.disclosure]),
        "content_html": "".join(f"<p>{html.escape(s)}</p>" for s in [*body, view.disclosure]),
        "language": "en",
        "tags": ["match-report", *(t for t in (_slug(view.home), _slug(view.away)) if t)],
        "supersedes_bundle_sha256": view.parent_bundle_sha256,
    })


# ----------------------------------------------------------------- Instagram (preview)


def _instagram_caption(view: ReportView, sentences: list[str], hashtags: list[str]) -> str:
    head = view.title if not view.is_erratum else f"{view.title}. Caption edited."
    parts = [head, "", *sentences, "", SOCIAL_DISCLOSURE, "", " ".join(hashtags)]
    return "\n".join(parts).strip()


def instagram(view: ReportView) -> dict[str, Any]:
    tags = [t for t in (_hashtag(view.home), _hashtag(view.away), _hashtag(view.league), "#MatchReport") if t]
    hashtags = list(dict.fromkeys(tags))[:INSTAGRAM_MAX_HASHTAGS]
    sentences = list(view.sentences)
    caption = _instagram_caption(view, sentences, hashtags)
    while len(caption) > INSTAGRAM_CAPTION_MAX_CHARS and sentences:
        sentences.pop()
        caption = _instagram_caption(view, sentences, hashtags)
    alt = f"Score card: {view.score_line}, {view.league}. Text only, no logos."
    payload = _envelope("instagram", view, {
        "label": dest_mod.PREVIEW_LABEL,
        "mode": "edit_caption" if view.is_erratum else "new_post",
        "edits_post_of_bundle_sha256": view.parent_bundle_sha256,
        "caption": caption,
        "hashtags": hashtags,
        "alt_text": alt[:INSTAGRAM_ALT_TEXT_MAX_CHARS],
        "media": {
            "type": "IMAGE",
            "format": INSTAGRAM_IMAGE_FORMAT,
            "width": 1080,
            "height": 1350,
            "aspect_ratio": "4:5",
            "color_space": INSTAGRAM_COLOR_SPACE,
            "max_bytes": INSTAGRAM_IMAGE_MAX_BYTES,
            "image": None,
            "note": "The score card image is not produced in this build; this is the specification it must meet.",
        },
        "limits": {
            "caption_max_chars": INSTAGRAM_CAPTION_MAX_CHARS,
            "max_hashtags": INSTAGRAM_MAX_HASHTAGS,
            "max_mentions": INSTAGRAM_MAX_MENTIONS,
            "alt_text_max_chars": INSTAGRAM_ALT_TEXT_MAX_CHARS,
        },
    })
    problems = instagram_problems(payload)
    if problems:
        raise ValueError("instagram payload over a platform limit: " + "; ".join(problems))
    return payload


def instagram_problems(payload: Mapping[str, Any]) -> list[str]:
    """Every published Instagram limit the payload breaks. Empty means within limits."""
    problems: list[str] = []
    caption = payload.get("caption")
    if not isinstance(caption, str) or not caption:
        return ["caption must be a non-empty string"]
    if len(caption) > INSTAGRAM_CAPTION_MAX_CHARS:
        problems.append(f"caption has {len(caption)} characters, over {INSTAGRAM_CAPTION_MAX_CHARS}")
    hashtags = re.findall(r"(?<![\w#])#\w+", caption)
    if len(hashtags) > INSTAGRAM_MAX_HASHTAGS:
        problems.append(f"caption has {len(hashtags)} hashtags, over {INSTAGRAM_MAX_HASHTAGS}")
    mentions = re.findall(r"(?<![\w@])@[\w.]+", caption)
    if len(mentions) > INSTAGRAM_MAX_MENTIONS:
        problems.append(f"caption has {len(mentions)} @ tags, over {INSTAGRAM_MAX_MENTIONS}")
    alt = payload.get("alt_text")
    if not isinstance(alt, str) or len(alt) > INSTAGRAM_ALT_TEXT_MAX_CHARS:
        problems.append(f"alt_text must be a string of at most {INSTAGRAM_ALT_TEXT_MAX_CHARS} characters")
    media = payload.get("media")
    if not isinstance(media, dict):
        return problems + ["media must be an object"]
    if media.get("format") != INSTAGRAM_IMAGE_FORMAT:
        problems.append(f"media format must be {INSTAGRAM_IMAGE_FORMAT}")
    width, height = media.get("width"), media.get("height")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
        problems.append("media width and height must be positive integers")
    else:
        if not INSTAGRAM_MIN_WIDTH <= width <= INSTAGRAM_MAX_WIDTH:
            problems.append(f"media width {width} is outside {INSTAGRAM_MIN_WIDTH} to {INSTAGRAM_MAX_WIDTH}")
        lo_w, lo_h = INSTAGRAM_ASPECT_MIN
        hi_w, hi_h = INSTAGRAM_ASPECT_MAX
        if width * lo_h < lo_w * height or width * hi_h > hi_w * height:
            problems.append(f"media aspect ratio {width}:{height} is outside 4:5 to 1.91:1")
    max_bytes = media.get("max_bytes")
    if not isinstance(max_bytes, int) or max_bytes > INSTAGRAM_IMAGE_MAX_BYTES:
        problems.append(f"media max_bytes must be at most {INSTAGRAM_IMAGE_MAX_BYTES}")
    if payload.get("label") != dest_mod.PREVIEW_LABEL:
        problems.append("an Instagram payload must carry the preview label")
    return problems


# ----------------------------------------------------------------- YouTube (preview)


def youtube_tag_chars(tags: Iterable[str]) -> int:
    """Length of a tag list as the YouTube Data API counts it (commas and added quotes count)."""
    items = list(tags)
    return sum(len(t) + (2 if " " in t else 0) for t in items) + max(len(items) - 1, 0)


def youtube(view: ReportView) -> dict[str, Any]:
    title = _strip_angles(view.title)
    if len(title) > YOUTUBE_TITLE_MAX_CHARS:
        title = title[:YOUTUBE_TITLE_MAX_CHARS].rstrip()
    lines = [_strip_angles(s) for s in view.sentences]
    if view.is_erratum:
        lines.insert(0, "Correction: this description replaces the earlier one.")
    footer = _strip_angles(SOCIAL_DISCLOSURE)

    def describe(body: list[str]) -> str:
        return "\n\n".join([*body, footer])

    description = describe(lines)
    while len(description.encode("utf-8")) > YOUTUBE_DESCRIPTION_MAX_BYTES and lines:
        lines.pop()
        description = describe(lines)
    tags: list[str] = []
    for tag in ("match report", view.home, view.away, view.league, "synthetic match"):
        clean = _strip_angles(tag)
        if clean and clean not in tags and youtube_tag_chars([*tags, clean]) <= YOUTUBE_TAGS_MAX_CHARS:
            tags.append(clean)
    payload = _envelope("youtube", view, {
        "label": dest_mod.PREVIEW_LABEL,
        "mode": "edit_metadata" if view.is_erratum else "new_video",
        "edits_video_of_bundle_sha256": view.parent_bundle_sha256,
        "video": {"title": title, "description": description, "tags": tags},
        "media": {
            "source": "the English audio report with a still score card",
            "note": "The video file is not produced in this build; the metadata is what would be sent.",
        },
        "limits": {
            "title_max_chars": YOUTUBE_TITLE_MAX_CHARS,
            "description_max_bytes": YOUTUBE_DESCRIPTION_MAX_BYTES,
            "tags_max_chars": YOUTUBE_TAGS_MAX_CHARS,
        },
    })
    problems = youtube_problems(payload)
    if problems:
        raise ValueError("youtube payload over a platform limit: " + "; ".join(problems))
    return payload


def youtube_problems(payload: Mapping[str, Any]) -> list[str]:
    """Every published YouTube Data API limit the payload breaks. Empty means within limits."""
    video = payload.get("video")
    if not isinstance(video, dict):
        return ["video must be an object"]
    problems: list[str] = []
    title, description, tags = video.get("title"), video.get("description"), video.get("tags")
    if not isinstance(title, str) or not title:
        problems.append("video title must be a non-empty string")
    elif len(title) > YOUTUBE_TITLE_MAX_CHARS:
        problems.append(f"video title has {len(title)} characters, over {YOUTUBE_TITLE_MAX_CHARS}")
    if not isinstance(description, str):
        problems.append("video description must be a string")
    elif len(description.encode("utf-8")) > YOUTUBE_DESCRIPTION_MAX_BYTES:
        problems.append(f"video description is {len(description.encode('utf-8'))} bytes, over {YOUTUBE_DESCRIPTION_MAX_BYTES}")
    for name, value in (("title", title), ("description", description)):
        if isinstance(value, str) and any(c in value for c in YOUTUBE_FORBIDDEN_CHARS):
            problems.append(f"video {name} may not contain < or >")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        problems.append("video tags must be a list of strings")
    elif youtube_tag_chars(tags) > YOUTUBE_TAGS_MAX_CHARS:
        problems.append(f"video tags count {youtube_tag_chars(tags)} characters, over {YOUTUBE_TAGS_MAX_CHARS}")
    if payload.get("label") != dest_mod.PREVIEW_LABEL:
        problems.append("a YouTube payload must carry the preview label")
    return problems


# ----------------------------------------------------------------- newsroom Teams and journalist email


def newsroom_teams(view: ReportView) -> dict[str, Any]:
    heading = f"Correction published: {view.score_line}" if view.is_erratum else f"Report published: {view.score_line}"
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": heading, "weight": "Bolder", "size": "Medium", "wrap": True},
            {"type": "TextBlock", "text": view.league, "isSubtle": True, "wrap": True},
            *({"type": "TextBlock", "text": s, "wrap": True} for s in view.sentences[:2]),
            {"type": "TextBlock", "text": view.disclosure, "isSubtle": True, "wrap": True, "size": "Small"},
        ],
    }
    return _envelope("newsroom_teams", view, {
        "mode": "reply" if view.is_erratum else "new_post",
        "reply_to_bundle_sha256": view.parent_bundle_sha256,
        "card": card,
    })


def journalist_email(view: ReportView) -> dict[str, Any]:
    subject = f"Match report digest: {view.title}"
    intro = (
        f"A corrected report replaces the earlier version (bundle {view.parent_bundle_sha256})."
        if view.is_erratum else "The club media office approved this report."
    )
    paragraphs = "".join(f"<p>{html.escape(s)}</p>" for s in view.sentences)
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape(subject)}</title></head><body>"
        f"<h1>{html.escape(view.title)}</h1><p>{html.escape(intro)}</p>{paragraphs}"
        f"<p><small>{html.escape(view.disclosure)}</small></p></body></html>"
    )
    text = "\n\n".join([view.title, intro, *view.sentences, view.disclosure])
    return _envelope("journalist_email", view, {
        "mode": "correction" if view.is_erratum else "digest",
        "corrects_bundle_sha256": view.parent_bundle_sha256,
        "subject": subject,
        "html": page,
        "text": text,
    })


RENDERERS = {
    "club_site": club_site,
    "feeds": feeds,
    "instagram": instagram,
    "youtube": youtube,
    "newsroom_teams": newsroom_teams,
    "journalist_email": journalist_email,
}


def render(dest_id: str, view: ReportView) -> bytes:
    """One destination's payload bytes, canonical JSON."""
    if dest_id not in RENDERERS:
        raise ValueError(f"unknown destination {dest_id!r}")
    return canonical_utf8(RENDERERS[dest_id](view))


def render_all(view: ReportView, offered: Iterable[str] = dest_mod.DESTINATION_IDS) -> dict[str, bytes]:
    """The payload of every offered destination, keyed by id, ready for `seal(destinations=...)`."""
    wanted = tuple(offered)
    unknown = sorted(set(wanted) - set(dest_mod.LABELS))
    if unknown:
        raise ValueError(f"unknown destinations {unknown}")
    return {dest_id: render(dest_id, view) for dest_id in dest_mod.in_order(wanted)}
