"""Tests for the Google Adsense ID extractor."""
from __future__ import annotations

import pytest

from services.extractor.adsense_extract import extract_adsense_ids


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------


def test_pub_id_in_script_tag():
    """ca-pub inside a <script> block is extracted."""
    html = """<script>
    (adsbygoogle = window.adsbygoogle || []).push({});
    </script>
    <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1234567890"
            crossorigin="anonymous"></script>"""
    results = extract_adsense_ids(html)
    assert len(results) == 1
    assert results[0] == {"type": "adsense_publisher", "id": "ca-pub-1234567890"}


def test_pub_id_in_data_ad_client_attribute():
    """ca-pub inside a data-ad-client attribute is extracted."""
    html = '<ins class="adsbygoogle" data-ad-client="ca-pub-9876543210" data-ad-slot="1122334455"></ins>'
    results = extract_adsense_ids(html)
    pub_ids = [r for r in results if r["type"] == "adsense_publisher"]
    assert len(pub_ids) == 1
    assert pub_ids[0]["id"] == "ca-pub-9876543210"


def test_admob_app_publisher_extracted():
    """AdMob ca-app-pub- ids are captured with their full prefix."""
    html = '<meta name="google-admob-app-id" content="ca-app-pub-1234567890123456~9876543210">'
    results = extract_adsense_ids(html)
    admob = [r for r in results if r["type"] == "admob"]
    assert len(admob) == 1
    assert admob[0]["id"] == "ca-app-pub-1234567890123456"


def test_data_ad_slot_extracted():
    """data-ad-slot attribute value is captured as an ad_slot entry."""
    html = '<ins class="adsbygoogle" data-ad-slot="5544332211"></ins>'
    results = extract_adsense_ids(html)
    slot_ids = [r for r in results if r["type"] == "ad_slot"]
    assert len(slot_ids) == 1
    assert slot_ids[0]["id"] == "5544332211"


def test_multiple_ids_pub_and_slot():
    """Both publisher ID and ad-slot ID are returned from the same snippet."""
    html = (
        '<ins data-ad-client="ca-pub-1111111111" data-ad-slot="2222222222"></ins>'
    )
    results = extract_adsense_ids(html)
    types = {r["type"] for r in results}
    assert "adsense_publisher" in types
    assert "ad_slot" in types
    assert len(results) == 2


def test_dedup_same_pub_id_twice():
    """The same publisher ID appearing twice yields only one entry."""
    html = (
        "ca-pub-1234567890 ... ca-pub-1234567890"
    )
    results = extract_adsense_ids(html)
    pub_ids = [r for r in results if r["type"] == "adsense_publisher"]
    assert len(pub_ids) == 1


# ---------------------------------------------------------------------------
# False-positive / negative tests
# ---------------------------------------------------------------------------


def test_skip_short_pub_number():
    """ca-pub with fewer than 10 digits must not be matched."""
    html = "ca-pub-12345"  # only 5 digits
    results = extract_adsense_ids(html)
    assert results == []


def test_skip_non_numeric_data_slot():
    """A data-slot attribute with a non-numeric value is not matched."""
    html = '<div data-slot="carousel-1"></div>'
    results = extract_adsense_ids(html)
    # data-slot (not data-ad-slot) should never match; value is also non-numeric
    assert results == []


def test_no_matches_in_plain_text():
    """Ordinary prose without any Adsense tokens returns an empty list."""
    html = "<p>This website does not use Google advertising services.</p>"
    results = extract_adsense_ids(html)
    assert results == []
