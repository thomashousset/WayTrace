"""Tests for ATS / IdP / chat-token detection.

* job_boards (Greenhouse / Lever / Workable / Ashby / Personio /
  Recruitee / BambooHR / SmartRecruiters)
* auth_providers (Auth0 / Okta / Cognito / Keycloak / WorkOS / Clerk)
* api_keys: Telegram bot tokens and Discord webhook URLs
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.job_boards_extract import extract_job_boards
from services.extractor.auth_providers_extract import extract_auth_providers
from services.extractor.patterns import (
    API_KEY_PATTERNS,
    DISCORD_WEBHOOK_RE,
    TELEGRAM_BOT_RE,
)


# ---------------------------------------------------------------------------
# job_boards
# ---------------------------------------------------------------------------


def _jb(html: str) -> list[dict]:
    accum = {"job_boards": {}}
    extract_job_boards(html, "2024-06", accum)
    return list(accum["job_boards"].values())


def test_jb_greenhouse_extracted():
    out = _jb('<a href="https://boards.greenhouse.io/anthropic">jobs</a>')
    assert any(p["platform"] == "greenhouse" and p["slug"] == "anthropic" for p in out)


def test_jb_greenhouse_api_endpoint():
    out = _jb('<script src="https://boards-api.greenhouse.io/v1/boards/anthropic/jobs"></script>')
    assert any(p["platform"] == "greenhouse" and p["slug"] == "anthropic" for p in out)


def test_jb_lever_extracted():
    out = _jb('<a href="https://jobs.lever.co/scaleai">careers</a>')
    assert any(p["platform"] == "lever" and p["slug"] == "scaleai" for p in out)


def test_jb_workable_subdomain():
    out = _jb('<a href="https://stripe.workable.com/jobs">x</a>')
    assert any(p["platform"] == "workable" and p["slug"] == "stripe" for p in out)


def test_jb_workable_apply_form():
    out = _jb('<a href="https://apply.workable.com/notion/j/abc123">x</a>')
    assert any(p["platform"] == "workable" and p["slug"] == "notion" for p in out)


def test_jb_ashby_extracted():
    out = _jb('<a href="https://jobs.ashbyhq.com/vercel">vercel jobs</a>')
    assert any(p["platform"] == "ashby" and p["slug"] == "vercel" for p in out)


def test_jb_personio_subdomain():
    out = _jb('<a href="https://acme.jobs.personio.com/">careers</a>')
    assert any(p["platform"] == "personio" and p["slug"] == "acme" for p in out)


def test_jb_recruitee_subdomain():
    out = _jb('<a href="https://oteria.recruitee.com/">x</a>')
    assert any(p["platform"] == "recruitee" and p["slug"] == "oteria" for p in out)


def test_jb_reserved_slugs_skipped():
    out = _jb('<a href="https://www.workable.com/">marketing</a>')
    assert all(p["slug"] != "www" for p in out)


# ---------------------------------------------------------------------------
# auth_providers
# ---------------------------------------------------------------------------


def _ap(html: str) -> list[dict]:
    accum = {"auth_providers": {}}
    extract_auth_providers(html, "2024-06", accum)
    return list(accum["auth_providers"].values())


def test_ap_auth0_tenant():
    out = _ap('<script src="https://acme.auth0.com/widget.js"></script>')
    assert any(p["platform"] == "auth0" and p["tenant"] == "acme" for p in out)


def test_ap_auth0_eu_subdomain():
    out = _ap('<a href="https://acme.eu.auth0.com/login">login</a>')
    assert any(p["platform"] == "auth0" and p["tenant"] == "acme" for p in out)


def test_ap_okta_tenant():
    out = _ap('<a href="https://stripe.okta.com/login">SSO</a>')
    assert any(p["platform"] == "okta" and p["tenant"] == "stripe" for p in out)


def test_ap_okta_preview():
    out = _ap('<a href="https://acme.oktapreview.com/">x</a>')
    assert any(p["platform"] == "okta" and p["tenant"] == "acme" for p in out)


def test_ap_cognito_userpool():
    out = _ap(
        '<script>const pool="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_aBcDeFgHi/...";</script>'
    )
    assert any(p["platform"] == "cognito" for p in out)


def test_ap_keycloak_realm():
    out = _ap('<a href="https://sso.acme.com/auth/realms/employees/account">x</a>')
    assert any(p["platform"] == "keycloak" and p["tenant"] == "employees" for p in out)


def test_ap_clerk_dev_tenant():
    out = _ap('<script src="https://acme-prod.clerk.accounts.dev/sdk.js"></script>')
    assert any(p["platform"] == "clerk" for p in out)


def test_ap_reserved_slugs_skipped():
    """Provider's own subdomains (api / dashboard / login) are filtered."""
    out = _ap('<a href="https://login.okta.com/">x</a>')
    assert all(p["tenant"] != "login" for p in out)


def test_ap_auth0_ignores_cdn_host():
    """cdn.auth0.com is Auth0's universal JS CDN, on every Auth0 site;
    'cdn' is not a tenant."""
    out = _ap('<script src="https://cdn.auth0.com/js/lock/11.x/lock.min.js"></script>')
    assert all(not (p["platform"] == "auth0" and p["tenant"] == "cdn") for p in out)


def test_ap_keycloak_ignores_master_realm():
    """'master' is the built-in Keycloak admin realm present on every
    install; it does not identify an org."""
    out = _ap('<a href="https://sso.acme.com/auth/realms/master/account">x</a>')
    assert all(not (p["platform"] == "keycloak" and p["tenant"] == "master") for p in out)


def test_jb_workable_ignores_marketing_subdomain():
    """resources.workable.com is Workable's own content host, not an ATS tenant."""
    out = _jb('<a href="https://resources.workable.com/stories">x</a>')
    assert all(not (p["platform"] == "workable" and p["slug"] == "resources") for p in out)


# ---------------------------------------------------------------------------
# Telegram bot tokens + Discord webhook
# ---------------------------------------------------------------------------


def test_telegram_bot_token_pattern():
    tok = "1234567890:" + "a" * 35
    assert TELEGRAM_BOT_RE.search(tok) is not None
    assert API_KEY_PATTERNS["Telegram_Bot"].search(tok) is not None


def test_telegram_bot_token_short_id_rejected():
    assert TELEGRAM_BOT_RE.search("12:" + "a" * 35) is None


def test_discord_webhook_url_pattern():
    url = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 60
    assert DISCORD_WEBHOOK_RE.search(url) is not None
    assert API_KEY_PATTERNS["Discord_Webhook"].search(url) is not None


def test_discord_webhook_app_subdomain():
    url = "https://discordapp.com/api/webhooks/100/" + "x" * 50
    assert DISCORD_WEBHOOK_RE.search(url) is not None


def test_discord_webhook_does_not_match_invite():
    """`discord.gg/<code>` is a server invite, not a webhook."""
    assert DISCORD_WEBHOOK_RE.search("https://discord.gg/abcdef") is None


# ---------------------------------------------------------------------------
# End-to-end: extract_all surfaces the new categories
# ---------------------------------------------------------------------------


def test_extract_all_includes_more_new_categories():
    html = (
        '<a href="https://boards.greenhouse.io/anthropic">jobs</a>'
        '<a href="https://stripe.okta.com/login">SSO</a>'
    )
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20240101120000"}]
    res = extract_all(pages, "example.com")
    assert len(res["job_boards"]) >= 1
    assert len(res["auth_providers"]) >= 1
