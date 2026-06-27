"""Tests for disclosure-program, CAPTCHA and status-page detection.

* bug_bounty_programs (HackerOne / Bugcrowd / Intigriti / YesWeHack)
* captcha_providers (reCAPTCHA / Turnstile / hCaptcha)
* status_pages (Statuspage.io / Instatus / Better Stack / FreshStatus
  / StatusHub)
* api_keys: Sentry DSN + Mapbox public access token
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.bug_bounty_extract import extract_bug_bounty_programs
from services.extractor.captcha_providers_extract import extract_captcha_providers
from services.extractor.status_pages_extract import extract_status_pages
from services.extractor.patterns import (
    API_KEY_PATTERNS,
    MAPBOX_TOKEN_RE,
    SENTRY_DSN_RE,
)


# ---------------------------------------------------------------------------
# Bug bounty programs
# ---------------------------------------------------------------------------


def _bb(html: str) -> list[dict]:
    accum = {"bug_bounty_programs": {}}
    extract_bug_bounty_programs(html, "2024-06", accum)
    return list(accum["bug_bounty_programs"].values())


def test_bb_hackerone_handle_extracted():
    out = _bb('<a href="https://hackerone.com/anthropic">disclosure</a>')
    assert any(p["platform"] == "hackerone" and p["handle"] == "anthropic" for p in out)
    assert out[0]["pivot_url"] == "https://hackerone.com/anthropic"


def test_bb_hackerone_reserved_paths_rejected():
    out = _bb('<a href="https://hackerone.com/hacktivity">x</a>'
              '<a href="https://hackerone.com/sitemap">y</a>'
              '<a href="https://hackerone.com/login">z</a>')
    assert out == []


def test_bb_bugcrowd_handle_extracted():
    out = _bb('<a href="https://bugcrowd.com/stripe">disclosure</a>')
    assert any(p["platform"] == "bugcrowd" and p["handle"] == "stripe" for p in out)


def test_bb_intigriti_with_companies_prefix():
    out = _bb('<a href="https://intigriti.com/companies/intigriti">x</a>')
    assert any(p["platform"] == "intigriti" and p["handle"] == "intigriti" for p in out)


def test_bb_yeswehack_with_programs_prefix():
    out = _bb('<a href="https://yeswehack.com/programs/red-bull">x</a>')
    assert any(p["platform"] == "yeswehack" and p["handle"] == "red-bull" for p in out)


def test_bb_dedup_repeated_match():
    out = _bb('<a href="https://hackerone.com/anthropic">a</a>'
              '<a href="https://hackerone.com/anthropic">b</a>')
    assert len(out) == 1
    assert out[0]["occurrences"] == 2


def test_bb_intigriti_marketing_paths_rejected():
    # Bare intigriti.com/<word> footer/nav links are not programs.
    out = _bb('<a href="https://www.intigriti.com/blog">blog</a>'
              '<a href="https://www.intigriti.com/pricing">price</a>'
              '<a href="https://www.intigriti.com/contact">contact</a>')
    assert [p for p in out if p["platform"] == "intigriti"] == []


def test_bb_hackerone_security_and_current_user_rejected():
    out = _bb('<a href="https://hackerone.com/security">sec</a>'
              '<a href="https://hackerone.com/current_user">me</a>'
              '<a href="https://hackerone.com/bug_bounty">bb</a>')
    assert [p for p in out if p["platform"] == "hackerone"] == []


def test_bb_bugcrowd_programs_listing_rejected():
    out = _bb('<a href="https://bugcrowd.com/programs">all programs</a>')
    assert [p for p in out if p["platform"] == "bugcrowd"] == []


def test_bb_yeswehack_about_rejected():
    out = _bb('<a href="https://yeswehack.com/about-us">about</a>')
    assert [p for p in out if p["platform"] == "yeswehack"] == []


# ---------------------------------------------------------------------------
# Captcha providers
# ---------------------------------------------------------------------------


def _cap(html: str) -> list[dict]:
    accum = {"captcha_providers": {}}
    extract_captcha_providers(html, "2024-06", accum)
    return list(accum["captcha_providers"].values())


def test_recaptcha_sitekey_extracted():
    # Real reCAPTCHA keys are exactly 40 chars: "6L" prefix + 38 body.
    sk = "6L" + "a" * 38
    assert len(sk) == 40
    html = f'<div class="g-recaptcha" data-sitekey="{sk}"></div>'
    out = _cap(html)
    assert any(p["provider"] == "recaptcha" and p["sitekey"] == sk for p in out)


def test_turnstile_sitekey_extracted():
    sk = "0x4AAAAAAA" + "B" * 22
    html = f'<div data-sitekey="{sk}" class="cf-turnstile"></div>'
    out = _cap(html)
    assert any(p["provider"] == "turnstile" and p["sitekey"] == sk for p in out)


def test_hcaptcha_sitekey_with_keyword_window_extracted():
    uuid = "10000000-ffff-ffff-ffff-000000000001"
    html = f'<div class="h-captcha" data-sitekey="{uuid}"></div>'
    out = _cap(html)
    assert any(p["provider"] == "hcaptcha" and p["sitekey"] == uuid for p in out)


def test_hcaptcha_uuid_without_keyword_rejected():
    """Bare UUID without 'hcaptcha' nearby is not enough. UUIDs are
    everywhere and would massively over-match."""
    uuid = "10000000-ffff-ffff-ffff-000000000001"
    html = f'<p>Order id: {uuid}</p>'
    out = _cap(html)
    assert all(p["provider"] != "hcaptcha" for p in out)


def test_recaptcha_script_only_emits_provider_entry():
    """When only the script URL is visible (sitekey passed at runtime),
    we still emit a provider-only marker."""
    html = '<script src="https://www.google.com/recaptcha/api.js"></script>'
    out = _cap(html)
    assert any(p["provider"] == "recaptcha" and not p["sitekey"] for p in out)


def test_turnstile_script_only_emits_provider_entry():
    html = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
    out = _cap(html)
    assert any(p["provider"] == "turnstile" and not p["sitekey"] for p in out)


def test_hcaptcha_script_only_emits_provider_entry():
    html = '<script src="https://js.hcaptcha.com/1/api.js"></script>'
    out = _cap(html)
    assert any(p["provider"] == "hcaptcha" and not p["sitekey"] for p in out)


def test_provider_script_skipped_when_sitekey_already_seen():
    """If a sitekey is found, don't double-emit a provider-only marker."""
    sk = "6L" + "a" * 38
    html = (
        f'<div class="g-recaptcha" data-sitekey="{sk}"></div>'
        '<script src="https://www.google.com/recaptcha/api.js"></script>'
    )
    out = _cap(html)
    recaptcha_entries = [p for p in out if p["provider"] == "recaptcha"]
    # One entry. the keyed one, not the script-only fallback.
    assert len(recaptcha_entries) == 1
    assert recaptcha_entries[0]["sitekey"] == sk


