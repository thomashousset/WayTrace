"""Finding value formatting helper.

`_item_value` maps a raw extractor item to its canonical display value, used by
the CSV export in routers/public.py. (The legacy DB-backed analysis pipeline and
the OSINT-value severity classifier that used to live here were removed once the
report became provenance-first and neutral.)
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

