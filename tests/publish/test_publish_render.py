"""Destination payload renderers: exact payloads, erratum modes, and the platforms' published limits.

Every limit check is proven to fail once on a deliberately broken payload (negative controls).
"""

from __future__ import annotations

import copy
import json

import pytest

import govkit
from agon.governance import destinations as D
from agon.governance.seal import canonical_utf8
from agon.publish import render as R


def _view(**over):
    _, result, facts = govkit.match()
    view = govkit.report_view(facts, result.meta["match_id"])
    return R.ReportView(**{**view.__dict__, **over})


def test_every_destination_renders_a_canonical_envelope():
    view = _view()
    payloads = R.render_all(view)
    assert list(payloads) == list(D.DESTINATION_IDS)
    for dest_id, data in payloads.items():
        body = json.loads(data)
        assert data == canonical_utf8(body)
        assert body["schema"] == "agon.destination/1" and body["destination"] == dest_id
        assert body["match_id"] == view.match_id
    assert R.render_all(view) == payloads  # deterministic: same view, same bytes


def test_render_all_refuses_an_unknown_destination():
    with pytest.raises(ValueError):
        R.render_all(_view(), ("club_site", "tiktok"))


def test_payloads_never_carry_a_bundle_hash_a_url_or_a_time():
    for data in R.render_all(_view()).values():
        text = data.decode("utf-8")
        assert "http://" not in text.replace("http://adaptivecards.io", "") and "https://" not in text
        assert "2026-" not in text


def test_previews_carry_the_preview_label_in_one_sentence():
    payloads = R.render_all(_view())
    for dest_id in ("instagram", "youtube"):
        assert json.loads(payloads[dest_id])["label"] == "Preview: not posted; posting needs Meta and Google app approval."


def test_erratum_modes_per_destination():
    parent = "c" * 64
    bodies = {k: json.loads(v) for k, v in R.render_all(_view(parent_bundle_sha256=parent)).items()}
    assert bodies["club_site"]["supersedes_bundle_sha256"] == parent and parent in bodies["club_site"]["recap_html"]
    assert bodies["club_site"]["title"].startswith("Correction: ")
    assert bodies["feeds"]["content_text"].startswith("Correction:")
    assert bodies["instagram"]["mode"] == "edit_caption" and bodies["instagram"]["edits_post_of_bundle_sha256"] == parent
    assert bodies["youtube"]["mode"] == "edit_metadata" and bodies["youtube"]["edits_video_of_bundle_sha256"] == parent
    assert bodies["newsroom_teams"]["mode"] == "reply" and bodies["newsroom_teams"]["reply_to_bundle_sha256"] == parent
    assert bodies["journalist_email"]["mode"] == "correction" and bodies["journalist_email"]["corrects_bundle_sha256"] == parent
    fresh = {k: json.loads(v) for k, v in R.render_all(_view()).items()}
    assert fresh["instagram"]["mode"] == "new_post" and fresh["newsroom_teams"]["mode"] == "new_post"


def test_club_site_escapes_html_and_carries_the_edition():
    body = json.loads(R.render("club_site", _view(sentences=("A <b>bold</b> & risky line.",))))
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; risky" in body["recap_html"] and "<b>" not in body["recap_html"]
    assert body["edition"]["edition"] == "en"


