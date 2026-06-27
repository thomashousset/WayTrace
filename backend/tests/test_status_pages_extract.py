"""Tests for the status_pages extractor."""
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
    return extract_all(pages, "example.com")["status_pages"]


def _providers(items: list[dict]) -> set[str]:
    return {it["provider"] for it in items}


def _find(items: list[dict], provider: str, slug: str) -> dict | None:
    for it in items:
        if it["provider"] == provider and it["slug"] == slug:
            return it
    return None


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_statuspage_io_in_href():
    html = '<a href="https://acme.statuspage.io/">status</a>'
    items = _run(html)
    entry = _find(items, "statuspage.io", "acme")
    assert entry is not None
    assert entry["pivot_url"] == "https://acme.statuspage.io/"


def test_detects_instatus_com():
    html = '<a href="https://foocorp.instatus.com">status</a>'
    items = _run(html)
    entry = _find(items, "instatus.com", "foocorp")
    assert entry is not None
    assert entry["pivot_url"] == "https://foocorp.instatus.com/"


def test_detects_betterstack_betteruptime():
    html = "Live status at https://widgets.betteruptime.com/ now."
    items = _run(html)
    entry = _find(items, "betterstack", "widgets")
    assert entry is not None
    # Pivot normalises betteruptime tenants to the betteruptime host.
    assert entry["pivot_url"] == "https://widgets.betteruptime.com/"


def test_detects_freshstatus_io():
    html = '<a href="https://helpdesk.freshstatus.io/">status</a>'
    items = _run(html)
    entry = _find(items, "freshstatus", "helpdesk")
    assert entry is not None
    assert entry["pivot_url"] == "https://helpdesk.freshstatus.io/"


def test_detects_statushub_io():
    html = '<a href="https://mycompany.statushub.io/">status</a>'
    items = _run(html)
    entry = _find(items, "statushub", "mycompany")
    assert entry is not None
    assert entry["pivot_url"] == "https://mycompany.statushub.io/"


def test_detects_custom_domain_status_subdomain():
    html = '<a href="https://status.acme.org/">our status page</a>'
    items = _run(html)
    entry = _find(items, "custom-domain", "status.acme.org")
    assert entry is not None
    assert entry["pivot_url"] == "https://status.acme.org/"


def test_detects_custom_domain_uptime_subdomain():
    html = '<a href="https://uptime.foocorp.io/">uptime</a>'
    items = _run(html)
    assert _find(items, "custom-domain", "uptime.foocorp.io") is not None


def test_detects_statuspage_with_trailing_path():
    # The host is what matters; a trailing incidents path is ignored.
    html = '<a href="https://acme.statuspage.io/incidents/abc">incident</a>'
    items = _run(html)
    assert _find(items, "statuspage.io", "acme") is not None


def test_does_not_attribute_arbitrary_status_com_to_betterstack():
    # status.com is an unrelated domain; Better Stack pages live on
    # betteruptime.com / status.betterstack.com, not *.status.com.
    html = '<a href="https://news.status.com/">news</a>'
    items = _run(html)
    assert all(it["provider"] != "betterstack" for it in items)


def test_detects_multiple_providers_in_one_page():
    html = (
        '<a href="https://alpha.statuspage.io/">a</a>'
        '<a href="https://bravo.instatus.com/">b</a>'
    )
    items = _run(html)
    assert _find(items, "statuspage.io", "alpha") is not None
    assert _find(items, "instatus.com", "bravo") is not None


# ---------------------------------------------------------------------------
# False positives / negatives
# ---------------------------------------------------------------------------


def test_ignores_statuspage_marketing_homepage():
    # The provider's own marketing site (www) must not be flagged as a tenant.
    html = '<a href="https://www.statuspage.io/">StatusPage product</a>'
    items = _run(html)
    assert _find(items, "statuspage.io", "www") is None
    assert items == []


def test_ignores_instatus_marketing_homepage():
    html = '<a href="https://www.instatus.com/">Instatus product</a>'
    assert _run(html) == []


def test_ignores_betterstack_marketing_homepage():
    # betterstack.com is the marketing apex, not a <tenant>.betteruptime.com host.
    html = '<a href="https://betterstack.com/">Better Stack</a>'
    assert _run(html) == []


def test_ignores_generic_status_path_on_normal_site():
    # A /status path on a regular site is not a hosted status page.
    html = '<a href="https://example.com/status">System status</a>'
    assert _run(html) == []


def test_ignores_generic_health_path():
    html = '<a href="https://example.com/health">health check</a>'
    assert _run(html) == []


def test_ignores_reserved_provider_subdomains():
    # docs/help/blog/status subdomains of providers are reserved, not tenants.
    html = (
        '<a href="https://docs.instatus.com/">docs</a>'
        '<a href="https://help.statushub.io/">help</a>'
    )
    items = _run(html)
    assert _providers(items) == set()


def test_ignores_unrelated_urls():
    html = '<a href="https://github.com/acme">repo</a> <a href="https://acme.com/">home</a>'
    assert _run(html) == []


def test_ignores_single_char_slug():
    # Slugs shorter than 2 chars are dropped by the extractor's length guard.
    html = '<a href="https://a.statuspage.io/">x</a>'
    assert _run(html) == []
