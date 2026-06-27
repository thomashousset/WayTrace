# backend/tests/test_outgoing_links_extract.py
"""Tests for outgoing external link extraction."""
from __future__ import annotations

import pytest

from services.extractor.outgoing_links_extract import extract_outgoing_links


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------

def test_external_link():
    html = '<a href="https://example.org/page">Visit</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["url"] == "https://example.org/page"
    assert results[0]["category"] == "other"
    assert results[0]["service"] == ""


def test_social_twitter():
    html = '<a href="https://twitter.com/user">Twitter</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "twitter"


def test_social_linkedin():
    html = '<a href="https://linkedin.com/in/johndoe">LinkedIn</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "linkedin"


def test_social_facebook():
    html = '<a href="https://facebook.com/mypage">Facebook</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "facebook"


def test_social_instagram():
    html = '<a href="https://instagram.com/myhandle">Instagram</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "instagram"


def test_social_youtube():
    html = '<a href="https://youtube.com/channel/UC123">YouTube</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "youtube"


def test_social_github():
    html = '<a href="https://github.com/myorg/repo">GitHub</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "github"


def test_social_discord():
    html = '<a href="https://discord.gg/abcdef">Join Discord</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "discord"


def test_social_reddit():
    html = '<a href="https://reddit.com/r/mysubreddit">Reddit</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "reddit"


def test_social_mastodon():
    html = '<a href="https://mastodon.social/@user">Mastodon</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 1
    assert results[0]["category"] == "social"
    assert results[0]["service"] == "mastodon"


def test_multiple_links():
    html = """
    <a href="https://twitter.com/myhandle">Twitter</a>
    <a href="https://github.com/myorg">GitHub</a>
    <a href="https://partner.io/about">Partner</a>
    """
    results = extract_outgoing_links(html, "mysite.com")
    assert len(results) == 3
    by_url = {r["url"]: r for r in results}
    assert by_url["https://twitter.com/myhandle"]["category"] == "social"
    assert by_url["https://github.com/myorg"]["category"] == "social"
    assert by_url["https://partner.io/about"]["category"] == "other"


def test_dedup_same_url():
    html = """
    <a href="https://twitter.com/user">First</a>
    <a href="https://twitter.com/user">Duplicate</a>
    """
    results = extract_outgoing_links(html, "mysite.com")
    urls = [r["url"] for r in results if r["url"] == "https://twitter.com/user"]
    assert len(urls) == 1


# ---------------------------------------------------------------------------
# False-positive tests
# ---------------------------------------------------------------------------

def test_skip_internal_link():
    html = '<a href="https://mysite.com/about">About</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert results == []


def test_skip_subdomain():
    html = '<a href="https://blog.example.com/post">Blog</a>'
    results = extract_outgoing_links(html, "example.com")
    assert results == []


def test_skip_relative_link():
    html = '<a href="/about">About</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert results == []


def test_skip_wayback():
    html = '<a href="https://web.archive.org/web/20200101/https://example.com">Archived</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert results == []


def test_skip_javascript():
    html = '<a href="javascript:void(0)">Click</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert results == []


def test_skip_mailto():
    html = '<a href="mailto:contact@example.com">Email</a>'
    results = extract_outgoing_links(html, "mysite.com")
    assert results == []
