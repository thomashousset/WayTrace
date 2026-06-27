"""Tests for the analytics_trackers extractor."""
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
    return extract_all(pages, "example.com")["analytics_trackers"]


def _pairs(items: list[dict]) -> set[tuple[str, str]]:
    return {(it["type"], it["id"]) for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_ga_universal():
    html = "<script>ga('create', 'UA-12345678-1', 'auto');</script>"
    items = _run(html)
    assert ("GA_Universal", "UA-12345678-1") in _pairs(items)


def test_detects_ga4_measurement_id():
    html = '<script>gtag("config", "G-ABCDE12345");</script>'
    items = _run(html)
    assert ("GA4", "G-ABCDE12345") in _pairs(items)


def test_detects_gtm_container():
    html = (
        "<script>(function(w,d,s,l,i){})"
        "(window,document,'script','dataLayer','GTM-ABCD123');</script>"
    )
    items = _run(html)
    assert ("GTM", "GTM-ABCD123") in _pairs(items)


def test_detects_google_ads_conversion_id():
    html = '<script>gtag("config", "AW-123456789");</script>'
    items = _run(html)
    assert ("Google_Ads", "AW-123456789") in _pairs(items)


def test_detects_meta_pixel():
    html = "<script>fbq('init', '123456789012345');</script>"
    items = _run(html)
    assert ("Meta_Pixel", "123456789012345") in _pairs(items)


def test_detects_hotjar_site_id():
    html = "<script>h._hjSettings={hjid:1234567,hjsv:6};</script>"
    items = _run(html)
    assert ("Hotjar", "1234567") in _pairs(items)


def test_detects_mixpanel_token():
    html = '<script>mixpanel.init("0123456789abcdef0123456789abcdef");</script>'
    items = _run(html)
    assert ("Mixpanel", "0123456789abcdef0123456789abcdef") in _pairs(items)


def test_detects_yandex_metrica_counter():
    html = '<script>ym(12345678, "init", {clickmap:true});</script>'
    items = _run(html)
    assert ("Yandex_Metrica", "12345678") in _pairs(items)


def test_carries_temporal_metadata():
    html = "<script>ga('create', 'UA-9876543-2', 'auto');</script>"
    entry = next(it for it in _run(html) if it["type"] == "GA_Universal")
    assert entry["first_seen"] == "2022-06"
    assert entry["last_seen"] == "2022-06"
    assert entry["occurrences"] == 1


# ---------------------------------------------------------------------------
# False positives
# ---------------------------------------------------------------------------


def test_ignores_plain_uuid():
    html = "<p>session id 550e8400-e29b-41d4-a716-446655440000</p>"
    assert _pairs(_run(html)) == set()


def test_ignores_ua_flight_number_without_suffix():
    # GA_Universal requires the trailing "-N" property index; a bare
    # "UA-12345678" airline code must not match.
    html = "<p>Flight UA-12345678 was rescheduled.</p>"
    assert not any(it["type"] == "GA_Universal" for it in _run(html))


def test_ignores_lowercase_g_prefix():
    # GA4 pattern is anchored on an uppercase "G-".
    html = "<p>the token g-abcde12345 is unrelated</p>"
    assert not any(it["type"] == "GA4" for it in _run(html))


def test_ignores_ga4_id_too_short():
    # GA4 measurement IDs are exactly 10 base36 chars after "G-".
    html = '<script>gtag("config", "G-ABCDE123");</script>'
    assert not any(it["type"] == "GA4" for it in _run(html))


def test_ignores_gtm_code_too_short():
    # GTM container IDs need 5-8 chars after the "GTM-" prefix.
    html = "<p>GTM-AB shorthand label</p>"
    assert not any(it["type"] == "GTM" for it in _run(html))


def test_ignores_long_number_without_fbq_context():
    # A 15-digit order number is only a Meta Pixel inside an fbq(...) call.
    html = "<p>Order number 123456789012345 confirmed.</p>"
    assert not any(it["type"] == "Meta_Pixel" for it in _run(html))


def test_ignores_bare_number_without_hotjar_context():
    html = "<p>Reference value 1234567 in the ledger.</p>"
    assert not any(it["type"] == "Hotjar" for it in _run(html))


def test_ignores_hex_string_without_mixpanel_context():
    # A loose 32-char hex (md5-like) is not a Mixpanel token without init().
    html = "<p>checksum 0123456789abcdef0123456789abcdef</p>"
    assert not any(it["type"] == "Mixpanel" for it in _run(html))


def test_ignores_number_without_ym_context():
    html = "<p>The counter reads 12345678 today.</p>"
    assert not any(it["type"] == "Yandex_Metrica" for it in _run(html))
