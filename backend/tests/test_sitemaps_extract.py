"""Tests for the sitemaps_and_robots extractor."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str, url: str = "https://example.com/") -> list[dict]:
    pages = [{"html": html, "url": url, "timestamp": "20220601120000"}]
    return extract_all(pages, "example.com")["sitemaps_and_robots"]


def _kinds(items: list[dict]) -> set[str]:
    return {it["kind"] for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_sitemap_xml_link():
    html = '<a href="https://example.com/sitemap.xml">sitemap</a>'
    assert "sitemap" in _kinds(_run(html))


def test_detects_sitemap_index():
    html = '<a href="https://example.com/sitemap_index.xml">idx</a>'
    assert any("sitemap_index.xml" in it["url"] for it in _run(html))


def test_detects_robots_txt():
    html = '<a href="/robots.txt">robots</a>'
    items = _run(html, url="https://example.com/page")
    assert any(it["kind"] == "robots" for it in items)


def test_detects_security_txt_well_known():
    html = '<a href="https://example.com/.well-known/security.txt">disclosure</a>'
    items = _run(html)
    assert any(it["kind"] == "security" for it in items)


def test_detects_humans_txt():
    html = '<a href="https://example.com/humans.txt">credits</a>'
    items = _run(html)
    assert any(it["kind"] == "humans" for it in items)


def test_detects_rel_sitemap_link():
    html = '<link rel="sitemap" type="application/xml" href="/sitemap.xml">'
    items = _run(html, url="https://example.com/")
    assert "sitemap" in _kinds(items)


def test_detects_custom_sitemap_filename():
    html = '<a href="https://example.com/sitemap-posts.xml">posts</a>'
    items = _run(html)
    assert "sitemap" in _kinds(items)


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_ignores_empty_page():
    assert _run("<html></html>") == []


def test_no_false_positive_plain_xml_file():
    html = '<a href="https://example.com/data.xml">data</a>'
    assert _run(html) == []


def test_no_match_for_word_robots_in_text():
    html = "<p>Our robots work hard.</p>"
    assert _run(html) == []


def test_skips_wayback_host():
    html = '<a href="https://web.archive.org/web/2020/https://example.com/robots.txt">cached</a>'
    items = _run(html)
    # The URL sweep should discard the web.archive.org host.
    assert all("web.archive.org" not in it["url"] for it in items)


def test_no_bare_security_filename():
    html = "<p>security is important</p>"
    assert _run(html) == []


# ---------------------------------------------------------------------------
# Programmatic-ad disclosures (ads.txt / app-ads.txt)
# ---------------------------------------------------------------------------


def test_detects_ads_txt():
    html = '<a href="https://example.com/ads.txt">disclosure</a>'
    items = _run(html)
    assert any(it["kind"] == "ads" and it["url"].endswith("/ads.txt") for it in items)


def test_detects_app_ads_txt():
    html = '<a href="https://example.com/app-ads.txt">disclosure</a>'
    items = _run(html)
    assert any(it["kind"] == "ads" and it["url"].endswith("/app-ads.txt") for it in items)


def test_no_match_for_random_ads_word():
    html = "<p>We display ads on this page.</p>"
    assert _run(html) == []

