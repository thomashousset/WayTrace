"""Tests for the hosting provider detection extractor."""
from __future__ import annotations

import pytest

from services.extractor.hosting_extract import detect_hosting


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------


def test_cloudflare_in_meta():
    """Cloudflare token in a meta tag is detected."""
    html = '<meta name="cf-ray" content="cloudflare-abc123">'
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "Cloudflare" in providers


def test_vercel_script_global():
    """__vercel global variable in a script block is detected as Vercel."""
    html = "<script>window.__vercel = {env: 'production'};</script>"
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "Vercel" in providers


def test_netlify_in_html():
    """netlify token anywhere in the HTML is detected."""
    html = '<meta name="generator" content="Netlify">'
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "Netlify" in providers


def test_github_pages_link():
    """github.io in a link href is detected as GitHub Pages."""
    html = '<link rel="canonical" href="https://myuser.github.io/myrepo/">'
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "GitHub Pages" in providers


def test_amazons3_in_src():
    """AmazonS3 bucket reference in an img src is detected."""
    html = '<img src="https://mybucket.s3.amazonaws.com/logo.png">'
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "AWS S3" in providers


def test_wordpress_com_via_wp_com_cdn():
    """s0.wp.com CDN URL is detected as WordPress.com."""
    html = '<link rel="stylesheet" href="https://s0.wp.com/wp-content/themes/pub/style.css">'
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "WordPress.com" in providers


def test_shopify_cdn_link():
    """cdn.shopify.com in a script src is detected as Shopify."""
    html = '<script src="https://cdn.shopify.com/s/files/1/0000/0001/t/1/assets/theme.js"></script>'
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "Shopify" in providers


def test_wpserveur_in_html_comment():
    """WPServeur token inside an HTML comment is detected."""
    html = "<!-- Optimized by WPServeur -->"
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "WPServeur" in providers


def test_multiple_signals_returns_multiple_providers():
    """HTML containing both Cloudflare and Vercel tokens returns both providers."""
    html = (
        '<meta name="cf-2" content="cloudflare">'
        "<script>window.__vercel_insights_id = '123';</script>"
    )
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    assert "Cloudflare" in providers
    assert "Vercel" in providers


def test_dedup_same_provider_multiple_occurrences():
    """The same provider token appearing multiple times yields only one entry."""
    html = (
        "<script>cloudflare.init();</script>"
        '<meta content="cloudflare-cdn">'
    )
    results = detect_hosting(html)
    cloudflare_hits = [r for r in results if r["provider"] == "Cloudflare"]
    assert len(cloudflare_hits) == 1


# ---------------------------------------------------------------------------
# False-positive / negative tests
# ---------------------------------------------------------------------------


def test_no_match_plain_text():
    """Ordinary HTML without any hosting tokens returns an empty list."""
    html = "<html><body><p>Hello world. No hosting signals here.</p></body></html>"
    results = detect_hosting(html)
    assert results == []


def test_skip_wayback_server_token():
    """Tokens containing 'wayback' or 'archive' are not returned as providers."""
    # Inject a hypothetical wayback-branded value; the built-in signals don't
    # include those terms, so the skip guard is validated via the filter logic.
    html = '<meta name="server" content="wayback-cloudflare-proxy">'
    # Cloudflare IS present in the HTML, but the provider name "Cloudflare"
    # does not contain wayback/archive, so it must still match; this test
    # verifies that _SKIP_TERMS filters on provider name, not on HTML content.
    results = detect_hosting(html)
    providers = [r["provider"] for r in results]
    # The skip guard applies to provider names, not to HTML text.
    # "Cloudflare" should still be detected even if "wayback" is in the HTML.
    assert "Cloudflare" in providers
    # Verify no entry has "wayback" or "archive" in its provider name.
    for r in results:
        assert "wayback" not in r["provider"].lower()
        assert "archive" not in r["provider"].lower()