# ---------------------------------------------------------------------------
# Additional captcha providers (Arkose/FunCaptcha, GeeTest, AWS WAF, Friendly)
# Each is its own distinct `provider` value. Precision over recall: bare
# tokens need provider context; script URLs are high-confidence on their own.
# ---------------------------------------------------------------------------


def test_arkose_public_key_from_script_url():
    pk = "476068BF-9607-4799-B53D-966BE98E2B81"
    html = f'<script src="https://client-api.arkoselabs.com/v2/{pk}/api.js"></script>'
    out = _cap(html)
    assert any(p["provider"] == "arkose" and p["sitekey"] == pk for p in out)


def test_funcaptcha_script_only_emits_provider():
    html = '<script src="https://funcaptcha.com/fc/api/"></script>'
    out = _cap(html)
    assert any(p["provider"] == "arkose" for p in out)


def test_geetest_id_with_context_extracted():
    gt = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"  # 32 hex
    html = f'<script src="https://static.geetest.com/static/js/gt.js"></script><script>initGeetest({{gt:"{gt}"}})</script>'
    out = _cap(html)
    assert any(p["provider"] == "geetest" and p["sitekey"] == gt for p in out)


def test_geetest_bare_hex_without_context_rejected():
    # 32-hex == an md5; never a GeeTest id without geetest context nearby.
    h = "5f4dcc3b5aa765d61d8327deb882cf99"
    html = f'<meta name="build" content="{h}">'
    out = _cap(html)
    assert all(p["provider"] != "geetest" for p in out)


def test_aws_waf_script_detected():
    html = '<script src="https://abc123.token.awswaf.com/abc123/challenge.js"></script>'
    out = _cap(html)
    assert any(p["provider"] == "aws_waf" for p in out)


def test_friendly_captcha_sitekey_with_context():
    sk = "FCMGD8ITLN5UB6F4"
    html = f'<div class="frc-captcha" data-sitekey="{sk}"></div><script src="https://cdn.jsdelivr.net/npm/friendly-challenge@0.9.12/widget.min.js"></script>'
    out = _cap(html)
    assert any(p["provider"] == "friendly_captcha" and p["sitekey"] == sk for p in out)


def test_friendly_captcha_script_only():
    html = '<script src="https://api.friendlycaptcha.com/api/v1/captcha.js"></script>'
    out = _cap(html)
    assert any(p["provider"] == "friendly_captcha" for p in out)


# ---------------------------------------------------------------------------
# Status pages
# ---------------------------------------------------------------------------


def _sp(html: str) -> list[dict]:
    accum = {"status_pages": {}}
    extract_status_pages(html, "2024-06", accum)
    return list(accum["status_pages"].values())


def test_statuspage_io_tenant_extracted():
    out = _sp('<a href="https://stripe-status.statuspage.io/">status</a>')
    assert any(p["provider"] == "statuspage.io" and p["slug"] == "stripe-status" for p in out)
    assert "statuspage.io" in out[0]["pivot_url"]


