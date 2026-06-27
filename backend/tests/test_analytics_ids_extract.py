"""Tests for the analytics_ids extractor."""
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
    return extract_all(pages, "example.com")["analytics_ids"]


def _platforms(items: list[dict]) -> set[str]:
    return {it["platform"] for it in items}


def _ids(items: list[dict], platform: str) -> set[str]:
    return {it["id_value"] for it in items if it["platform"] == platform}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_ga4_measurement_id():
    html = (
        '<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABCDE12345"></script>'
    )
    items = _run(html)
    assert "G-ABCDE12345" in _ids(items, "ga4")


def test_detects_universal_analytics():
    html = '<script>ga("create", "UA-12345678-1", "auto");</script>'
    items = _run(html)
    assert "UA-12345678-1" in _ids(items, "ua")


def test_detects_gtm_container():
    html = '<!-- Google Tag Manager --> GTM-ABC1234 <!-- end -->'
    items = _run(html)
    assert "GTM-ABC1234" in _ids(items, "gtm")


def test_detects_hotjar_from_url():
    html = '<script src="https://static.hotjar.com/c/hotjar-1234567.js"></script>'
    items = _run(html)
    assert "1234567" in _ids(items, "hotjar")


def test_detects_matomo_site_id():
    html = '<script>_paq.push(["setSiteId", "42"]); setSiteId( "42" )</script>'
    items = _run(html)
    assert "42" in _ids(items, "matomo")


def test_detects_plausible_script():
    html = (
        '<script defer data-domain="example.com" '
        'src="https://plausible.io/js/plausible.js"></script>'
    )
    items = _run(html)
    assert "example.com" in _ids(items, "plausible")


def test_detects_fathom_site_code():
    html = '<script src="https://cdn.usefathom.com/script.js" data-site="ABCDEFGH" defer></script>'
    items = _run(html)
    assert "ABCDEFGH" in _ids(items, "fathom")


def test_detects_yandex_metrica():
    html = "<script>ym(12345678, 'init', {});</script>"
    items = _run(html)
    assert "12345678" in _ids(items, "yandex_metrica")


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_ignores_empty_page():
    assert _run("<html><body>hello</body></html>") == []


def test_rejects_placeholder_gtm():
    html = "<!-- docs example: GTM-XXXXXX -->"
    items = _run(html)
    assert "gtm" not in _platforms(items)


def test_plausible_without_data_domain_not_emitted():
    html = '<script src="https://plausible.io/js/plausible.js"></script>'
    items = _run(html)
    assert "plausible" not in _platforms(items)


def test_fathom_rejects_lowercase():
    html = '<script data-site="abcdefgh"></script>'
    items = _run(html)
    assert "fathom" not in _platforms(items)


def test_ga4_short_id_rejected():
    # GA4 needs exactly 10 alphanumerics after "G-".
    html = "text G-SHORT here"
    items = _run(html)
    assert "ga4" not in _platforms(items)


def test_no_false_match_on_generic_hex():
    html = "<p>commit abc123def456 ...</p>"
    items = _run(html)
    assert items == []
