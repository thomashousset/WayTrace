"""Tests for the domain verification meta tag extractor."""
from __future__ import annotations

import pytest

from services.extractor.verification_extract import extract_verification_tags


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------


def test_google_site_verification():
    """Google site verification meta tag is extracted correctly."""
    html = '<meta name="google-site-verification" content="abc123defgh456ijklm">'
    results = extract_verification_tags(html)
    assert len(results) == 1
    assert results[0] == {"service": "google", "verification_id": "abc123defgh456ijklm"}


def test_bing_msvalidate():
    """Bing msvalidate.01 meta tag is extracted correctly."""
    html = '<meta name="msvalidate.01" content="AABBCCDDEEFF00112233">'
    results = extract_verification_tags(html)
    assert len(results) == 1
    assert results[0] == {"service": "bing", "verification_id": "AABBCCDDEEFF00112233"}


def test_facebook_domain_verification():
    """Facebook domain verification meta tag is extracted correctly."""
    html = '<meta name="facebook-domain-verification" content="fb1234567890abcdef">'
    results = extract_verification_tags(html)
    assert len(results) == 1
    assert results[0] == {"service": "facebook", "verification_id": "fb1234567890abcdef"}


def test_pinterest_domain_verify():
    """Pinterest p:domain_verify meta tag is extracted correctly."""
    html = '<meta name="p:domain_verify" content="pinterest_verify_token_abc">'
    results = extract_verification_tags(html)
    assert len(results) == 1
    assert results[0] == {"service": "pinterest", "verification_id": "pinterest_verify_token_abc"}


def test_yandex_verification():
    """Yandex verification meta tag is extracted correctly."""
    html = '<meta name="yandex-verification" content="yandex9876543210abcd">'
    results = extract_verification_tags(html)
    assert len(results) == 1
    assert results[0] == {"service": "yandex", "verification_id": "yandex9876543210abcd"}


def test_multiple_verifications_in_same_page():
    """Multiple verification tags from different services are all extracted."""
    html = """
    <html>
    <head>
        <meta name="google-site-verification" content="google_token_abc123xyz">
        <meta name="msvalidate.01" content="BING_TOKEN_ABC123XYZ456">
        <meta name="facebook-domain-verification" content="fb_verify_abc123xyz">
        <meta name="yandex-verification" content="yandex_verify_abc123xyz">
    </head>
    <body></body>
    </html>
    """
    results = extract_verification_tags(html)
    assert len(results) == 4
    services = {r["service"] for r in results}
    assert services == {"google", "bing", "facebook", "yandex"}


# ---------------------------------------------------------------------------
# False positive / negative tests
# ---------------------------------------------------------------------------


def test_skip_placeholder_xxxxxxxx():
    """Content matching XXXXX placeholder pattern is skipped."""
    html = '<meta name="google-site-verification" content="XXXXXXXXXXXXXXXXXX">'
    results = extract_verification_tags(html)
    assert results == []


def test_skip_empty_content():
    """Meta tag with empty content attribute is skipped."""
    html = '<meta name="google-site-verification" content="">'
    results = extract_verification_tags(html)
    assert results == []


def test_skip_short_content():
    """Content shorter than 6 characters is skipped."""
    html = '<meta name="msvalidate.01" content="abc">'
    results = extract_verification_tags(html)
    assert results == []


def test_skip_your_code_here_placeholder():
    """Content starting with 'your-' placeholder pattern is skipped."""
    html = '<meta name="google-site-verification" content="your-verification-code-here">'
    results = extract_verification_tags(html)
    assert results == []


def test_skip_unrelated_meta_tags():
    """Unrelated meta tags like description and keywords are not extracted."""
    html = """
    <html>
    <head>
        <meta name="description" content="A wonderful website about cats and dogs.">
        <meta name="keywords" content="cats, dogs, animals, pets">
        <meta name="author" content="Jane Doe, Web Developer">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body></body>
    </html>
    """
    results = extract_verification_tags(html)
    assert results == []
