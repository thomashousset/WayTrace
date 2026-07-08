"""Regression tests for the extractor QA pass (RETEX n2): each asserts a
confirmed false positive is gone AND a real positive still works, so tightening
a pattern never silently kills recall.
"""
from __future__ import annotations

from services.extractor import extract_all
from services.extractor.js_urls_extract import extract_js_urls
from services.extractor.connstring_extract import extract_connection_strings


def _run(html, domain="example.com"):
    pages = [{"html": html, "url": f"http://{domain}/", "timestamp": "20230615120000"}]
    return extract_all(pages, domain)


def _social(html):
    return {(s["platform"], s["handle"]) for s in _run(html)["social_profiles"]}


def test_social_lookalike_domains_rejected():
    r = _social('<a href="https://notlinkedin.com/in/foo">x</a>'
                '<a href="https://mytwitter.com/fakeuser">y</a>'
                '<a href="https://evilgithub.com/org">z</a>')
    assert not any(p in ("linkedin", "twitter", "github") for p, _ in r)


def test_social_real_domains_still_match():
    r = _social('<a href="https://www.linkedin.com/in/jane-doe">j</a>'
                '<a href="https://twitter.com/realhandle">t</a>')
    assert ("linkedin", "jane-doe") in r
    assert ("twitter", "realhandle") in r


def test_telegram_joinchat_not_a_handle():
    r = _social('<a href="https://t.me/joinchat/AAAAAEabcdef">invite</a>')
    assert not any(p == "telegram" and h == "joinchat" for p, h in r)


def test_analytics_tracker_placeholders_denied():
    trackers = {(t["type"], t["id"]) for t in _run(
        '<!-- docs: put GTM-XXXXXX and G-XXXXXXXXXX here -->')["analytics_trackers"]}
    assert ("GTM", "GTM-XXXXXX") not in trackers
    assert ("GA4", "G-XXXXXXXXXX") not in trackers


def test_analytics_tracker_real_ids_still_found():
    trackers = {t["id"] for t in _run(
        '<script>gtag("config","G-ABCDE12345")</script>'
        '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC1234"></script>'
    )["analytics_trackers"]}
    assert "G-ABCDE12345" in trackers
    assert "GTM-ABC1234" in trackers


def test_hosting_prose_mentions_not_detected():
    providers = {h["provider"] for h in _run(
        "<p>We migrated away from Shopify last year. Our team loves Vercel.</p>"
    )["hosting"]}
    assert "Shopify" not in providers
    assert "Vercel" not in providers


def test_hosting_real_signals_detected():
    providers = {h["provider"] for h in _run(
        '<link href="https://cdn.shopify.com/s/files/1/x/theme.css">'
    )["hosting"]}
    assert "Shopify" in providers


def test_technologies_substring_libs_not_matched():
    techs = {t["technology"] for t in _run(
        '<script src="/assets/revue.js"></script>'
        '<script src="/js/grid3.js"></script>'
    )["technologies"]}
    assert "Vue.js" not in techs
    assert "D3.js" not in techs


def test_technologies_real_libs_matched():
    techs = {t["technology"] for t in _run(
        '<script src="/assets/vue.min.js"></script>'
        '<script src="/js/d3.js"></script>'
    )["technologies"]}
    assert "Vue.js" in techs
    assert "D3.js" in techs


def test_endpoints_date_format_strings_ignored():
    paths = {e["path"] for e in _run(
        '<script>var fmt="/YYYY/MM/DD"; moment().format("/DD/MM/YYYY");</script>'
    )["endpoints"]}
    assert "/yyyy/mm/dd" not in paths and "/YYYY/MM/DD" not in paths


def test_persons_byline_connector_stripped():
    names = {p["name"] for p in _run('<span class="author">By John Smith</span>')["persons"]}
    assert "John Smith" in names
    assert "By John Smith" not in names


def test_persons_tool_name_rejected():
    names = {p["name"].lower() for p in _run('<meta name="author" content="Yoast SEO">')["persons"]}
    assert "yoast seo" not in names


def test_js_urls_template_literal_truncated():
    urls = {d["url"] for d in extract_js_urls(
        '<script>const u=`https://api.example.com/users/${id}/profile`;</script>')}
    assert any(u.rstrip("/") == "https://api.example.com/users" for u in urls)
    assert not any("${" in u or "{" in u for u in urls)


def test_connstring_glued_scheme_not_matched():
    assert extract_connection_strings("custommysql://root:pw@h/db") == []


def test_connstring_real_uri_matched():
    got = extract_connection_strings("mongodb://user:pw@db.host:27017/app")
    assert any("mongodb://" in c.get("value", "") for c in got)


# XMR-shaped string: '4' + [0-9AB] + 93 base58 chars = 95 chars (matches XMR_RE).
_XMR_SHAPED = "4A" + "B" * 93


def test_xmr_without_context_not_emitted():
    # A 95-char XMR-shaped base58 blob with no Monero context is noise.
    from services.extractor.crypto_extract import extract_crypto_addresses
    got = extract_crypto_addresses(f"<p>opaque token {_XMR_SHAPED} here</p>")
    assert not any(c.get("type") == "xmr" for c in got)


def test_xmr_with_context_still_emitted():
    from services.extractor.crypto_extract import extract_crypto_addresses
    got = extract_crypto_addresses(f"<p>Monero (XMR) donations: {_XMR_SHAPED}</p>")
    assert any(c.get("type") == "xmr" for c in got)
