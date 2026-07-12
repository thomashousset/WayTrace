"""Regression tests for false-positive and dedup fixes.

Covers phone E.164 canonicalisation, JSON-LD strict @type=Person,
subdomain www-prefix dedup, html_comments CMS-noise denylist, the
LinkedIn opaque-handle filter, hosting detection via response
headers, Discord invite extraction and RNCP detection.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.french_business_ids_extract import extract_french_business_ids
from services.extractor.helpers import canonicalize_phone_key, phone_display
from services.extractor.hosting_extract import detect_hosting


# ---------------------------------------------------------------------------
# Phone canonical dedup
# ---------------------------------------------------------------------------


def test_phone_canonical_key_fr_national_to_e164():
    assert canonicalize_phone_key("0188615589") == "33188615589"
    assert canonicalize_phone_key("0623826414") == "33623826414"


def test_phone_canonical_key_already_e164_passthrough():
    assert canonicalize_phone_key("33188615589") == "33188615589"


def test_phone_canonical_key_fr_premium_left_raw():
    # 08 premium / 00 special are not canonicalized to +33.
    assert canonicalize_phone_key("0825123456") == "0825123456"


def test_phone_canonical_key_unknown_country_passthrough():
    # 11-digit US number stays as-is.
    assert canonicalize_phone_key("12125551234") == "12125551234"


def test_phone_display_prefers_plus_form():
    assert phone_display("33188615589", "+33188615589") == "+33188615589"
    assert phone_display("33188615589", "0188615589") == "+33188615589"


def test_phone_extraction_dedup_national_and_e164():
    """0188615589 and +33188615589 should fuse into one phone entity."""
    html = """<html><body>
    <p>Téléphone : 01 88 61 55 89</p>
    <p>From abroad: <a href="tel:+33188615589">+33 1 88 61 55 89</a></p>
    </body></html>"""
    pages = [{"html": html, "url": "https://oteria.fr/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "oteria.fr")
    keys = [p["value"] for p in results["phones"] if "188615589" in p["value"]]
    # Same number must collapse to a single canonical entry.
    assert len(set(keys)) <= 1


# ---------------------------------------------------------------------------
# Persons JSON-LD strict @type=Person
# ---------------------------------------------------------------------------


def test_persons_jsonld_organization_name_not_emitted():
    """An Article whose author is an Organization must not leak the org
    name into persons (oteria.fr v2 emitted "Oteria" as a person)."""
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Article",
     "headline":"Cybersecurity 101",
     "author":{"@type":"Organization","name":"Oteria"}}
    </script></head><body></body></html>"""
    pages = [{"html": html, "url": "https://oteria.fr/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "oteria.fr")
    names = [p["name"] for p in results["persons"]]
    assert "Oteria" not in names
    assert "oteria" not in [n.lower() for n in names]


def test_persons_jsonld_person_at_type_emitted():
    html = """<html><head>
    <script type="application/ld+json">
    {"@type":"Person","name":"Jane Doe"}
    </script></head><body></body></html>"""
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "example.com")
    assert any(p["name"] == "Jane Doe" for p in results["persons"])


def test_persons_jsonld_bare_string_author_emitted():
    html = """<html><head>
    <script type="application/ld+json">
    {"@type":"Article","author":"Alice Martin"}
    </script></head><body></body></html>"""
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "example.com")
    assert any(p["name"] == "Alice Martin" for p in results["persons"])


# ---------------------------------------------------------------------------
# Subdomain www-strip dedup
# ---------------------------------------------------------------------------


