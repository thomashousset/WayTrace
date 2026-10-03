"""Tests for the support_chat extractor (chat/helpdesk widgets + tenant ids)."""
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
    return extract_all(pages, "example.com")["support_chat"]


def _find(items: list[dict], vendor: str) -> dict | None:
    for it in items:
        if it["vendor"] == vendor:
            return it
    return None


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------

def test_intercom_app_id():
    html = '<script>window.intercomSettings={app_id:"a1b2c3d4"};</script>' \
           '<script src="https://widget.intercom.io/widget/a1b2c3d4"></script>'
    entry = _find(_run(html), "intercom")
    assert entry is not None
    assert entry["tenant_id"] == "a1b2c3d4"
    assert "a1b2c3d4" in entry["pivot_url"]


def test_crisp_website_id_uuid():
    html = '<script>window.CRISP_WEBSITE_ID="8c2f1e40-1a2b-4c3d-9e8f-abcdef012345";' \
           'd=document;s=d.createElement("script");s.src="https://client.crisp.chat/l.js";</script>'
    entry = _find(_run(html), "crisp")
    assert entry is not None
    assert entry["tenant_id"] == "8c2f1e40-1a2b-4c3d-9e8f-abcdef012345"


def test_tawk_to_property_id():
    html = '<script src="https://embed.tawk.to/5f9a1b2c3d4e5f6a7b8c9d0e/default"></script>'
    entry = _find(_run(html), "tawk_to")
    assert entry is not None
    assert entry["tenant_id"] == "5f9a1b2c3d4e5f6a7b8c9d0e"


def test_drift_org_id():
    html = '<script>drift.load("abc123def456");</script>' \
           '<script src="https://js.driftt.com/include/x/abc123def456.js"></script>'
    entry = _find(_run(html), "drift")
    assert entry is not None
    assert entry["tenant_id"] == "abc123def456"


def test_zendesk_subdomain_tenant():
    html = '<a href="https://acmehelp.zendesk.com/hc/en-us">Help center</a>'
    entry = _find(_run(html), "zendesk")
    assert entry is not None
    assert entry["tenant_id"] == "acmehelp"
    assert entry["pivot_url"] == "https://acmehelp.zendesk.com/"


def test_livechat_license():
    html = '<script>window.__lc={license:12345678};</script>' \
           '<script src="https://cdn.livechatinc.com/tracking.js"></script>'
    entry = _find(_run(html), "livechat")
    assert entry is not None
    assert entry["tenant_id"] == "12345678"


def test_tidio_key():
    html = '<script src="https://code.tidio.co/abcd1234efgh.js"></script>'
    entry = _find(_run(html), "tidio")
    assert entry is not None
    assert entry["tenant_id"] == "abcd1234efgh"


def test_smartsupp_key():
    html = '<script>var _smartsupp={};_smartsupp.key="a1b2c3d4e5f6";</script>' \
           '<script src="https://www.smartsuppchat.com/loader.js"></script>'
    entry = _find(_run(html), "smartsupp")
    assert entry is not None
    assert entry["tenant_id"] == "a1b2c3d4e5f6"


def test_detected_without_id_still_recorded():
    # Intercom present via CDN host but no app_id readable: still recorded.
    html = '<script src="https://js.intercomcdn.com/frame-modern.js"></script>'
    entry = _find(_run(html), "intercom")
    assert entry is not None
    assert entry["tenant_id"] == ""
    assert entry["pivot_url"] == "https://www.intercom.com/"


# ---------------------------------------------------------------------------
# Negative / false-positive guards
# ---------------------------------------------------------------------------

def test_bare_app_id_without_vendor_is_ignored():
    html = '<script>var app_id="deadbeef";</script>'
    assert _run(html) == []


def test_bare_license_word_is_ignored():
    html = '<p>This project is released under the MIT license: 12345678 downloads.</p>'
    assert _run(html) == []


def test_zendesk_vendor_infra_subdomain_not_a_tenant():
    html = '<script src="https://static.zdassets.com/ekr/snippet.js"></script>'
    entry = _find(_run(html), "zendesk")
    assert entry is not None
    # static.zdassets.com is vendor infra, not a tenant subdomain.
    assert entry["tenant_id"] == ""


def test_plain_prose_mentioning_chat_ignored():
    html = '<p>We added a live chat and a support desk last quarter.</p>'
    assert _run(html) == []


def test_unrelated_uuid_not_taken_as_crisp():
    html = '<div data-session="8c2f1e40-1a2b-4c3d-9e8f-abcdef012345">x</div>'
    assert _run(html) == []


def test_word_drift_in_content_ignored():
    html = '<h1>Continental drift and plate tectonics</h1>' \
           '<p>The drift load of sediment increased.</p>'
    assert _run(html) == []


def test_tawk_to_wrong_length_id_ignored():
    # 10-hex, not the required 24: no match.
    html = '<script src="https://embed.tawk.to/deadbeef01/default"></script>'
    assert _run(html) == []
