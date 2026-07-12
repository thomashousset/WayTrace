"""Finding value/severity formatting helpers.

These map a raw extractor item to its canonical display value and OSINT-value
severity. Shared by the CSV export in routers/public.py. (The legacy DB-backed
analysis pipeline that used to live here was removed once the public scan flow
became the single pipeline.)
"""
from __future__ import annotations


def _item_value(category: str, item: dict) -> str | None:
    """Extract the canonical string value for an item."""
    if category == "analytics_trackers":
        return item.get("id") or item.get("value")
    if category == "social_profiles":
        return item.get("url") or item.get("handle")
    if category == "technologies":
        return item.get("technology") or item.get("value")
    if category == "persons":
        return item.get("name") or item.get("value")
    if category == "phones":
        return item.get("normalized") or item.get("raw") or item.get("value")
    if category == "api_keys":
        return item.get("value")
    if category == "jwt_tokens":
        return item.get("token") or item.get("value")
    if category == "endpoints":
        return item.get("path") or item.get("value")
    if category == "directory_listings":
        return item.get("path") or item.get("url") or item.get("value")
    if category == "organizations":
        return item.get("name") or item.get("value")
    if category == "addresses":
        return item.get("value") or ", ".join(filter(None, [item.get("street"), item.get("city")]))
    if category == "linked_documents":
        return item.get("url") or item.get("value")
    if category == "html_comments":
        return item.get("comment", "")[:200]
    if category == "meta_info":
        return item.get("content", "")[:200]
    if category == "html_titles":
        return item.get("content", "")[:200]
    if category == "hidden_fields":
        return f"{item.get('name', '')}:{item.get('value', '')[:40]}"
    if category == "internal_ips":
        return item.get("ip") or item.get("value")
    if category == "adsense_ids":
        return item.get("id") or item.get("value")
    if category == "verification_tags":
        return item.get("verification_id") or item.get("value")
    if category == "iframe_sources":
        return item.get("url") or item.get("value")
    if category == "js_urls":
        return item.get("url") or item.get("value")
    if category == "connection_strings":
        return item.get("value")
    if category == "crypto_addresses":
        return item.get("address") or item.get("value")
    if category == "favicons":
        return item.get("url") or item.get("value")
    if category == "outgoing_links":
        return item.get("url") or item.get("value")
    if category == "hosting":
        return item.get("provider") or item.get("value")
    if category == "http_headers":
        # Stored as "<type>: <value>" so the user sees both the header type and its value
        t = item.get("type", "")
        v = item.get("value", "")
        return f"{t}: {v}" if t else v
    if category == "bug_bounty_programs":
        # Display the pivot URL. most actionable form for the user.
        return item.get("pivot_url") or f"{item.get('platform','')}/{item.get('handle','')}"
    if category == "captcha_providers":
        # "provider:sitekey" or just provider when only the script was seen.
        sk = item.get("sitekey") or ""
        return f"{item.get('provider','?')}:{sk}" if sk else item.get("provider", "")
    if category == "status_pages":
        return item.get("pivot_url") or item.get("slug") or item.get("value")
    if category == "job_boards":
        return item.get("pivot_url") or f"{item.get('platform','')}/{item.get('slug','')}"
    if category == "auth_providers":
        return item.get("pivot_url") or f"{item.get('platform','')}/{item.get('tenant','')}"
    # Categories whose accumulator items don't carry a top-level "value"
    # key. Without these branches the fallback `item.get("value")` returns
    # None and the finding is silently dropped at the persistence layer
    # (run_analysis: `if value is None: continue`).
    if category == "assets":
        return item.get("path") or item.get("value")
    if category == "analytics_ids":
        # "platform:id_value" disambiguates the same id across providers
        # (e.g. a hex id reused by Mixpanel and Amplitude).
        plat = item.get("platform", "")
        idv = item.get("id_value") or ""
        if plat and idv:
            return f"{plat}:{idv}"
        return idv or item.get("value")
    if category == "cookie_consent":
        # account_id is the actionable pivot; fall back to the platform
        # name when we only detected the script without a tenant id.
        plat = item.get("platform", "")
        acct = item.get("account_id") or ""
        if plat and acct:
            return f"{plat}:{acct}"
        return plat or item.get("value")
    if category == "rss_feeds":
        return item.get("url") or item.get("value")
    if category == "github_repos":
        # Prefer pivot_url (browser-clickable). fall back to owner/repo.
        pivot = item.get("pivot_url")
        if pivot:
            return pivot
        owner, repo = item.get("owner", ""), item.get("repo", "")
        if owner and repo:
            return f"{owner}/{repo}"
        return item.get("value")
    if category == "sitemaps_and_robots":
        return item.get("url") or item.get("value")
    if category == "pgp_keys":
        # identifier is the fingerprint / key id. it's the canonical handle
        # for the key regardless of where it was published.
        return item.get("identifier") or item.get("pivot_url") or item.get("value")
    return item.get("value")


