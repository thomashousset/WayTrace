"""Analysis pipeline: load pages from DB, run extractor, store findings."""
from __future__ import annotations

import asyncio
import json
import zlib

import aiosqlite
from fastapi import APIRouter, HTTPException
from loguru import logger

from config import settings
from db import get_db
from services.extractor import compute_highlights, extract_page_safe
from services.extractor.dirlist_extract import detect_directory_listing
from services.extractor.finalize import ALL_CATEGORIES, finalize_accum
from services.extractor.helpers import ts_to_month, update_entity
from services.extractor.http_headers_extract import extract_http_headers
from services.extractor.jwt_extract import extract_jwts

router = APIRouter(prefix="/api", tags=["analyze"])

# Per-domain lock: two concurrent POST /api/analyze/{id} requests for the
# same domain would race on 'DELETE FROM findings' + re-insertion and could
# leave partial rows from one request while the other is mid-insert.
_analysis_locks: dict[int, asyncio.Lock] = {}
_analysis_locks_guard = asyncio.Lock()


async def _get_analysis_lock(domain_id: int) -> asyncio.Lock:
    async with _analysis_locks_guard:
        lock = _analysis_locks.get(domain_id)
        if lock is None:
            lock = asyncio.Lock()
            _analysis_locks[domain_id] = lock
        return lock


# Pages bigger than this contribute almost nothing extra to OSINT - a
# 2 MB blog post with embedded base64 images yields the same emails and
# links as its first 800 KB - so we truncate before extraction. The
# stored HTML is left untouched.
_MAX_HTML_BYTES_FOR_ANALYSIS = 800_000

# How often to push a progress update to crawl_state during the analyze
# loop, in pages. Keeps the UI bar moving instead of frozen at 92 %.
_ANALYZE_PROGRESS_EVERY = 25


async def _bump_analyze_progress(db_path: str, domain_id: int, done: int, total: int) -> None:
    """Best-effort progress update. never blocks analyze on DB hiccups."""
    try:
        # Map analyze progress into the 92-99 % band so we don't fight
        # the collect pipeline's own 0-92 % calculation.
        pct = 92 + min(7, int(7 * done / max(total, 1)))
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE crawl_state SET progress = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
                "WHERE domain_id = ?",
                (pct, domain_id),
            )
            await db.commit()
    except Exception as exc:
        logger.debug("Analyze progress update failed: {}", exc)


