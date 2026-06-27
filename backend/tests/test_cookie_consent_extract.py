"""Tests for the cookie_consent extractor."""
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
    return extract_all(pages, "example.com")["cookie_consent"]


def _platforms(items: list[dict]) -> set[str]:
    return {it["platform"] for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_iubenda_with_account_id():
    html = '<a href="https://www.iubenda.com/privacy-policy/12345678">privacy</a>'
    items = _run(html)
    assert "iubenda" in _platforms(items)
    match = next(it for it in items if it["platform"] == "iubenda")
    assert match["account_id"] == "12345678"


def test_detects_tarteaucitron():
    html = '<script src="/js/tarteaucitron.js"></script>'
    items = _run(html)
    assert "tarteaucitron" in _platforms(items)


def test_detects_onetrust():
    html = '<script src="https://cdn.cookielaw.org/otSDKStub.js"></script>'
    items = _run(html)
    assert "onetrust" in _platforms(items)


def test_detects_cookieyes():
    html = '<div class="cky-consent-container"></div>'
    items = _run(html)
    assert "cookieyes" in _platforms(items)


def test_detects_cookiebot_with_id():
    html = '<script data-cbid="abc-123-def" src="https://consent.cookiebot.com/x.js"></script>'
    items = _run(html)
    assert any(it["platform"] == "cookiebot" and it["account_id"] for it in items)


def test_detects_axeptio():
    html = '<script src="https://static.axept.io/sdk.js"></script>'
    items = _run(html)
    assert "axeptio" in _platforms(items)


def test_detects_usercentrics_with_settings_id():
    html = '<script id="usercentrics-cmp" src="https://app.usercentrics.eu/ui.js" data-settings-id="ABcd123"></script>'
    items = _run(html)
    assert any(it["platform"] == "usercentrics" and it["account_id"] == "ABcd123" for it in items)


def test_detects_didomi():
    html = '<script src="https://sdk.privacy-center.org/loader.js"></script>'
    items = _run(html)
    assert "didomi" in _platforms(items)


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_ignores_empty_page():
    assert _run("<html></html>") == []


def test_no_cmp_when_only_the_word_cookie():
    html = "<p>This site uses cookies.</p>"
    assert _run(html) == []


def test_no_false_positive_on_cookielaw_in_text():
    html = "<p>The cookie law in Europe requires disclosure.</p>"
    assert _run(html) == []


def test_no_false_positive_from_unrelated_cdn():
    html = '<script src="https://cdn.example.com/tracker.js"></script>'
    assert _run(html) == []


def test_tarteaucitron_text_mention_alone():
    # Passing mention without script or class triggers nothing.
    html = "<p>We use a consent tool.</p>"
    assert _run(html) == []
