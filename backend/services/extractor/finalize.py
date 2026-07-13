"""Accumulator finalization and high-level extract_all entry point."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from loguru import logger

from .extract import extract_page
from .highlights import compute_highlights
from .jwt_extract import extract_jwts
from .dirlist_extract import detect_directory_listing
from .helpers import ts_to_month, update_entity
from .patterns import SOCIAL_PATTERNS

ALL_CATEGORIES = [
    "emails", "subdomains", "api_keys", "cloud_buckets",
    "analytics_trackers", "endpoints", "assets", "social_profiles",
    "technologies", "persons", "phones",
    "organizations", "addresses", "linked_documents",
    "html_comments", "meta_info", "html_titles",
    "jwt_tokens", "directory_listings",
    "hidden_fields", "internal_ips", "adsense_ids",
    "verification_tags", "iframe_sources", "js_urls",
    "connection_strings",
    "crypto_addresses", "favicons", "outgoing_links", "hosting",
    "http_headers", "french_business_ids",
    "analytics_ids", "cookie_consent", "rss_feeds",
    "github_repos", "sitemaps_and_robots", "pgp_keys",
    "bug_bounty_programs", "captcha_providers", "status_pages",
    "job_boards", "auth_providers",
]


def extract_page_safe(
    html: str, url: str, timestamp: str, domain: str, accum: dict,
    categories: set[str] | None = None,
    response_headers: dict | None = None,
) -> bool:
    """Extract data from a single page, returning True on success."""
    try:
        extract_page(
            html, url, timestamp, domain, accum,
            categories=categories,
            response_headers=response_headers,
        )
        return True
    except Exception as exc:
        logger.warning("Extraction error on {} ({}): {}", url, type(exc).__name__, exc)
        return False


def _handle_from_social_url(url: str) -> str:
    """First path segment of a social URL, used as the profile handle."""
    try:
        after = url.split("://", 1)[-1]
        path = after.split("/", 1)[1] if "/" in after else ""
        return path.strip("/").split("/")[0].split("?")[0]
    except (IndexError, AttributeError):
        return ""


def _reconcile_social_outgoing(accum: dict) -> None:
    """De-duplicate social links between Outgoing links and Social profiles.

    Social profiles is the single source of truth for social platforms.
      - A social outgoing link already recognised as a profile (caught by
        SOCIAL_PATTERNS) is removed from outgoing_links: it is not repeated.
      - A social outgoing link on a platform SOCIAL_PATTERNS does not cover
        (e.g. pinterest, reddit) is promoted into social_profiles and removed
        from outgoing so it still surfaces under Social profiles.
      - A social-domain link that is a non-profile route (github.com/features,
        a facebook pixel, ...) is left in outgoing untouched: SOCIAL_PATTERNS
        deliberately filtered it out of Social profiles, so it is not a profile.
    """
    covered = set(SOCIAL_PATTERNS)
    outgoing = accum.get("outgoing_links") or {}
    social = accum.setdefault("social_profiles", {})
    drop: list[str] = []
    for url_key, entry in outgoing.items():
        if entry.get("category") != "social":
            continue
        service = entry.get("service") or "social"
        url = entry.get("url", url_key)
        handle = _handle_from_social_url(url)
        skey = f"{service}:{handle.lower()}" if handle else f"{service}:{url.lower()}"
        if skey in social:
            drop.append(url_key)  # already a known profile, dedup out of outgoing
        elif service not in covered and handle:
            social[skey] = {
                "first_seen": entry.get("first_seen"),
                "last_seen": entry.get("last_seen"),
                "occurrences": entry.get("occurrences", 1),
                "platform": service,
                "handle": handle,
                "url": url,
            }
            drop.append(url_key)
        # else: covered platform but not a profile (filtered) -> keep in outgoing
    for k in drop:
        outgoing.pop(k, None)


def finalize_accum(accum: dict, categories: list[str] | None = None) -> dict:
    """Convert accumulator dicts to sorted result lists.

    If *categories* is provided, only included categories are populated;
    excluded categories return empty lists.
    """
    _reconcile_social_outgoing(accum)

    def _sort_list(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda x: x["occurrences"], reverse=True)

    def _cat(key: str, items: list[dict]) -> list[dict]:
        if categories is not None and key not in categories:
            return []
        return _sort_list(items)

    result = {
        "emails": _cat("emails",
            [{"value": e.pop("value", k), **e} for k, e in accum["emails"].items()]
        ),
        "subdomains": _cat("subdomains",
            [{"value": e.pop("value", k), **e} for k, e in accum["subdomains"].items()]
        ),
        "api_keys": _cat("api_keys",
            [
                {"type": e.pop("type", ""), "value": e.pop("value", k), **e}
                for k, e in accum["api_keys"].items()
            ]
        ),
        "cloud_buckets": _cat("cloud_buckets",
            [{"value": e.pop("value", k), **e} for k, e in accum["cloud_buckets"].items()]
        ),
        "analytics_trackers": _cat("analytics_trackers",
            [
                {"type": e.pop("type", ""), "id": e.pop("id", k), **e}
                for k, e in accum["analytics_trackers"].items()
            ]
        ),
        "endpoints": _cat("endpoints",
            [{"path": e.pop("path", k), **e} for k, e in accum["endpoints"].items()]
        ),
        "social_profiles": _cat("social_profiles",
            [
                {
                    "platform": e.pop("platform", ""),
                    "handle": e.pop("handle", ""),
                    "url": e.pop("url", ""),
                    **e,
                }
                for k, e in accum["social_profiles"].items()
            ]
        ),
        "technologies": _cat("technologies",
            [
                {
                    "technology": e.pop("technology", k),
                    "version": e.pop("version", None),
                    **e,
                }
                for k, e in accum["technologies"].items()
            ]
        ),
        "persons": _cat("persons",
            [
                {"name": e.pop("name", k), "context": e.pop("context", ""), **e}
                for k, e in accum["persons"].items()
            ]
        ),
        "phones": _cat("phones",
            [
                {"raw": e.pop("raw", ""), "normalized": e.pop("normalized", k), **e}
                for k, e in accum["phones"].items()
            ]
        ),
        "jwt_tokens": _cat("jwt_tokens",
            [
                {
                    "token": e.pop("token", k),
                    "claims": e.pop("claims", {}),
                    "sensitive_claims": e.pop("sensitive_claims", []),
                    "source": e.pop("source", ""),
                    **e,
                }
                for k, e in accum["jwt_tokens"].items()
            ]
        ),
        "directory_listings": _cat("directory_listings",
            [
                {
                    "path": e.pop("path", k),
                    "server_type": e.pop("server_type", ""),
                    "url": e.pop("url", ""),
                    **e,
                }
                for k, e in accum["directory_listings"].items()
            ]
        ),
        "organizations": _cat("organizations",
            [
                {
                    "name": e.pop("name", k),
                    "type": e.pop("type", ""),
                    "url": e.pop("url", ""),
                    **e,
                }
                for k, e in accum.get("organizations", {}).items()
            ]
        ),
        "addresses": _cat("addresses",
            [
                {
                    "value": k,
                    "street": e.pop("street", ""),
                    "city": e.pop("city", ""),
                    "postal_code": e.pop("postal_code", ""),
                    "country": e.pop("country", ""),
                    **e,
                }
                for k, e in accum.get("addresses", {}).items()
            ]
        ),
        "linked_documents": _cat("linked_documents",
            [
                {"url": e.pop("url", k), "extension": e.pop("extension", ""), **e}
                for k, e in accum.get("linked_documents", {}).items()
            ]
        ),
        "html_comments": _cat("html_comments",
            [
                {"comment": e.pop("comment", k), **e}
                for k, e in accum.get("html_comments", {}).items()
            ]
        ),
        "meta_info": _cat("meta_info",
            [
                {"property": e.pop("property", ""), "content": e.pop("content", k), **e}
                for k, e in accum.get("meta_info", {}).items()
            ]
        ),
        "html_titles": _cat("html_titles",
            [
                {"property": e.pop("property", "title"), "content": e.pop("content", k), **e}
                for k, e in accum.get("html_titles", {}).items()
            ]
        ),
        "hidden_fields": _cat("hidden_fields",
            [
                {
                    "name": e.pop("name", ""),
                    "value": e.pop("value", k),
                    "form_action": e.pop("form_action", ""),
                    **e,
                }
                for k, e in accum.get("hidden_fields", {}).items()
            ]
        ),
        "internal_ips": _cat("internal_ips",
            [
                {"ip": e.pop("ip", k), "context": e.pop("context", ""), **e}
                for k, e in accum.get("internal_ips", {}).items()
            ]
        ),
        "adsense_ids": _cat("adsense_ids",
            [
                {"type": e.pop("type", ""), "id": e.pop("id", k), **e}
                for k, e in accum.get("adsense_ids", {}).items()
            ]
        ),
        "verification_tags": _cat("verification_tags",
            [
                {
                    "service": e.pop("service", ""),
                    "verification_id": e.pop("verification_id", k),
                    **e,
                }
                for k, e in accum.get("verification_tags", {}).items()
            ]
        ),
        "iframe_sources": _cat("iframe_sources",
            [
                {
                    "url": e.pop("url", k),
                    "service": e.pop("service", ""),
                    "domain": e.pop("domain", ""),
                    **e,
                }
                for k, e in accum.get("iframe_sources", {}).items()
            ]
        ),
        "js_urls": _cat("js_urls",
            [
                {"url": e.pop("url", k), "context": e.pop("context", ""), **e}
                for k, e in accum.get("js_urls", {}).items()
            ]
        ),
        "connection_strings": _cat("connection_strings",
            [
                {
                    "type": e.pop("type", ""),
                    "value": e.pop("value", k),
                    "has_credentials": e.pop("has_credentials", False),
                    **e,
                }
                for k, e in accum.get("connection_strings", {}).items()
            ]
        ),
        "crypto_addresses": _cat("crypto_addresses",
            [
                {
                    "type": e.pop("type", ""),
                    "address": e.pop("address", k),
                    "validated": e.pop("validated", False),
                    "validation_method": e.pop("validation_method", ""),
                    **e,
                }
                for k, e in accum.get("crypto_addresses", {}).items()
            ]
        ),
        "favicons": _cat("favicons",
            [
                {
                    "url": e.pop("url", k),
                    "type": e.pop("type", ""),
                    "sizes": e.pop("sizes", None),
                    **e,
                }
                for k, e in accum.get("favicons", {}).items()
            ]
        ),
        "outgoing_links": _cat("outgoing_links",
            [
                {
                    "url": e.pop("url", k),
                    "domain": e.pop("domain", ""),
                    "category": e.pop("category", ""),
                    "service": e.pop("service", ""),
                    **e,
                }
                for k, e in accum.get("outgoing_links", {}).items()
            ]
        ),
        "hosting": _cat("hosting",
            [
                {"provider": e.pop("provider", k), "signal": e.pop("signal", ""), **e}
                for k, e in accum.get("hosting", {}).items()
            ]
        ),
        "http_headers": _cat("http_headers",
            [
                {
                    "type": e.pop("type", ""),
                    "header": e.pop("header", ""),
                    "value": e.pop("value", k),
                    **e,
                }
                for k, e in accum.get("http_headers", {}).items()
            ]
        ),
        "french_business_ids": _cat("french_business_ids",
            [
                {
                    "type": e.pop("type", ""),
                    "value": e.pop("value", k),
                    "raw": e.pop("raw", ""),
                    "validated": e.pop("validated", False),
                    **e,
                }
                for k, e in accum.get("french_business_ids", {}).items()
            ]
        ),
        "assets": _cat("assets",
            [
                {"path": e.pop("path", k), "type": e.pop("type", ""), **e}
                for k, e in accum.get("assets", {}).items()
            ]
        ),
        "analytics_ids": _cat("analytics_ids",
            [
                {
                    "platform": e.pop("platform", ""),
                    "id_value": e.pop("id_value", k),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("analytics_ids", {}).items()
            ]
        ),
        "cookie_consent": _cat("cookie_consent",
            [
                {
                    "platform": e.pop("platform", k),
                    "account_id": e.pop("account_id", ""),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("cookie_consent", {}).items()
            ]
        ),
        "rss_feeds": _cat("rss_feeds",
            [
                {
                    "url": e.pop("url", k),
                    "feed_type": e.pop("feed_type", ""),
                    "title": e.pop("title", ""),
                    **e,
                }
                for k, e in accum.get("rss_feeds", {}).items()
            ]
        ),
        "github_repos": _cat("github_repos",
            [
                {
                    "owner": e.pop("owner", ""),
                    "repo": e.pop("repo", ""),
                    "raw_url": e.pop("raw_url", ""),
                    "pivot_url": e.pop("pivot_url", k),
                    **e,
                }
                for k, e in accum.get("github_repos", {}).items()
            ]
        ),
        "sitemaps_and_robots": _cat("sitemaps_and_robots",
            [
                {"url": e.pop("url", k), "kind": e.pop("kind", ""), **e}
                for k, e in accum.get("sitemaps_and_robots", {}).items()
            ]
        ),
        "pgp_keys": _cat("pgp_keys",
            [
                {
                    "kind": e.pop("kind", ""),
                    "identifier": e.pop("identifier", k),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("pgp_keys", {}).items()
            ]
        ),
        "bug_bounty_programs": _cat("bug_bounty_programs",
            [
                {
                    "platform": e.pop("platform", ""),
                    "handle": e.pop("handle", ""),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("bug_bounty_programs", {}).items()
            ]
        ),
        "captcha_providers": _cat("captcha_providers",
            [
                {
                    "provider": e.pop("provider", ""),
                    "sitekey": e.pop("sitekey", ""),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("captcha_providers", {}).items()
            ]
        ),
        "status_pages": _cat("status_pages",
            [
                {
                    "provider": e.pop("provider", ""),
                    "slug": e.pop("slug", ""),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("status_pages", {}).items()
            ]
        ),
        "job_boards": _cat("job_boards",
            [
                {
                    "platform": e.pop("platform", ""),
                    "slug": e.pop("slug", ""),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("job_boards", {}).items()
            ]
        ),
        "auth_providers": _cat("auth_providers",
            [
                {
                    "platform": e.pop("platform", ""),
                    "tenant": e.pop("tenant", ""),
                    "pivot_url": e.pop("pivot_url", ""),
                    **e,
                }
                for k, e in accum.get("auth_providers", {}).items()
            ]
        ),
    }

    return result


def merge_analytics_ids(result: dict) -> None:
    """Fold result['analytics_ids'] into result['analytics_trackers'] in place,
    deduping by normalized ID and emptying analytics_ids.

    Called once on the assembled scan results (not inside extract_all) so the
    two extractors stay independently testable while the UI sees a single
    deduped 'Analytics & trackers' category. The two routinely surface the same
    value (gtm:GTM-X vs GTM-X), which reads as noise."""
    ids = result.get("analytics_ids") or []
    if not ids:
        return
    trackers = list(result.get("analytics_trackers") or [])

    def _norm(s: str) -> str:
        return (s or "").strip().upper()

    by_id: dict[str, dict] = {}
    for t in trackers:
        by_id.setdefault(_norm(t.get("id")), t)

    for a in ids:
        nid = _norm(a.get("id_value"))
        existing = by_id.get(nid)
        if existing is not None:
            if not existing.get("pivot_url") and a.get("pivot_url"):
                existing["pivot_url"] = a["pivot_url"]
            if not existing.get("platform") and a.get("platform"):
                existing["platform"] = a["platform"]
            fs, ls = a.get("first_seen"), a.get("last_seen")
            if fs and (not existing.get("first_seen") or fs < existing["first_seen"]):
                existing["first_seen"] = fs
            if ls and (not existing.get("last_seen") or ls > existing["last_seen"]):
                existing["last_seen"] = ls
            existing["occurrences"] = max(
                existing.get("occurrences", 1), a.get("occurrences", 1)
            )
        else:
            item = {"type": a.get("platform", ""), "id": a.get("id_value", ""),
                    "pivot_url": a.get("pivot_url", "")}
            for kk in ("first_seen", "last_seen", "occurrences",
                       "source_url", "source_page_id", "platform"):
                if kk in a:
                    item[kk] = a[kk]
            by_id[nid] = item
            trackers.append(item)

    result["analytics_trackers"] = sorted(
        trackers, key=lambda x: x.get("occurrences", 1), reverse=True
    )
    result["analytics_ids"] = []


def _mine_subdomains_from_snapshot_urls(
    pages: list[dict], domain: str, accum: dict
) -> None:
    """Mine subdomain hostnames from the CDX snapshot URLs themselves.

    Pages whose scrape failed (``html`` is ``None``) never enter the
    per-page extractor, so any subdomain that only appears in the original
    archived URL would be lost. even though CDX itself proves that host
    existed historically. We sweep ``pages`` once at finalize time and
    record every hostname ending in ``.<domain>`` (apex and ``www.``
    excluded) with provenance ``cdx-url``.
    """
    apex = domain.lower()
    www = f"www.{apex}"
    suffix = "." + apex
    for page in pages:
        url = page.get("url") or ""
        ts = page.get("timestamp") or ""
        if not url or not ts:
            continue
        try:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
        except ValueError:
            continue
        if not host or host == apex or host == www or not host.endswith(suffix):
            continue
        # ASCII-LDH guard. same shape used by _record_subdomain. Punycoded
        # hosts (xn--…) are plain ASCII so they remain valid.
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            continue
        label = host.split(".", 1)[0]
        if not label or label.startswith("-") or label.endswith("-"):
            continue
        try:
            month = ts_to_month(ts)
        except Exception:
            continue
        # update_entity counts every call as one occurrence. the CDX
        # layer already deduplicates per (url, timestamp), so the rank
        # reflects how many archived snapshots mention each subdomain.
        update_entity(
            accum["subdomains"], host, month,
            {"value": host, "source": "cdx-url"},
        )


def new_accum() -> dict:
    """A fresh, empty accumulator (one dict per category)."""
    return {cat: {} for cat in ALL_CATEGORIES}


def mine_subdomains(pages: list[dict], domain: str, accum: dict, cat_set: set | None) -> None:
    """Mine subdomains from the CDX URLs up front. Runs even when pages have
    html=None (a failed scrape still proves the host existed)."""
    if cat_set is None or "subdomains" in cat_set:
        _mine_subdomains_from_snapshot_urls(pages, domain, accum)


def process_page(page: dict, domain: str, accum: dict, cat_set: set | None,
                 page_seq: dict) -> bool:
    """Extract ONE page into the shared accumulator (incremental). Same work the
    extract_all loop does per page: category extraction, JWTs, directory
    listings, and stamping source-page provenance onto newly-introduced values.
    Returns True if the page contributed (html present and parsed)."""
    if page["html"] is None:
        return False
    counts_before = {cat: len(accum[cat]) for cat in ALL_CATEGORIES}
    processed = extract_page_safe(
        page["html"], page["url"], page["timestamp"], domain, accum,
        categories=cat_set,
        response_headers=page.get("response_headers"),
    )

    # JWT extraction (searches both URL and HTML)
    if cat_set is None or "jwt_tokens" in cat_set:
        for jwt_info in extract_jwts(page["html"], page["url"], page["timestamp"]):
            token = jwt_info["token"]
            month = ts_to_month(page["timestamp"])
            update_entity(
                accum["jwt_tokens"], token, month,
                {
                    "token": token[:50] + "..." if len(token) > 50 else token,
                    "claims": jwt_info["claims"],
                    "sensitive_claims": list(jwt_info["sensitive_claims"].keys()),
                    "source": jwt_info["source"],
                },
            )

    # Directory listing detection
    if cat_set is None or "directory_listings" in cat_set:
        dirlist = detect_directory_listing(page["html"], page["url"], page["timestamp"])
        if dirlist:
            month = ts_to_month(page["timestamp"])
            update_entity(
                accum["directory_listings"], dirlist["path"], month,
                {
                    "path": dirlist["path"],
                    "server_type": dirlist["server_type"],
                    "url": dirlist["url"],
                },
            )

    # Stamp the page provenance onto the keys this page introduced.
    src_url = page.get("source_url") or (
        f"https://web.archive.org/web/{page['timestamp']}/{page['url']}"
    )
    pid = page_seq.setdefault(src_url, len(page_seq) + 1)
    for cat in ALL_CATEGORIES:
        cat_dict = accum[cat]
        before = counts_before.get(cat, 0)
        if len(cat_dict) <= before:
            continue
        for k in list(cat_dict.keys())[before:]:
            entry = cat_dict[k]
            if isinstance(entry, dict) and "source_url" not in entry:
                entry["source_url"] = src_url
                entry["source_page_id"] = pid
    return bool(processed)


def extract_all(pages: list[dict], domain: str, categories: list[str] | None = None) -> dict:
    accum = new_accum()
    cat_set = set(categories) if categories else None
    mine_subdomains(pages, domain, accum, cat_set)
    # Source-page provenance: update_entity never rewrites an existing key, so a
    # per-page diff of new accum keys is exactly the page that introduced each
    # value (see process_page).
    page_seq: dict[str, int] = {}
    processed = 0
    for page in pages:
        if process_page(page, domain, accum, cat_set, page_seq):
            processed += 1
    logger.info("Extracted data from {} pages for {}", processed, domain)
    return finalize_accum(accum, categories=categories)
