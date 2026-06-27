"""Tests for the html_comments extractor."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{
        "html": html,
        "url": "https://example.com/",
        "timestamp": "20220601120000",
    }]
    return extract_all(pages, "example.com")["html_comments"]


def _comments(items: list[dict]) -> set[str]:
    return {it["comment"] for it in items}


# ---------------------------------------------------------------------------
# Positive - meaningful author-written comments that should be kept
# ---------------------------------------------------------------------------


def test_keeps_todo_note():
    html = "<body><!-- TODO: fix the broken nav link before launch --></body>"
    assert "TODO: fix the broken nav link before launch" in _comments(_run(html))


def test_keeps_internal_note():
    html = "<body><!-- NOTE: do not remove this hidden form field --></body>"
    assert "NOTE: do not remove this hidden form field" in _comments(_run(html))


def test_keeps_dev_hint():
    html = "<div><!-- staging server creds are in the team wiki, ask Bob --></div>"
    assert any("staging server creds" in c for c in _comments(_run(html)))


def test_keeps_fixme_comment():
    html = "<body><!-- FIXME: hardcoded API endpoint, move to config --></body>"
    assert "FIXME: hardcoded API endpoint, move to config" in _comments(_run(html))


def test_keeps_legacy_markup_note():
    html = "<ul><!-- old pricing table removed in 2021 redesign, kept for ref --></ul>"
    assert any("old pricing table" in c for c in _comments(_run(html)))


def test_keeps_template_author_comment():
    html = "<head><!-- Custom theme by the in-house design team, v2.3 --></head>"
    assert "Custom theme by the in-house design team, v2.3" in _comments(_run(html))


def test_records_temporal_metadata():
    html = "<body><!-- TODO: migrate this page to the new layout system --></body>"
    items = _run(html)
    entry = next(it for it in items if it["comment"].startswith("TODO: migrate"))
    assert entry["first_seen"] == "2022-06"
    assert entry["last_seen"] == "2022-06"
    assert entry["occurrences"] == 1


# ---------------------------------------------------------------------------
# False positives - boilerplate / noise / too-short that must be filtered out
# ---------------------------------------------------------------------------


def test_drops_google_tag_manager():
    html = "<!-- Google Tag Manager --><!-- End Google Tag Manager -->"
    assert _run(html) == []


def test_drops_facebook_pixel():
    html = "<!-- Facebook Pixel Code goes here for tracking visits -->"
    assert _run(html) == []


def test_drops_yoast_seo():
    html = "<!-- This site is optimized with the Yoast SEO plugin v1 -->"
    assert _run(html) == []


def test_drops_webflow_watermark():
    html = "<!-- Last Published: Mon Jan 01 2022 by webflow editor -->"
    assert _run(html) == []


def test_drops_google_analytics():
    html = "<!-- Google Analytics tracking snippet placed in head -->"
    assert _run(html) == []


def test_drops_matomo():
    html = "<!-- Matomo analytics script for the website here -->"
    assert _run(html) == []


def test_drops_conditional_ie_comment():
    html = "<!--[if IE 8]><link rel=stylesheet href=ie.css><![endif]-->"
    assert _run(html) == []


def test_drops_wayback_marker():
    html = "<!-- BEGIN WAYBACK TOOLBAR INSERT -->"
    assert _run(html) == []


def test_drops_begin_section_marker():
    # Starts with "begin " -> excluded as a structural marker.
    html = "<!-- begin header section markup that is long enough -->"
    assert _run(html) == []


def test_drops_end_section_marker():
    # Starts with "end " -> excluded as a structural marker.
    html = "<!-- end of the footer container block here ok -->"
    assert _run(html) == []


def test_drops_too_short_comment():
    html = "<!-- hi -->"
    assert _run(html) == []


def test_drops_empty_comment():
    html = "<body><!---->text<!--   --></body>"
    assert _run(html) == []