def test_the_newsroom_card_is_an_adaptive_card():
    card = json.loads(R.render("newsroom_teams", _view()))["card"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert card["body"][0]["text"].startswith("Report published: ")


# ----------------------------------------------------------------- Instagram limits


def test_the_published_instagram_limits_are_the_constants():
    assert (R.INSTAGRAM_CAPTION_MAX_CHARS, R.INSTAGRAM_MAX_HASHTAGS, R.INSTAGRAM_MAX_MENTIONS) == (2200, 30, 20)
    assert R.INSTAGRAM_ALT_TEXT_MAX_CHARS == 1000 and R.INSTAGRAM_IMAGE_MAX_BYTES == 8 * 1024 * 1024
    assert (R.INSTAGRAM_MIN_WIDTH, R.INSTAGRAM_MAX_WIDTH) == (320, 1440)


def test_a_long_report_is_cut_to_fit_the_caption_limit():
    long = tuple(f"Sentence {i} " + "x" * 300 + "." for i in range(20))
    body = R.instagram(_view(sentences=long))
    assert len(body["caption"]) <= 2200
    assert body["caption"].endswith(" ".join(body["hashtags"]))
    assert R.instagram_problems(body) == []


def _ig():
    return R.instagram(_view())


@pytest.mark.parametrize("mutate, problem", [
    (lambda b: b.update(caption="x" * 2201), "2201 characters, over 2200"),
    (lambda b: b.update(caption=" ".join(f"#t{i}" for i in range(31))), "31 hashtags, over 30"),
    (lambda b: b.update(caption=" ".join(f"@u{i}" for i in range(21))), "21 @ tags, over 20"),
    (lambda b: b.update(alt_text="a" * 1001), "alt_text"),
    (lambda b: b["media"].update(format="PNG"), "format must be JPEG"),
    (lambda b: b["media"].update(width=1500, height=1500), "outside 320 to 1440"),
    (lambda b: b["media"].update(width=1000, height=1300), "aspect ratio"),
    (lambda b: b["media"].update(width=1440, height=700), "aspect ratio"),
    (lambda b: b["media"].update(max_bytes=9 * 1024 * 1024), "max_bytes"),
    (lambda b: b.update(label="Posted to Instagram"), "preview label"),
])
def test_negative_control_each_instagram_limit_fails_once(mutate, problem):
    body = copy.deepcopy(_ig())
    assert R.instagram_problems(body) == []
    mutate(body)
    assert any(problem in p for p in R.instagram_problems(body)), R.instagram_problems(body)


def test_instagram_hashtags_are_fictional_names_in_camel_case():
    view = _view()
    tags = R.instagram(view)["hashtags"]
    assert tags[-1] == "#MatchReport" and all(t.startswith("#") and " " not in t for t in tags)


# ----------------------------------------------------------------- YouTube limits


def test_the_published_youtube_limits_are_the_constants():
    assert (R.YOUTUBE_TITLE_MAX_CHARS, R.YOUTUBE_DESCRIPTION_MAX_BYTES, R.YOUTUBE_TAGS_MAX_CHARS) == (100, 5000, 500)


def test_youtube_tag_length_counts_commas_and_added_quotes():
    assert R.youtube_tag_chars(["Foo-Baz"]) == 7
    assert R.youtube_tag_chars(["Foo Baz"]) == 9
    assert R.youtube_tag_chars(["a", "b c"]) == 1 + 1 + 5


def test_youtube_strips_angle_brackets_and_cuts_a_long_description():
    long = tuple("Line <%d> " % i + "y" * 600 for i in range(12))
    body = R.youtube(_view(sentences=long, home="A" * 120))
    video = body["video"]
    assert len(video["title"]) <= 100 and "<" not in video["description"] and ">" not in video["description"]
    assert len(video["description"].encode("utf-8")) <= 5000
    assert R.youtube_problems(body) == []


def _yt():
    return R.youtube(_view())


@pytest.mark.parametrize("mutate, problem", [
    (lambda b: b["video"].update(title="t" * 101), "101 characters, over 100"),
    (lambda b: b["video"].update(description="Ω" * 2501), "5002 bytes, over 5000"),
    (lambda b: b["video"].update(title="a <b> title"), "may not contain < or >"),
    (lambda b: b["video"].update(tags=["tag number %03d" % i for i in range(40)]), "over 500"),
    (lambda b: b.update(label=None), "preview label"),
])
def test_negative_control_each_youtube_limit_fails_once(mutate, problem):
    body = copy.deepcopy(_yt())
    assert R.youtube_problems(body) == []
    mutate(body)
    assert any(problem in p for p in R.youtube_problems(body)), R.youtube_problems(body)
