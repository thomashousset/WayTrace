# backend/services/collector.py
"""Collection pipeline: CDX crawl, HTML download, backup-file discovery."""
from __future__ import annotations

import asyncio
import json
import time
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
import aiosqlite
from loguru import logger

from services.cdx import build_cdx_params, detect_and_strip_resume_key, parse_cdx_rows
from services.rate_limiter import RateLimiter

CDX_API = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE = "https://web.archive.org/web"

# Polite identification ; archive.org blocks anonymous crawlers
USER_AGENT = "WayTrace/2.0 (OSINT research tool; +https://github.com/HXLLO/WayTrace)"

# Max consecutive 429 responses before giving up on a phase
MAX_429_RETRIES = 5

# Path to extension list relative to this file's package root
_EXTENSIONS_FILE = Path(__file__).parent.parent / "data" / "extensions.txt"


def _load_extensions_sync(path: Path = _EXTENSIONS_FILE) -> list[str]:
    """Load backup-file extensions from a text file, ignoring blank lines and comments."""
    exts: list[str] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                exts.append(line)
    return exts


async def _load_extensions(path: Path = _EXTENSIONS_FILE) -> list[str]:
    return await asyncio.to_thread(_load_extensions_sync, path)


# Depth-driven caps on the CDX phase. The download cap is decided after
# CDX returns (in select_snapshots_in_db). Without an upper bound here a
# popular domain can return hundreds of thousands of snapshots before
# pagination terminates - most of which the selection step would discard
# anyway. Each depth is mapped to a snapshot budget and a wall-clock
# budget; whichever trips first stops pagination.
_CDX_DEPTH_BUDGETS: dict[str, dict[str, int]] = {
    "quick":    {"max_snapshots": 5_000,   "deadline_seconds": 90},
    "standard": {"max_snapshots": 20_000,  "deadline_seconds": 180},
    "full":     {"max_snapshots": 60_000,  "deadline_seconds": 300},
    "max":      {"max_snapshots": 250_000, "deadline_seconds": 600},
}


def cdx_budget_for_depth(depth: str) -> tuple[int, int]:
    """Return (max_snapshots, deadline_seconds) for a depth preset."""
    cfg = _CDX_DEPTH_BUDGETS.get(depth) or _CDX_DEPTH_BUDGETS["standard"]
    return cfg["max_snapshots"], cfg["deadline_seconds"]