async def run_analysis(
    domain_id: int,
    db_path: str,
    categories: list[str] | None = None,
) -> dict:
    """Load downloaded pages from DB, run OSINT extraction, store findings.

    Returns a dict keyed by category with lists of extracted entities.
    Re-running is safe ; previous findings for the domain are deleted first.

    *categories*, when provided, is a whitelist of known category names
    the caller wants persisted. We validate defensively here because this
    set is interpolated into a DELETE statement downstream. a new caller
    passing arbitrary strings must not reach the DB even if the route-
    level validation changes in the future.
    """
    if categories is not None:
        valid = set(ALL_CATEGORIES)
        bad = [c for c in categories if c not in valid]
        if bad:
            raise ValueError(f"Unknown categories: {bad}")

    # Fetch domain name
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT name FROM domains WHERE id = ?", (domain_id,))
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Domain {domain_id} not found")
        domain = row[0]

        # Load all downloaded pages
        cursor = await db.execute(
            """SELECT p.id, p.html, s.url, s.timestamp, s.source, s.source_url,
                      p.response_headers
               FROM pages p
               JOIN snapshots s ON s.id = p.snapshot_id
               WHERE s.domain_id = ? AND p.status = 'done' AND p.html IS NOT NULL""",
            (domain_id,),
        )
        rows = await cursor.fetchall()

    pages = []
    page_ids = []  # parallel list: page_id for each page
    page_source_urls = {}  # page_id -> viewable URL (archive.org or local viewer)
    for page_id, html_blob, url, timestamp, source, source_url, headers_json in rows:
        if html_blob is None:
            continue
        try:
            html = zlib.decompress(html_blob).decode("utf-8", errors="replace")
        except (zlib.error, TypeError):
            # Fallback path for rows that were stored uncompressed.
            try:
                html = html_blob.decode("utf-8", errors="replace")
            except (AttributeError, UnicodeDecodeError):
                continue
        try:
            response_headers = json.loads(headers_json) if headers_json else None
        except (json.JSONDecodeError, TypeError):
            response_headers = None
        pages.append({
            "html": html,
            "url": url,
            "timestamp": timestamp,
            "response_headers": response_headers,
        })
        page_ids.append(page_id)
        # Build viewable URL: archive.org link (we only collect from archive.org now)
        if source_url:
            page_source_urls[page_id] = source_url
        else:
            page_source_urls[page_id] = f"https://web.archive.org/web/{timestamp}/{url}"

    logger.info(
        "Analyzing {} pages for domain_id={} ({})", len(pages), domain_id, domain
    )

    # Track which page first introduced each finding (category:value -> page_id)
    accum = {cat: {} for cat in ALL_CATEGORIES}
    cat_set = set(categories) if categories else None
    source_map = {}  # "category:value_key" -> page_id

    total_pages = len(pages)
    for idx, (page, pid) in enumerate(zip(pages, page_ids)):
        if page["html"] is None:
            continue
        # Page-size cap: archived blog posts with embedded base64 images
        # routinely hit several megabytes of HTML, and the marginal
        # OSINT yield past ~800 KB is negligible. Truncate here so the
        # extractor's per-pattern regex passes don't blow up.
        html_for_analysis = page["html"]
        if len(html_for_analysis) > _MAX_HTML_BYTES_FOR_ANALYSIS:
            html_for_analysis = html_for_analysis[:_MAX_HTML_BYTES_FOR_ANALYSIS]
        # Snapshot only the LENGTH of each category's accum before
        # extraction. update_entity is the only writer and never re-
        # inserts existing keys, so keys appended after this length are
        # exactly the new ones for this page. Replaces a per-page
        # `set(accum[cat].keys())` snapshot which was O(N) in accum
        # size. and accum grows to 10 000+ entries on big scans, so
        # the snapshot alone dominated wall-clock on 327-page runs.
        counts_before = {cat: len(accum[cat]) for cat in ALL_CATEGORIES}

        extract_page_safe(html_for_analysis, page["url"], page["timestamp"], domain, accum, categories=cat_set)

        # JWT + dirlist
        if cat_set is None or "jwt_tokens" in cat_set:
            for jwt_info in extract_jwts(html_for_analysis, page["url"], page["timestamp"]):
                month = ts_to_month(page["timestamp"])
                update_entity(accum["jwt_tokens"], jwt_info["token"], month, {
                    "token": jwt_info["token"][:50] + "..." if len(jwt_info["token"]) > 50 else jwt_info["token"],
                    "claims": jwt_info["claims"],
                    "sensitive_claims": list(jwt_info["sensitive_claims"].keys()),
                    "source": jwt_info["source"],
                })
        if cat_set is None or "directory_listings" in cat_set:
            dirlist = detect_directory_listing(html_for_analysis, page["url"], page["timestamp"])
            if dirlist:
                month = ts_to_month(page["timestamp"])
                update_entity(accum["directory_listings"], dirlist["path"], month, {
                    "path": dirlist["path"], "server_type": dirlist["server_type"], "url": dirlist["url"],
                })

        # HTTP response headers
        if cat_set is None or "http_headers" in cat_set:
            headers = page.get("response_headers")
            if headers:
                month = ts_to_month(page["timestamp"])
                for hdr_finding in extract_http_headers(headers):
                    key = f"{hdr_finding['type']}:{hdr_finding['value'].lower()}"
                    update_entity(accum["http_headers"], key, month, hdr_finding)

        # Periodic progress nudge so the UI's progress bar moves while
        # the analyze loop chews through hundreds of pages.
        if total_pages > 50 and (idx + 1) % _ANALYZE_PROGRESS_EVERY == 0:
            await _bump_analyze_progress(db_path, domain_id, idx + 1, total_pages)

        # Record new keys -> this page_id, using the dict insertion-
        # order property (Python 3.7+): keys appended during this page
        # sit at indices >= counts_before[cat]. Cheaper than rebuilding
        # the full key set after each page.
        for cat in ALL_CATEGORIES:
            cat_dict = accum[cat]
            before = counts_before.get(cat, 0)
            if len(cat_dict) <= before:
                continue
            # Slice the new keys via list(). only touches the tail.
            new_keys = list(cat_dict.keys())[before:]
            for k in new_keys:
                source_map[f"{cat}:{k}"] = pid
                try:
                    display = _item_value(cat, accum[cat][k])
                    if isinstance(display, str) and display:
                        source_map[f"{cat}:{display.lower()}"] = pid
                except (KeyError, TypeError, AttributeError) as exc:
                    logger.debug("Skipping malformed accum entry {}/{}: {}", cat, k, exc)

    results = finalize_accum(accum, categories=categories)
    results["highlights"] = compute_highlights(results, domain)

    # Store findings with source_page_id
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")

        if categories is None:
            await db.execute("DELETE FROM findings WHERE domain_id = ?", (domain_id,))
        else:
            placeholders = ",".join("?" * len(categories))
            await db.execute(
                f"DELETE FROM findings WHERE domain_id = ? AND category IN ({placeholders})",
                [domain_id] + list(categories),
            )

        for category, items in results.items():
            if category == "highlights":
                continue
            if not items:
                continue
            for item in items:
                value = _item_value(category, item)
                if value is None:
                    continue
                # Find the source page for this finding
                # Try matching by the accumulator key (lowercase value)
                accum_key = value.lower() if isinstance(value, str) else str(value)
                spid = source_map.get(f"{category}:{accum_key}")
                metadata = json.dumps({k: v for k, v in item.items() if k not in ("value",)})
                if spid:
                    meta_dict = json.loads(metadata)
                    meta_dict["source_page_id"] = spid
                    meta_dict["source_url"] = page_source_urls.get(spid, "")
                    metadata = json.dumps(meta_dict)
                first_seen = item.get("first_seen")
                last_seen = item.get("last_seen")
                occurrences = item.get("occurrences", 1)
                severity = _item_severity(category, item)

                await db.execute(
                    """INSERT OR REPLACE INTO findings
                       (domain_id, category, value, metadata, first_seen, last_seen,
                        occurrences, severity, source_page_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (domain_id, category, value, metadata,
                     first_seen, last_seen, occurrences, severity, spid),
                )

        await db.commit()

    logger.info("Stored findings for domain_id={}", domain_id)
    return results


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

@router.post("/analyze/{domain_id}")
async def trigger_analysis(domain_id: int):
    """Run analysis for a domain and store findings."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute("SELECT name FROM domains WHERE id = ?", (domain_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Domain not found")

    lock = await _get_analysis_lock(domain_id)
    async with lock:
        try:
            results = await run_analysis(domain_id, db_path)
        except Exception as exc:
            # Keep the public surface generic. details go to server logs.
            # str(exc) could leak DB paths, filesystem layout, or internal
            # field names if something unexpected raised.
            logger.error("Analysis failed for domain_id={}: {!r}", domain_id, exc)
            try:
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        "UPDATE crawl_state SET status = 'failed', "
                        "updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
                        "WHERE domain_id = ?",
                        (domain_id,),
                    )
                    await db.commit()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Analysis failed")

    # Flip crawl_state to a terminal "done" so the UI stops polling.
    # Standalone analyze does not go through the collect orchestrator
    # that normally owns this transition.
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE crawl_state SET phase = 'done', status = 'done', "
                "progress = 100, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
                "WHERE domain_id = ?",
                (domain_id,),
            )
            await db.commit()
    except Exception as exc:
        logger.debug("Could not flip crawl_state to done for {}: {}", domain_id, exc)

    summary = {cat: len(items) for cat, items in results.items()}
    return {"domain_id": domain_id, "findings_by_category": summary}
