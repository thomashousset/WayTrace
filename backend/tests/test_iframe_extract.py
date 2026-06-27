"""Tests for the iframe source extractor."""
from __future__ import annotations

import pytest

from services.extractor.iframe_extract import extract_iframe_sources


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------


def test_youtube_iframe_detected():
    """A YouTube embed iframe is classified as youtube."""
    html = '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>'
    results = extract_iframe_sources(html)
    assert len(results) == 1
    assert results[0]["service"] == "youtube"
    assert results[0]["url"] == "https://www.youtube.com/embed/dQw4w9WgXcQ"
    assert results[0]["domain"] == "www.youtube.com"


def test_google_maps_detected():
    """A Google Maps embed iframe is classified as google_maps."""
    html = '<iframe src="https://www.google.com/maps/embed?pb=xyz"></iframe>'
    results = extract_iframe_sources(html)
    assert len(results) == 1
    assert results[0]["service"] == "google_maps"
    assert results[0]["domain"] == "www.google.com"


def test_vimeo_detected():
    """A Vimeo embed iframe is classified as vimeo."""
    html = '<iframe src="https://player.vimeo.com/video/123456789"></iframe>'
    results = extract_iframe_sources(html)
    assert len(results) == 1
    assert results[0]["service"] == "vimeo"
    assert results[0]["domain"] == "player.vimeo.com"


def test_unknown_service():
    """An iframe from an unrecognised domain is classified as unknown."""
    html = '<iframe src="https://example.com/widget"></iframe>'
    results = extract_iframe_sources(html)
    assert len(results) == 1
    assert results[0]["service"] == "unknown"
    assert results[0]["domain"] == "example.com"


def test_multiple_iframes():
    """Multiple distinct iframes are all returned."""
    html = (
        '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
        '<iframe src="https://player.vimeo.com/video/111"></iframe>'
        '<iframe src="https://example.com/widget"></iframe>'
    )
    results = extract_iframe_sources(html)
    assert len(results) == 3
    services = {r["service"] for r in results}
    assert services == {"youtube", "vimeo", "unknown"}


def test_two_youtube_videos_both_kept():
    """Two YouTube iframes with different URLs are both kept (not deduped)."""
    html = (
        '<iframe src="https://www.youtube.com/embed/video1"></iframe>'
        '<iframe src="https://www.youtube.com/embed/video2"></iframe>'
    )
    results = extract_iframe_sources(html)
    assert len(results) == 2
    assert all(r["service"] == "youtube" for r in results)


def test_typeform_detected():
    """A Typeform embed iframe is classified as typeform."""
    html = '<iframe src="https://form.typeform.com/to/abc123"></iframe>'
    results = extract_iframe_sources(html)
    assert len(results) == 1
    assert results[0]["service"] == "typeform"
    assert results[0]["domain"] == "form.typeform.com"


# ---------------------------------------------------------------------------
# False-positive / skip tests
# ---------------------------------------------------------------------------


def test_skip_empty_src():
    """An iframe with an empty src attribute produces no results."""
    html = '<iframe src=""></iframe>'
    results = extract_iframe_sources(html)
    assert results == []


def test_skip_about_blank():
    """An iframe with src=about:blank is ignored."""
    html = '<iframe src="about:blank"></iframe>'
    results = extract_iframe_sources(html)
    assert results == []


def test_skip_javascript_void():
    """An iframe with a javascript: src is ignored."""
    html = '<iframe src="javascript:void(0)"></iframe>'
    results = extract_iframe_sources(html)
    assert results == []


def test_skip_web_archive_org():
    """An iframe pointing to web.archive.org is skipped."""
    html = '<iframe src="https://web.archive.org/web/20200101/https://example.com/"></iframe>'
    results = extract_iframe_sources(html)
    assert results == []


def test_skip_doubleclick():
    """An iframe from doubleclick.net is skipped as an ad domain."""
    html = '<iframe src="https://www.doubleclick.net/instream/ad_status.js"></iframe>'
    results = extract_iframe_sources(html)
    assert results == []


def test_skip_googlesyndication():
    """An iframe from googlesyndication.com is skipped as an ad domain."""
    html = '<iframe src="https://pagead2.googlesyndication.com/pagead/show_ads.js"></iframe>'
    results = extract_iframe_sources(html)
    assert results == []


def test_classify_service_host_suffix_not_substring():
    """fake-youtube.com.evil.tld must NOT classify as youtube (no substring match)."""
    from services.extractor.iframe_extract import _classify_service
    assert _classify_service("https://fake-youtube.com.evil.tld/x") == "unknown"
    assert _classify_service("https://youtube.com.phishing.example/x") == "unknown"
    assert _classify_service("https://youtube.com/watch?v=x") == "youtube"
    assert _classify_service("https://music.youtube.com/x") == "youtube"


def test_google_maps_requires_maps_path():
    """google.com hostname must only classify as google_maps with /maps path."""
    from services.extractor.iframe_extract import _classify_service
    assert _classify_service("https://google.com/") == "unknown"
    assert _classify_service("https://google.com/maps/embed?x") == "google_maps"
    assert _classify_service("https://maps.google.com/embed") == "google_maps"