def test_subdomain_www_strip_dedup():
    """www.x.example.com and x.example.com should not double-emit."""
    html = """<html><body>
    <a href="https://www.cafe-cyber.oteria.fr/">a</a>
    <a href="https://cafe-cyber.oteria.fr/">b</a>
    </body></html>"""
    pages = [{"html": html, "url": "https://oteria.fr/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "oteria.fr")
    subs = [s["value"] for s in results["subdomains"]]
    assert "cafe-cyber.oteria.fr" in subs
    assert "www.cafe-cyber.oteria.fr" not in subs


def test_subdomain_www_apex_skipped():
    """www.apex itself is the apex's www-mirror, not a subdomain."""
    html = '<a href="https://www.example.com/">home</a>'
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "example.com")
    assert results["subdomains"] == []


# ---------------------------------------------------------------------------
# HTML comments denylist (Webflow / Finsweet / GTM noise)
# ---------------------------------------------------------------------------


def test_html_comment_finsweet_dropped():
    html = "<html><body><!-- [Attributes by Finsweet] CMS Filter --></body></html>"
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    assert results["html_comments"] == []


def test_html_comment_gtm_dropped():
    html = "<html><body><!-- Google Tag Manager (noscript) --></body></html>"
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    assert results["html_comments"] == []


def test_html_comment_webflow_last_published_dropped():
    html = """<html><body>
    <!-- Last Published: Wed May 21 2025 10:00:00 GMT+0000 (Coordinated Universal Time) -->
    </body></html>"""
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    assert results["html_comments"] == []


def test_html_comment_real_todo_kept():
    html = "<html><body><!-- TODO: rotate API key before deploy --></body></html>"
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    assert any("TODO" in c.get("comment", "") for c in results["html_comments"])


# ---------------------------------------------------------------------------
# LinkedIn anonymized handle filter
# ---------------------------------------------------------------------------


def test_linkedin_anon_acoaa_filtered():
    html = """<html><body>
    <a href="https://linkedin.com/in/ACoAADPMbhkBXMj2oMvP2_RlgSO8nwxLT3Pcor4">x</a>
    <a href="https://linkedin.com/in/jane-doe">y</a>
    </body></html>"""
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    handles = [p["handle"] for p in results["social_profiles"] if p["platform"] == "linkedin"]
    assert "jane-doe" in handles
    assert all(not h.startswith("ACoA") for h in handles)


# ---------------------------------------------------------------------------
# Hosting from response headers
# ---------------------------------------------------------------------------


def test_hosting_fastly_from_x_served_by_header():
    html = "<html></html>"
    headers = {"x-served-by": "cache-iad-kcgs7200103-IAD"}
    out = detect_hosting(html, headers=headers)
    providers = [h["provider"] for h in out]
    assert "Fastly" in providers


def test_hosting_openresty_from_server_header():
    html = "<html></html>"
    headers = {"server": "openresty"}
    out = detect_hosting(html, headers=headers)
    providers = [h["provider"] for h in out]
    assert "OpenResty" in providers


def test_hosting_cloudflare_from_cf_ray_header():
    html = "<html></html>"
    headers = {"cf-ray": "8ab2cd3ef4567-AMS"}
    out = detect_hosting(html, headers=headers)
    providers = [h["provider"] for h in out]
    assert "Cloudflare" in providers


def test_hosting_no_headers_path_unchanged():
    """detect_hosting must remain backward-compatible without headers."""
    html = '<script src="https://cdn.shopify.com/x.js"></script>'
    out = detect_hosting(html)
    providers = [h["provider"] for h in out]
    assert "Shopify" in providers


# ---------------------------------------------------------------------------
# Discord invites
# ---------------------------------------------------------------------------


def test_discord_invite_short_form_extracted():
    html = '<a href="https://discord.gg/YHxhYnQYwS">Join</a>'
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    discord = [p for p in results["social_profiles"] if p["platform"] == "discord"]
    assert any(p["handle"] == "YHxhYnQYwS" for p in discord)


def test_discord_invite_long_form_extracted():
    html = '<a href="https://discord.com/invite/abc123def">Join</a>'
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    discord = [p for p in results["social_profiles"] if p["platform"] == "discord"]
    assert any(p["handle"] == "abc123def" for p in discord)


# ---------------------------------------------------------------------------
# RNCP extraction
# ---------------------------------------------------------------------------


def test_rncp_bare_token_extracted():
    out = extract_french_business_ids("<p>Titre RNCP36122 reconnu par l'État.</p>")
    rncp = [i for i in out if i["type"] == "rncp"]
    assert any(i["value"] == "RNCP36122" for i in rncp)
    assert "francecompetences.fr/recherche/rncp/36122" in rncp[0]["pivot_url"]


def test_rncp_with_space_extracted():
    out = extract_french_business_ids("<p>Référence : RNCP 38765 (niveau 7)</p>")
    assert any(i["type"] == "rncp" and i["value"] == "RNCP38765" for i in out)


def test_rncp_url_extracted_validated_true():
    html = '<a href="https://www.francecompetences.fr/recherche/rncp/36122/">cert</a>'
    out = extract_french_business_ids(html)
    rncp = [i for i in out if i["type"] == "rncp"]
    assert rncp
    assert rncp[0]["validated"] is True


def test_rncp_dedups_when_token_and_url_both_present():
    html = """<p>Notre formation est référencée RNCP36122
    (<a href="https://www.francecompetences.fr/recherche/rncp/36122/">détail</a>).</p>"""
    out = extract_french_business_ids(html)
    rncp = [i for i in out if i["type"] == "rncp"]
    assert len(rncp) == 1


def test_rncp_rejects_too_short_or_too_long():
    out = extract_french_business_ids("<p>Reference RNCP12 or RNCP1234567 invalid.</p>")
    rncp = [i for i in out if i["type"] == "rncp"]
    assert rncp == []


# ---------------------------------------------------------------------------
# Regressions surfaced by smoke scans on real-world archives.
# ---------------------------------------------------------------------------


def test_email_json_escape_leak_dropped():
    """Anthropic.com archived blog had ``\\u003esupport@anthropic.com``
    leaks from JSON-encoded HTML. The local-part starts with the unicode
    escape `u003e` and must be rejected."""
    html = '<p>Contact \\u003esupport@anthropic.com for help.</p>'
    pages = [{"html": html, "url": "https://anthropic.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "anthropic.com")
    locals_seen = [e["value"].split("@")[0] for e in results["emails"]]
    assert all(not l.startswith("u00") for l in locals_seen)


def test_siren_periodic_pattern_dropped():
    """243243243 satisfies Luhn but is a 3-digit motif × 3. drop it.
    anthropic.com's archived FR translation page produced this FP."""
    out = extract_french_business_ids(
        "<p>SIREN: 243243243 reference de demonstration</p>"
    )
    assert all(i["value"] != "243243243" for i in out if i["type"] == "siren")


def test_siren_alternating_two_digit_pattern_dropped():
    """212121212 alternates 2-1: caught by len(distinct)<=2 already."""
    out = extract_french_business_ids("<p>SIREN: 212121212 demo</p>")
    assert all(i["value"] != "212121212" for i in out if i["type"] == "siren")


def test_siret_periodic_pattern_dropped():
    out = extract_french_business_ids(
        "<p>SIRET: 12345671234567 demonstration</p>"
    )
    assert all(i["value"] != "12345671234567" for i in out if i["type"] == "siret")


def test_organization_url_as_name_rejected():
    """stripe.com's JSON-LD had ``name=https://stripe.com/``. reject
    URL-shaped strings as organization names."""
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Organization",
     "name":"https://stripe.com/","url":"https://stripe.com/"}
    </script></head><body></body></html>"""
    pages = [{"html": html, "url": "https://stripe.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "stripe.com")
    names = [o["name"] for o in results.get("organizations", [])]
    assert all(not n.startswith("http") for n in names)


def test_organization_real_name_kept():
    html = """<html><head>
    <script type="application/ld+json">
    {"@type":"Organization","name":"Anthropic PBC","url":"https://anthropic.com/"}
    </script></head><body></body></html>"""
    pages = [{"html": html, "url": "https://anthropic.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "anthropic.com")
    names = [o["name"] for o in results.get("organizations", [])]
    assert "Anthropic PBC" in names


def test_api_key_public_tier_for_stripe_pk():
    """pk_live_/pk_test_ are publishable Stripe keys, frontend-safe.
    Tag tier=public so highlights demote them from LEAK to PIVOT."""
    pk = "pk_live_" + "a" * 30
    html = f'<script>window.STRIPE_PK = "{pk}";</script>'
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    keys = results["api_keys"]
    pk_entries = [k for k in keys if k["value"].startswith("pk_live_")]
    assert pk_entries
    assert pk_entries[0]["tier"] == "public"


def test_api_key_secret_tier_for_stripe_sk():
    sk = "sk_live_" + "a" * 30
    html = f'<script>const SK = "{sk}";</script>'
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    keys = results["api_keys"]
    sk_entries = [k for k in keys if k["value"].startswith("sk_live_")]
    assert sk_entries
    assert sk_entries[0]["tier"] == "secret"


def test_api_key_oauth_client_tier_public():
    oauth = "1062961139910-l2m55cb9h51u5cuc9c56eb3fevouidh9.apps.googleusercontent.com"
    html = f'<script>const CLIENT_ID = "{oauth}";</script>'
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20240101120000"}]
    results = extract_all(pages, "x.com")
    keys = results["api_keys"]
    matched = [k for k in keys if k["type"] == "Google_OAuth_Client"]
    assert matched
    assert matched[0]["tier"] == "public"




def test_highlights_split_secret_vs_public_keys():
    """A page with both a sk_live_ secret and a pk_live_ public key must
    produce one LEAK highlight and one PIVOT api_keys_public highlight."""
    from services.extractor.highlights import compute_highlights
    from services.extractor.finalize import ALL_CATEGORIES
    results = {cat: [] for cat in ALL_CATEGORIES}
    results["api_keys"] = [
        {"type": "Stripe", "value": "sk_live_" + "a" * 30, "tier": "secret",
         "first_seen": "2024-01", "last_seen": "2024-06", "occurrences": 1},
        {"type": "Stripe", "value": "pk_live_" + "b" * 30, "tier": "public",
         "first_seen": "2024-01", "last_seen": "2024-06", "occurrences": 1},
    ]
    hs = compute_highlights(results, "x.com")
    leaks = [h for h in hs if h["severity"] == "LEAK" and h["category"] == "api_keys"]
    pivots = [h for h in hs if h["severity"] == "PIVOT" and h["category"] == "api_keys_public"]
    assert len(leaks) == 1
    assert len(pivots) == 1