async def crawl_cdx(
    session: Any,
    domain: str,
    db_path: str,
    rate_limiter: RateLimiter | None = None,
    limit: int = 10000,
    depth: str = "standard",
    max_snapshots: int | None = None,
    deadline_seconds: int | None = None,
) -> dict:
    """Query CDX API with wildcard subdomains and store snapshots in SQLite.

    *depth* selects the snapshot/wall-clock budget; *max_snapshots* and
    *deadline_seconds* override the preset for advanced callers / tests.
    Pagination stops when either budget is exhausted, or when archive.org
    runs out of resume keys. Returns a dict with ``snapshots_indexed``.
    """
    if rate_limiter is None:
        rate_limiter = RateLimiter(initial_delay=0, min_delay=0)

    budget_count, budget_seconds = cdx_budget_for_depth(depth)
    if max_snapshots is None:
        max_snapshots = budget_count
    if deadline_seconds is None:
        deadline_seconds = budget_seconds
    deadline = time.monotonic() + deadline_seconds

    snapshots_indexed = 0
    resume_key: str | None = None
    consecutive_errors = 0
    stop_reason: str | None = None
    max_pages = 50  # max CDX pagination pages to avoid infinite loops

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            "INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,)
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM domains WHERE name = ?", (domain,))
        row = await cursor.fetchone()
        domain_id: int = row[0]

        page_num = 0
        while page_num < max_pages:
            # Wall-clock budget: stop before issuing a fresh request if we
            # already overshot, regardless of remaining pages.
            if time.monotonic() > deadline:
                stop_reason = (
                    f"deadline_{deadline_seconds}s_after_{snapshots_indexed}_snapshots"
                )
                logger.warning(
                    "CDX deadline reached for {} after {}s; stopping with {} snapshots "
                    "(depth={})", domain, deadline_seconds, snapshots_indexed, depth,
                )
                break
            # Snapshot count budget: avoid pulling 250k rows from archive
            # when the eventual download cap (depth=quick) is ~200.
            if snapshots_indexed >= max_snapshots:
                stop_reason = f"snapshot_cap_{max_snapshots}_for_depth_{depth}"
                logger.info(
                    "CDX snapshot cap {} reached for {} (depth={}); stopping pagination",
                    max_snapshots, domain, depth,
                )
                break
            page_num += 1
            page_params = build_cdx_params(domain, limit=5000, resume_key=resume_key)

            try:
                cdx_timeout = aiohttp.ClientTimeout(total=60)
                async with session.get(CDX_API, params=page_params, timeout=cdx_timeout) as resp:
                    # 429 (throttle), 502/503/504 (archive.org edge errors) are
                    # transient. Back off and retry rather than giving up with
                    # zero snapshots (which looks like a silent crash to users).
                    if resp.status in (429, 502, 503, 504):
                        consecutive_errors += 1
                        rate_limiter.on_429()
                        # Exponential-ish backoff capped at 60s.
                        wait_time = min(rate_limiter.pause_429 * (2 ** (consecutive_errors - 1)), 60)
                        logger.warning(
                            "CDX {} for {} (attempt {}/{}), waiting {:.0f}s",
                            resp.status, domain, consecutive_errors, MAX_429_RETRIES, wait_time,
                        )
                        if consecutive_errors >= MAX_429_RETRIES:
                            logger.error("CDX error limit reached for {} after {} retries", domain, consecutive_errors)
                            break
                        await asyncio.sleep(wait_time)
                        continue
                    if resp.status != 200:
                        rate_limiter.on_error()
                        logger.warning("CDX returned {} for {}", resp.status, domain)
                        break

                    raw = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                consecutive_errors += 1
                rate_limiter.on_error()
                logger.warning("CDX request failed for {} (attempt {}/{}): {}", domain, consecutive_errors, MAX_429_RETRIES, repr(exc))
                if consecutive_errors >= MAX_429_RETRIES:
                    break
                await asyncio.sleep(5)
                continue

            consecutive_errors = 0
            rate_limiter.on_success()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.error("Failed to parse CDX response: {}", exc)
                break

            if not data:
                break

            next_resume_key, data = detect_and_strip_resume_key(data)
            rows = parse_cdx_rows(data)

            for snap in rows:
                ts = snap["timestamp"]
                url = snap["url"]
                try:
                    source_url = f"https://web.archive.org/web/{ts}/{url}"
                    await db.execute(
                        """INSERT OR IGNORE INTO snapshots
                           (domain_id, url, timestamp, mimetype, status_code, digest,
                            source, source_url)
                           VALUES (?, ?, ?, ?, ?, ?, 'archive', ?)""",
                        (
                            domain_id, url, ts,
                            snap["mimetype"], snap["status"], snap["digest"],
                            source_url,
                        ),
                    )
                    snapshots_indexed += 1
                except aiosqlite.Error as exc:
                    logger.debug("Skipping snapshot {}: {}", url, exc)

            await db.commit()

            if next_resume_key:
                resume_key = next_resume_key
                await rate_limiter.wait()
            else:
                break

    logger.info(
        "CDX crawl complete for {}: {} snapshots indexed (stop_reason={})",
        domain, snapshots_indexed, stop_reason or "exhausted",
    )
    return {
        "snapshots_indexed": snapshots_indexed,
        "domain_id": domain_id,
        "stop_reason": stop_reason or "exhausted",
        "depth_budget_max_snapshots": max_snapshots,
        "depth_budget_seconds": deadline_seconds,
    }


