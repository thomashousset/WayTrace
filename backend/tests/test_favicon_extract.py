"""Tests for the favicon URL extractor."""
from __future__ import annotations

import pytest

from services.extractor.favicon_extract import extract_favicons


def test_link_rel_icon():
    """<link rel="icon"> href is extracted as a favicon entry."""
    html = '<link rel="icon" href="/favicon.ico">'
    results = extract_favicons(html)
    assert len(results) == 1
    assert results[0]["url"] == "/favicon.ico"
    assert results[0]["type"] == "favicon"


def test_link_rel_shortcut_icon():
    """<link rel="shortcut icon"> href is extracted."""
    html = '<link rel="shortcut icon" href="/images/favicon.png">'
    results = extract_favicons(html)
    assert len(results) == 1
    assert results[0]["url"] == "/images/favicon.png"
    assert results[0]["type"] == "favicon"


def test_apple_touch_icon():
    """<link rel="apple-touch-icon"> is typed as apple-touch-icon."""
    html = '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
    results = extract_favicons(html)
    assert len(results) == 1
    assert results[0]["type"] == "apple-touch-icon"


def test_icon_with_sizes():
    """sizes attribute is captured when present."""
    html = '<link rel="icon" href="/favicon-32.png" sizes="32x32">'
    results = extract_favicons(html)
    assert len(results) == 1
    assert results[0]["sizes"] == "32x32"


def test_multiple_favicons():
    """Three different icon link tags each produce one entry."""
    html = (
        '<link rel="icon" href="/favicon.ico">'
        '<link rel="apple-touch-icon" href="/apple.png">'
        '<link rel="shortcut icon" href="/shortcut.png">'
    )
    results = extract_favicons(html)
    assert len(results) == 3


def test_absolute_url():
    """An absolute URL href is returned unchanged."""
    html = '<link rel="icon" href="https://cdn.example.com/favicon.ico">'
    results = extract_favicons(html)
    assert len(results) == 1
    assert results[0]["url"] == "https://cdn.example.com/favicon.ico"


def test_dedup_same_href():
    """The same URL appearing under different rel values yields only one entry."""
    html = (
        '<link rel="icon" href="/favicon.ico">'
        '<link rel="shortcut icon" href="/favicon.ico">'
    )
    results = extract_favicons(html)
    assert len(results) == 1
    assert results[0]["url"] == "/favicon.ico"


def test_skip_stylesheet():
    """<link rel="stylesheet"> is ignored."""
    html = '<link rel="stylesheet" href="/style.css">'
    results = extract_favicons(html)
    assert results == []


def test_skip_empty_href():
    """A link tag with an empty href string is skipped."""
    html = '<link rel="icon" href="">'
    results = extract_favicons(html)
    assert results == []


def test_skip_no_href():
    """A link tag with no href attribute at all is skipped."""
    html = '<link rel="icon">'
    results = extract_favicons(html)
    assert results == []


def test_skip_wayback_favicon():
    """Wayback internal artifacts under /_static/ are skipped."""
    html = '<link rel="icon" href="/_static/images/archive.ico">'
    results = extract_favicons(html)
    assert results == []
