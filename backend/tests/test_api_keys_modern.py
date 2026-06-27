"""Tests for modern API key patterns (OpenAI, Anthropic, GitLab, HuggingFace, Notion, Discord).

These services post-date the original API_KEY_PATTERNS table; tokens leaked
in archived HTML still pivot to live accounts (high LEAK value), so the
extractor must catch them.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.patterns import (
    ANTHROPIC_RE,
    API_KEY_PATTERNS,
    DIGITALOCEAN_RE,
    MAILGUN_RE,
    DISCORD_TOKEN_RE,
    GITLAB_TOKEN_RE,
    HUGGINGFACE_RE,
    LINEAR_RE,
    NOTION_RE,
    NPM_TOKEN_RE,
    OPENAI_RE,
    SHOPIFY_RE,
    SUPABASE_RE,
)


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


def test_openai_legacy_48char_key():
    key = "sk-" + "a" * 24 + "B" * 12 + "1" * 12  # 48 alnum body
    assert OPENAI_RE.search(key) is not None
    assert API_KEY_PATTERNS["OpenAI"].search(key) is not None


def test_openai_project_key():
    key = "sk-proj-" + "a" * 40 + "_-" + "B" * 30
    assert OPENAI_RE.search(key) is not None


def test_openai_service_account_key():
    key = "sk-svcacct-" + "Z" * 50
    assert OPENAI_RE.search(key) is not None


def test_openai_admin_key():
    key = "sk-admin-" + "a1" * 30
    assert OPENAI_RE.search(key) is not None


def test_openai_does_not_match_stripe():
    # Stripe uses underscore, not hyphen.
    assert OPENAI_RE.search("sk_test_1234567890abcdef1234567890") is None


def test_openai_does_not_match_short_string():
    assert OPENAI_RE.search("sk-tooshort") is None


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def test_anthropic_api03_key():
    key = "sk-ant-api03-" + "a" * 50 + "_" + "B" * 50
    assert ANTHROPIC_RE.search(key) is not None
    assert API_KEY_PATTERNS["Anthropic"].search(key) is not None


def test_anthropic_session_key():
    key = "sk-ant-sid01-" + "Z" * 95
    assert ANTHROPIC_RE.search(key) is not None


def test_anthropic_does_not_match_too_short():
    assert ANTHROPIC_RE.search("sk-ant-api03-tooshort") is None


def test_anthropic_does_not_match_bare_sk_ant():
    assert ANTHROPIC_RE.search("sk-ant-something") is None


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def test_gitlab_personal_access_token():
    key = "glpat-" + "abcdef1234567890ABCD"
    assert GITLAB_TOKEN_RE.search(key) is not None
    assert API_KEY_PATTERNS["GitLab"].search(key) is not None


def test_gitlab_deploy_token():
    key = "gldt-" + "X" * 25
    assert GITLAB_TOKEN_RE.search(key) is not None


def test_gitlab_runner_token():
    key = "glrt-" + "Z" * 25
    assert GITLAB_TOKEN_RE.search(key) is not None


def test_gitlab_does_not_match_unrelated_glpat_prose():
    assert GITLAB_TOKEN_RE.search("glpat-short") is None


# ---------------------------------------------------------------------------
# HuggingFace
# ---------------------------------------------------------------------------


def test_huggingface_token():
    key = "hf_" + "A" * 35
    assert HUGGINGFACE_RE.search(key) is not None
    assert API_KEY_PATTERNS["HuggingFace"].search(key) is not None


def test_huggingface_too_short():
    assert HUGGINGFACE_RE.search("hf_short") is None


def test_huggingface_too_long():
    # 41 chars > 40 max
    assert HUGGINGFACE_RE.search("hf_" + "A" * 41) is None


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------


def test_notion_integration_secret():
    key = "secret_" + "abcdef1234567890ABCDEFGHIJKLMNOPQRSTUVWxyz1"  # 43 chars
    assert NOTION_RE.search(key) is not None
    assert API_KEY_PATTERNS["Notion"].search(key) is not None


def test_notion_too_short():
    assert NOTION_RE.search("secret_short") is None


def test_notion_does_not_match_random_secret_prose():
    # 'secret_password' doesn't have 43 base62 chars.
    assert NOTION_RE.search("secret_password=hunter2") is None


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


def test_discord_bot_token():
    # Three dot-separated parts: id (24-29 base64url), time (6-7), hmac (27-38).
    key = "M" + "A" * 23 + ".AbCdEf." + "Z" * 30
    assert DISCORD_TOKEN_RE.search(key) is not None
    assert API_KEY_PATTERNS["Discord_Token"].search(key) is not None


def test_discord_does_not_match_two_parts():
    assert DISCORD_TOKEN_RE.search("MAB.CDE") is None


# ---------------------------------------------------------------------------
# Coexistence: Stripe + OpenAI in the same blob both fire.
# ---------------------------------------------------------------------------


def test_stripe_and_openai_both_fire():
    text = (
        'STRIPE = "sk_live_' + "a" * 30 + '"\n'
        'OPENAI = "sk-' + "B" * 48 + '"\n'
    )
    matched = {name for name, p in API_KEY_PATTERNS.items() if p.search(text)}
    assert "Stripe" in matched
    assert "OpenAI" in matched


# ---------------------------------------------------------------------------
# Supabase / DigitalOcean / Shopify / Linear / npm (added 2026-05)
# ---------------------------------------------------------------------------


def test_supabase_pat():
    key = "sbp_" + "0123456789abcdef" * 2 + "01234567"  # sbp_ + 40 hex
    assert SUPABASE_RE.search(key) is not None
    assert API_KEY_PATTERNS["Supabase"].search(key) is not None


def test_supabase_rejects_short():
    assert SUPABASE_RE.search("sbp_deadbeef") is None


def test_digitalocean_pat():
    key = "dop_v1_" + "a1b2c3d4" * 8  # dop_v1_ + 64 hex
    assert DIGITALOCEAN_RE.search(key) is not None
    assert API_KEY_PATTERNS["DigitalOcean"].search(key) is not None


def test_digitalocean_oauth_and_refresh_prefixes():
    assert DIGITALOCEAN_RE.search("doo_v1_" + "f" * 64) is not None
    assert DIGITALOCEAN_RE.search("dor_v1_" + "0a" * 32) is not None


def test_digitalocean_rejects_wrong_version():
    assert DIGITALOCEAN_RE.search("dop_v2_" + "a" * 64) is None


def test_shopify_access_token():
    key = "shpat_" + "abcdef0123456789" * 2  # shpat_ + 32 hex
    assert SHOPIFY_RE.search(key) is not None
    assert API_KEY_PATTERNS["Shopify"].search(key) is not None


def test_shopify_rejects_non_hex_body():
    assert SHOPIFY_RE.search("shpat_" + "g" * 32) is None


def test_linear_api_key():
    key = "lin_api_" + "A1b2" * 10  # lin_api_ + 40 alnum
    assert LINEAR_RE.search(key) is not None
    assert API_KEY_PATTERNS["Linear"].search(key) is not None


def test_linear_rejects_short():
    assert LINEAR_RE.search("lin_api_tooshort") is None


def test_npm_token():
    key = "npm_" + "Ab9" * 12  # npm_ + 36 base62
    assert NPM_TOKEN_RE.search(key) is not None
    assert API_KEY_PATTERNS["npm"].search(key) is not None


def test_npm_rejects_short():
    assert NPM_TOKEN_RE.search("npm_short") is None


# ---------------------------------------------------------------------------
# Mailgun - must not collide with `*-cache-key-<md5>` fragments (false LEAK)
# ---------------------------------------------------------------------------


def test_mailgun_standalone_key_matches():
    key = "key-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # key- + 32 hex
    assert MAILGUN_RE.search(key) is not None
    assert API_KEY_PATTERNS["Mailgun"].search(key) is not None


def test_mailgun_ignores_cache_key_md5_fragment():
    # md5 = exactly 32 chars; "fragment-cache-key-<md5>" is ubiquitous in
    # Rails/WordPress HTML and must not surface as a leaked Mailgun key.
    blob = "fragment-cache-key-5f4dcc3b5aa765d61d8327deb882cf99"
    assert MAILGUN_RE.search(blob) is None
    assert API_KEY_PATTERNS["Mailgun"].search(blob) is None


def test_mailgun_ignores_data_key_attribute():
    blob = 'data-key-5f4dcc3b5aa765d61d8327deb882cf99'
    assert MAILGUN_RE.search(blob) is None


def test_mailgun_ignores_oversized_run():
    # 40-char hash after key- is not a 32-char Mailgun key.
    blob = "key-5f4dcc3b5aa765d61d8327deb882cf995f4dcc3b"
    assert MAILGUN_RE.search(blob) is None


# ---------------------------------------------------------------------------
# Twilio - AC/SK + 32 hex is structurally identical to an md5 / react-id, and
# it lands in LEAK. Require a nearby Twilio context keyword (precision over
# recall: real leaked SIDs sit next to the vendor name / config key).
# ---------------------------------------------------------------------------


def _api_keys(html: str) -> list[dict]:
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
    return extract_all(pages, "example.com")["api_keys"]


def test_twilio_sid_with_context_is_kept():
    sid = "AC" + "a1b2c3d4" * 4  # AC + 32 hex
    html = f'<script>var twilioAccountSid = "{sid}";</script>'
    keys = _api_keys(html)
    assert any(k["value"] == sid and k["type"] == "Twilio" for k in keys)


def test_twilio_api_key_sid_with_accountsid_context_is_kept():
    sid = "SK" + "0f1e2d3c" * 4
    html = f'<code>accountSid: "{sid}"</code>'
    keys = _api_keys(html)
    assert any(k["value"] == sid and k["type"] == "Twilio" for k in keys)


def test_twilio_like_md5_without_context_is_dropped():
    # "AC" + md5 (32 hex) in a react id, with no Twilio context nearby, is
    # almost never a real Account SID.
    h = "AC" + "5f4dcc3b5aa765d61d8327deb882cf99"
    html = f'<div data-reactid="{h}">x</div>'
    keys = _api_keys(html)
    assert all(k["type"] != "Twilio" for k in keys)