async def download_pages(
    session: Any,
    domain_id: int,
    db_path: str,
    rate_limiter: RateLimiter | None = None,
    max_concurrent: int = 5,
    on_progress: Any = None,
) -> dict:
    """Download pending HTML pages from Wayback Machine and store compressed HTML.

    Returns a dict with ``pages_downloaded`` and ``pages_failed`` counts.
    """
    if rate_limiter is None:
        rate_limiter = RateLimiter(initial_delay=0, min_delay=0)

    # Collect pending pages
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            """SELECT p.id, s.url, s.timestamp
               FROM pages p
               JOIN snapshots s ON s.id = p.snapshot_id
               WHERE s.domain_id = ? AND p.status = 'pending'""",
            (domain_id,),
        )
        pending = await cursor.fetchall()

    pages_downloaded = 0
    pages_failed = 0
    consecutive_429 = 0
    stop_flag = False
    sem = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(page_id: int, url: str, timestamp: str) -> None:
        nonlocal pages_downloaded, pages_failed, consecutive_429, stop_flag
        if stop_flag:
            return
        wayback_url = f"{WAYBACK_BASE}/{timestamp}id_/{url}"
        async with sem:
            if stop_flag:
                return
            await rate_limiter.wait()
            try:
                req_timeout = aiohttp.ClientTimeout(total=30)
                async with session.get(wayback_url, timeout=req_timeout) as resp:
                    if resp.status == 200:
                        consecutive_429 = 0
                        raw_bytes = await resp.read()
                        html = raw_bytes.decode("utf-8", errors="replace")
                        compressed = zlib.compress(html.encode("utf-8"))
                        # Capture x-archive-orig-* headers. these are the
                        # original upstream response headers that archive.org
                        # preserves. Strip the prefix so downstream code sees
                        # the real header names. Wrapped in try/except so a
                        # misbehaving / mock response doesn't break the fetch.
                        orig_headers: dict[str, str] = {}
                        try:
                            header_items = resp.headers.items()
                            for hname, hval in header_items:
                                if not isinstance(hname, str) or not isinstance(hval, str):
                                    continue
                                key = hname.lower()
                                if key.startswith("x-archive-orig-"):
                                    real_name = key[len("x-archive-orig-"):]
                                    if real_name:
                                        orig_headers[real_name] = hval[:500]
                        except (TypeError, AttributeError):
                            pass
                        headers_json = json.dumps(orig_headers) if orig_headers else None
                        async with aiosqlite.connect(db_path) as db:
                            await db.execute(
                                "UPDATE pages SET html = ?, response_headers = ?, "
                                "status = 'done', "
                                "scraped_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
                                "WHERE id = ?",
                                (compressed, headers_json, page_id),
                            )
                            await db.commit()
                        pages_downloaded += 1
                        rate_limiter.on_success()
                    elif resp.status == 429:
                        consecutive_429 += 1
                        rate_limiter.on_429()
                        logger.warning(
                            "Wayback 429 on page {} (attempt {}/{})",
                            url, consecutive_429, MAX_429_RETRIES,
                        )
                        if consecutive_429 >= MAX_429_RETRIES:
                            logger.error("429 limit reached, stopping download")
                            stop_flag = True
                        else:
                            await rate_limiter.wait_429()
                    else:
                        consecutive_429 = 0
                        async with aiosqlite.connect(db_path) as db:
                            await db.execute(
                                "UPDATE pages SET status = 'failed', "
                                "error = ? WHERE id = ?",
                                (f"HTTP {resp.status}", page_id),
                            )
                            await db.commit()
                        pages_failed += 1
                        rate_limiter.on_error()
            except (aiohttp.ClientError, asyncio.TimeoutError, zlib.error) as exc:
                logger.error("Error downloading page {}: {}", url, repr(exc))
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        "UPDATE pages SET status = 'failed', error = ? WHERE id = ?",
                        (str(exc)[:200], page_id),
                    )
                    await db.commit()
                pages_failed += 1
                rate_limiter.on_error()

    # Process in batches for progress reporting
    batch_size = max(max_concurrent * 5, 20)
    for batch_start in range(0, len(pending), batch_size):
        if stop_flag:
            break
        batch = pending[batch_start:batch_start + batch_size]
        tasks = [_fetch_one(pid, url, ts) for pid, url, ts in batch]
        await asyncio.gather(*tasks)
        logger.info("Download progress: {}/{} done, {} failed", pages_downloaded, len(pending), pages_failed)
        if on_progress:
            await on_progress(pages_downloaded, pages_failed, len(pending))

    logger.info(
        "download_pages for domain_id={}: downloaded={}, failed={}",
        domain_id, pages_downloaded, pages_failed,
    )
    return {"pages_downloaded": pages_downloaded, "pages_failed": pages_failed}


async def discover_backup_files(domain_id: int, db_path: str) -> int:
    """Filter CDX snapshots by known backup/sensitive extensions and store results.

    This is a pure DB query ; no HTTP calls are made.
    Returns the number of backup files discovered.
    """
    extensions = await _load_extensions()

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            "SELECT id, url, timestamp, digest FROM snapshots WHERE domain_id = ?",
            (domain_id,),
        )
        rows = await cursor.fetchall()

        count = 0
        for snapshot_id, url, timestamp, digest in rows:
            # Determine path portion only (strip query string / fragment)
            try:
                parsed_path = urlparse(url).path
            except ValueError:
                parsed_path = url

            matched_ext: str | None = None
            for ext in extensions:
                if parsed_path.lower().endswith(ext.lower()):
                    matched_ext = ext
                    break

            if matched_ext is None:
                continue

            try:
                await db.execute(
                    """INSERT OR IGNORE INTO backup_files
                       (domain_id, url, extension, timestamp, digest)
                       VALUES (?, ?, ?, ?, ?)""",
                    (domain_id, url, matched_ext, timestamp, digest),
                )
                count += 1
            except aiosqlite.Error as exc:
                logger.debug("Skipping backup_file {}: {}", url, exc)

        await db.commit()

    logger.info(
        "discover_backup_files for domain_id={}: {} files found", domain_id, count
    )
    return count