# OSINT-first taxonomy (replaces security CVSS-style labels):
#   LEAK       -> actual security exposure the owner almost certainly didn't
#                 mean to publish. api_keys, credentials, dirlist, internal
#                 IP leaks, JWT sensitive claims. Red.
#   PIVOT      -> lead for further investigation. The finding itself is
#                 usually benign but gives you the next breadcrumb:
#                 internal-named endpoints, subdomains, persons, trackers
#                 (cross-domain correlation), verification tags, favicon
#                 hashes (Shodan/Censys pivot), crypto addresses.
#   CONTEXT    -> useful background that shapes follow-up. Hosting, tech
#                 stack, meta-info, HTTP fingerprints, public email
#                 patterns, iframe integrations. Blue.
#   BACKGROUND -> noise-tier listing. Public endpoints, outgoing links,
#                 social profiles, html comments, pretty benign JS URLs.
#                 Gray. Visible in the table but never highlighted.
#
# Reclassification notes:
#   * Generic public inboxes like info@/contact@/support@ were "HIGH" under
#     the old scheme. they don't warrant the PIVOT badge the way a
#     personal jane.doe@domain does.
#   * Subdomains stay elevated but become PIVOT, not HIGH: the UI framing
#     of "HIGH severity" on a legitimate sub was misleading for OSINT.

_ENDPOINT_PIVOT_PREFIXES = ("/api", "/admin", "/login", "/auth", "/graphql",
                            "/internal", "/debug", "/staging", "/dashboard")
_GENERIC_EMAIL_LOCAL_PARTS = ("info@", "contact@", "support@", "hello@",
                              "team@", "office@", "marketing@", "press@",
                              "sales@", "rgpd@", "dpo@", "careers@",
                              "handicap@", "accessibilite@")
_PERSONAL_EMAIL_PATTERNS = ("admin@", "root@", "security@", "webmaster@")


def _item_severity(category: str, item: dict) -> str | None:
    if category == "api_keys":
        return "LEAK"
    if category == "cloud_buckets":
        return "LEAK"
    if category == "jwt_tokens":
        # Sensitive claims = LEAK, bare token = PIVOT (could still be valid)
        if item.get("sensitive_claims"):
            return "LEAK"
        return "PIVOT"
    if category == "directory_listings":
        return "LEAK"
    if category == "emails":
        email = item.get("value", "")
        if any(p in email for p in _PERSONAL_EMAIL_PATTERNS):
            return "PIVOT"
        if any(p in email for p in _GENERIC_EMAIL_LOCAL_PARTS):
            return "CONTEXT"
        # Named mailboxes (jane.doe@, j-smith@) are the prime OSINT pivots.
        local = email.split("@", 1)[0]
        if "." in local or "-" in local:
            return "PIVOT"
        return "CONTEXT"
    if category == "subdomains":
        return "PIVOT"
    if category == "endpoints":
        path = item.get("path", "")
        if any(path.startswith(p) for p in _ENDPOINT_PIVOT_PREFIXES):
            return "PIVOT"
        return "BACKGROUND"
    if category == "analytics_trackers":
        return "PIVOT"   # same tracker ID across domains == same operator
    if category == "technologies":
        return "CONTEXT"
    if category == "persons":
        return "PIVOT"
    if category == "phones":
        return "CONTEXT"
    if category == "social_profiles":
        return "BACKGROUND"
    if category == "connection_strings":
        return "LEAK" if item.get("has_credentials") else "PIVOT"
    if category == "internal_ips":
        return "LEAK"
    if category == "js_urls":
        url = item.get("url", "").lower()
        if any(p in url for p in ("/api", "/internal", "/admin", "/staging", "/debug")):
            return "PIVOT"
        return "CONTEXT"
    if category == "hidden_fields":
        return "PIVOT"   # CSRF tokens, user ids, workflow state
    if category == "adsense_ids":
        return "PIVOT"   # same pub ID => same operator
    if category == "verification_tags":
        return "PIVOT"   # same token ID => same operator
    if category == "iframe_sources":
        return "CONTEXT"
    if category == "crypto_addresses":
        return "PIVOT"
    if category == "favicons":
        # Changes over time are a real pivot (Shodan rehash pivot, brand
        # migration tell); stable favicons sit in CONTEXT.
        if item.get("first_seen") != item.get("last_seen"):
            return "PIVOT"
        return "CONTEXT"
    if category == "outgoing_links":
        return "BACKGROUND"
    if category == "linked_documents":
        return "CONTEXT"   # archived PDFs / docs. occasionally leak content
    if category == "hosting":
        return "CONTEXT"
    if category == "meta_info":
        return "CONTEXT"
    if category == "html_comments":
        return "BACKGROUND"
    if category == "http_headers":
        t = item.get("type", "")
        if t in ("server", "x_powered_by", "aspnet_version",
                 "aspnetmvc_version", "generator_hdr", "via", "x_served_by",
                 "backend"):
            return "CONTEXT"
        return "BACKGROUND"
    if category == "organizations" or category == "addresses":
        return "CONTEXT"
    if category == "bug_bounty_programs":
        # Discovery of an org's public disclosure program is a strong
        # OSINT pivot. points at an actively-managed scope.
        return "PIVOT"
    if category == "captcha_providers":
        # The CAPTCHA stack is part of the security fingerprint; the
        # site key cross-correlates accounts but isn't itself a leak.
        return "CONTEXT"
    if category == "status_pages":
        # Status pages are PIVOT-grade. incident history + component
        # names often map to internal infra.
        return "PIVOT"
    if category == "job_boards":
        # ATS tenants pivot hard: hiring activity + job pages often
        # leak hiring-manager names + internal team structure.
        return "PIVOT"
    if category == "auth_providers":
        # IdP / SSO tenant identifier is a strong infrastructure
        # fingerprint and cluster pivot (multi-product orgs share IdP).
        return "PIVOT"
    return None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