def test_instatus_tenant_extracted():
    out = _sp('<a href="https://anthropic.instatus.com/">status</a>')
    assert any(p["provider"] == "instatus.com" and p["slug"] == "anthropic" for p in out)


def test_betterstack_tenant_extracted():
    out = _sp('<a href="https://acme.betteruptime.com/">status</a>')
    assert any(p["provider"] == "betterstack" and p["slug"] == "acme" for p in out)


def test_status_pages_reserved_subdomain_skipped():
    """www / blog / docs aren't tenant slugs. they're the providers'
    own marketing subdomains."""
    out = _sp('<a href="https://www.statuspage.io/">marketing</a>'
              '<a href="https://docs.instatus.com/">docs</a>')
    assert out == []


def test_status_pages_custom_domain_detected():
    """status.stripe.com is a Statuspage.io tenant behind a CNAME. the
    standard `*.statuspage.io` regex misses it. The custom-domain
    fallback catches the host itself."""
    out = _sp('<a href="https://status.stripe.com/">Stripe Status</a>')
    assert any(p["provider"] == "custom-domain" and p["slug"] == "status.stripe.com" for p in out)
    custom = next(p for p in out if p["provider"] == "custom-domain")
    assert custom["pivot_url"] == "https://status.stripe.com/"


def test_status_pages_health_subdomain_detected():
    out = _sp('<a href="https://health.acme.com/">health</a>')
    assert any(p["slug"] == "health.acme.com" for p in out)


def test_status_pages_incidents_subdomain_detected():
    out = _sp('<a href="https://incidents.example.com/">incidents</a>')
    assert any(p["slug"] == "incidents.example.com" for p in out)


def test_status_pages_unrelated_subdomain_not_detected():
    """staging.<x> shouldn't match. it's not a status-page convention."""
    out = _sp('<a href="https://staging.example.com/">staging</a>')
    assert out == []


# ---------------------------------------------------------------------------
# Sentry DSN + Mapbox in api_keys
# ---------------------------------------------------------------------------


def test_sentry_dsn_pattern_matches():
    dsn = "https://" + "a" * 32 + "@o123456.ingest.sentry.io/789012"
    assert SENTRY_DSN_RE.search(dsn) is not None
    assert API_KEY_PATTERNS["Sentry_DSN"].search(dsn) is not None


def test_sentry_dsn_with_region_subdomain():
    dsn = "https://" + "f" * 32 + "@o555.ingest.us.sentry.io/4509"
    assert SENTRY_DSN_RE.search(dsn) is not None


def test_sentry_dsn_does_not_match_bare_url():
    assert SENTRY_DSN_RE.search("https://sentry.io/welcome/") is None


def test_mapbox_token_pattern_matches():
    tok = "pk.eyJabc123def456ghi789jkl012345.mnoPQRstuvwxyz0123456789"
    assert MAPBOX_TOKEN_RE.search(tok) is not None
    assert API_KEY_PATTERNS["Mapbox"].search(tok) is not None


def test_mapbox_does_not_match_stripe_pk():
    assert MAPBOX_TOKEN_RE.search("pk_live_" + "a" * 24) is None


# ---------------------------------------------------------------------------
# Tier classification. public-by-design vs secret
# ---------------------------------------------------------------------------


def test_sentry_dsn_tier_is_public():
    dsn = "https://" + "a" * 32 + "@o123456.ingest.sentry.io/789012"
    html = f'<script>Sentry.init({{ dsn: "{dsn}" }})</script>'
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20240101120000"}]
    res = extract_all(pages, "example.com")
    matches = [k for k in res["api_keys"] if k["type"] == "Sentry_DSN"]
    assert matches
    assert matches[0]["tier"] == "public"


def test_mapbox_tier_is_public():
    tok = "pk.eyJ" + "a" * 30 + "." + "b" * 30
    html = f'<script>mapboxgl.accessToken = "{tok}"</script>'
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20240101120000"}]
    res = extract_all(pages, "example.com")
    matches = [k for k in res["api_keys"] if k["type"] == "Mapbox"]
    assert matches
    assert matches[0]["tier"] == "public"


# ---------------------------------------------------------------------------
# End-to-end: extract_all surfaces the new categories
# ---------------------------------------------------------------------------


def test_extract_all_includes_new_categories():
    sk = "6L" + "a" * 38
    html = (
        '<a href="https://hackerone.com/anthropic">disclosure</a>'
        '<a href="https://stripe-status.statuspage.io/">status</a>'
        f'<div class="g-recaptcha" data-sitekey="{sk}"></div>'
    )
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20240101120000"}]
    res = extract_all(pages, "example.com")
    assert len(res["bug_bounty_programs"]) == 1
    assert len(res["status_pages"]) == 1
    assert len(res["captcha_providers"]) == 1
