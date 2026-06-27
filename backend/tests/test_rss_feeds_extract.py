"""Tests for the rss_feeds extractor."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str, url: str = "https://example.com/") -> list[dict]:
    pages = [{"html": html, "url": url, "timestamp": "20220601120000"}]
    return extract_all(pages, "example.com")["rss_feeds"]


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_rss_link_alternate():
    html = (
        '<link rel="alternate" type="application/rss+xml" '
        'title="Blog feed" href="https://example.com/feed/">'
    )
    items = _run(html)
    assert any(it["feed_type"] == "rss" for it in items)
    assert any(it["title"] == "Blog feed" for it in items)


def test_detects_atom_link_alternate():
    html = (
        '<link rel="alternate" type="application/atom+xml" '
        'href="https://example.com/atom.xml">'
    )
    items = _run(html)
    assert any(it["feed_type"] == "atom" for it in items)


def test_resolves_relative_feed_href():
    html = '<link rel="alternate" type="application/rss+xml" href="/feed/">'
    items = _run(html, url="https://site.example/page")
    assert any(it["url"].startswith("https://site.example/feed") for it in items)


def test_detects_bare_feed_url_in_body():
    html = '<a href="https://blog.example.com/rss.xml">RSS</a>'
    items = _run(html)
    assert any("rss.xml" in it["url"] for it in items)


def test_detects_feed_slash_in_body():
    html = '<a href="https://site.example/blog/feed/">Subscribe</a>'
    items = _run(html)
    assert any(it["url"].endswith("/feed/") for it in items)


def test_classifies_atom_from_url_only():
    html = '<a href="https://ex.com/atom.xml">atom</a>'
    items = _run(html)
    assert any(it["feed_type"] == "atom" for it in items)


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_ignores_link_alternate_without_feed_type():
    # <link rel="alternate" hreflang=...> is for translation, not feeds.
    html = '<link rel="alternate" hreflang="en" href="https://example.com/en/">'
    assert _run(html) == []


def test_ignores_stylesheet_link():
    html = '<link rel="stylesheet" href="https://example.com/feed.xml">'
    assert _run(html) == []


def test_empty_html_no_feeds():
    assert _run("<html></html>") == []


def test_no_match_for_word_feed_in_text():
    html = "<p>I need to feed the cat.</p>"
    assert _run(html) == []


def test_no_match_for_non_http_scheme():
    html = '<a href="file:///tmp/feed.xml">local</a>'
    assert _run(html) == []


# ---------------------------------------------------------------------------
# Extended URL pattern coverage (Blogger, Hugo, WordPress query feeds)
# ---------------------------------------------------------------------------


def test_detects_blogger_feeds_posts_default():
    html = '<a href="https://example.com/feeds/posts/default">blogger</a>'
    items = _run(html)
    assert any("/feeds/posts/default" in it["url"] for it in items)


def test_detects_hugo_index_xml():
    html = '<a href="https://example.com/blog/index.xml">hugo</a>'
    items = _run(html)
    assert any(it["url"].endswith("/index.xml") for it in items)


def test_detects_wordpress_feed_query():
    html = '<a href="https://example.com/?feed=rss2">wp-rss</a>'
    items = _run(html)
    assert any("?feed=rss2" in it["url"] for it in items)


def test_detects_wordpress_atom_feed_query():
    html = '<a href="https://example.com/?feed=atom">wp-atom</a>'
    items = _run(html)
    found = [it for it in items if "?feed=atom" in it["url"]]
    assert found
    assert found[0]["feed_type"] == "atom"

