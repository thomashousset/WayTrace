from __future__ import annotations

import asyncio
import gzip
import json
import time
from pathlib import Path

import aiohttp
from loguru import logger

from config import settings
from services import archive_health, archive_rate
from services.scraper import _get_global_sem  # shared archive.org concurrency cap

CDX_URL = "https://web.archive.org/cdx/search/cdx"

# Default wall-clock deadline for the simple /api/scan flow's CDX phase.
# The two-phase /api/collect path uses depth-aware budgets via
# services.collector.cdx_budget_for_depth. this constant is the floor
# for the legacy single-shot endpoint.
_LEGACY_CDX_DEADLINE_SECONDS = 240

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cdx"
# How long a cached CDX result is served before we re-query archive.org. Repeat
# lookups of the same domain (preflight then scan, or two users) reuse it, which
# is the main lever for cutting archive.org load / throttling. 6h keeps it fresh.
_CACHE_TTL_SECONDS = 6 * 3600


def _cache_path(domain: str) -> Path:
    safe = domain.replace("/", "_").replace(":", "_")
    return _CACHE_DIR / f"{safe}.json.gz"


def _load_cache_sync(domain: str, ttl: int | None = None) -> dict | None:
    p = _cache_path(domain)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if ttl is not None:
            age = time.time() - data.get("cached_at", 0)
            if age > ttl:
                logger.info("CDX cache stale for {} (age {}s > {}s)", domain, int(age), ttl)
                return None
        logger.info("CDX cache hit for {} ({} snapshots)", domain, data.get("total_found", 0))
        return data
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        logger.debug("CDX cache read failed for {}: {}", domain, exc)
        return None


def _save_cache_sync(domain: str, result: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with gzip.open(_cache_path(domain), "wt", encoding="utf-8") as f:
            json.dump({**result, "cached_at": time.time()}, f)
        logger.info("CDX cached for {} ({} snapshots)", domain, result.get("total_found", 0))
    except Exception as exc:
        logger.warning("CDX cache write failed: {}", exc)


async def _load_cache(domain: str, ttl: int | None = None) -> dict | None:
    return await asyncio.to_thread(_load_cache_sync, domain, ttl)


async def _save_cache(domain: str, result: dict) -> None:
    await asyncio.to_thread(_save_cache_sync, domain, result)


def build_cdx_params(
    domain: str,
    *,
    resume_key: str | None = None,
    limit: int | None = None,
    collapse: str | None = "timestamp:6",
) -> dict:
    """Return the shared CDX query params. Used by both preflight and collector.

    *collapse* defaults to ``timestamp:6``. archive.org keeps a single
    snapshot per (URL, YYYY-MM) bucket, which trades a bit of temporal
    granularity for **path diversity**. Without it, archive.org returns
    snapshots sorted by urlkey, so for a popular site like stripe.com
    a 20 000-row budget gets eaten entirely by ~20 k captures of the
    same homepage URL. every other path (``/docs``, ``/pricing``,
    ``/jobs``, ``/.well-known/security.txt``) never makes it into the
    sample. ``timestamp:6`` keeps month-level evolution while exposing
    the full URL surface to the post-CDX path-scoring filter.
    """
    params: dict = {
        "url": f"*.{domain}/*",
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "filter": ["statuscode:200", "mimetype:text/html"],
        "showResumeKey": "true",
    }
    if collapse:
        params["collapse"] = collapse
    if resume_key:
        params["resumeKey"] = resume_key
    if limit is not None:
        params["limit"] = limit
    return params


# Approximate records per CDX "page" reported by ``showNumPages=true``.
# Archive.org's CDX indexes data into blocks of roughly this size; the
# multiplication is rough (within a factor of ~2) but sufficient for
# depth-selection thresholding. The figure is **pre-filter**: it does
# not account for the statuscode=200 / mimetype=text/html filters we
# apply later. the real HTML snapshot count is typically 10-30 % of
# the raw record count, which the auto-depth thresholds bake in.
_CDX_RECORDS_PER_PAGE = 3000


async def cdx_size_probe(
    domain: str,
    *,
    request_timeout: int = 15,
) -> dict:
    """Cheap CDX probe. return an approximate record-count estimate.

    Hits ``cdx?url=*.<domain>/*&showNumPages=true`` and parses the
    integer-only response. One small HTTP request, ~2-10 s on a healthy
    archive.org. Used to drive the auto-depth picker before the heavy
    crawl starts.

    The result dict keys:
      * ``ok`` (bool): probe succeeded.
      * ``page_count`` (int): raw value returned by archive.org.
      * ``estimated_records`` (int): ``page_count × _CDX_RECORDS_PER_PAGE``.
      * ``error`` (str): set when ``ok=False``.
    """
    url = f"{CDX_URL}?url=*.{domain}/*&showNumPages=true"
    timeout = aiohttp.ClientTimeout(total=request_timeout)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with archive_rate.slot(_get_global_sem()), session.get(url) as resp:
                if resp.status != 200:
                    return {
                        "ok": False, "page_count": 0, "estimated_records": 0,
                        "error": f"HTTP {resp.status}",
                    }
                raw = (await resp.text()).strip()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return {
            "ok": False, "page_count": 0, "estimated_records": 0,
            "error": repr(exc),
        }

    try:
        page_count = int(raw)
    except ValueError:
        return {
            "ok": False, "page_count": 0, "estimated_records": 0,
            "error": f"unexpected probe payload: {raw[:60]!r}",
        }

    estimated = page_count * _CDX_RECORDS_PER_PAGE
    logger.info(
        "CDX size probe for {}: {} pages -> ~{} records",
        domain, page_count, estimated,
    )
    return {
        "ok": True, "page_count": page_count,
        "estimated_records": estimated, "error": "",
    }


def detect_and_strip_resume_key(data: list) -> tuple[str | None, list]:
    """If the last row is a lone resumeKey string, return (key, data without it)."""
    if data and isinstance(data[-1], list) and len(data[-1]) == 1:
        possible = data[-1][0]
        if isinstance(possible, str) and len(possible) > 20:
            return possible, data[:-1]
    return None, data


_CDX_HEADER_FIRST_COL = {"timestamp", "original", "urlkey"}


def parse_cdx_rows(data: list) -> list[dict]:
    """Parse CDX JSON rows into snapshot dicts.

    Handles both header-present and header-absent shapes. Rows with fewer
    than 5 fields are skipped silently (defensive against CDX quirks).
    """
    if not data:
        return []
    first = data[0] if data else None
    if first and isinstance(first, list) and first and first[0] in _CDX_HEADER_FIRST_COL:
        data = data[1:]
    rows: list[dict] = []
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 5:
            continue
        rows.append(
            {
                "timestamp": entry[0],
                "url": entry[1],
                "status": entry[2],
                "mimetype": entry[3],
                "digest": entry[4],
            }
        )
    return rows


def _salvage_partial_cdx_json(raw: bytes, exc: json.JSONDecodeError, domain: str) -> list | None:
    """Best-effort recovery when CDX streams a malformed row in a huge payload.

    The response shape is a JSON array of arrays. On malformed input we
    truncate at the last complete row boundary (``],``) before the failure
    position, close the array, and reparse. Returns None if no safe
    truncation point is found. callers should treat that as terminal.
    """
    # bytes.decode(errors="replace") cannot raise, and bytes slicing clamps
    # out-of-range positions. so no try/except needed.
    text = raw[: exc.pos].decode("utf-8", errors="replace")

    cut = text.rfind("],")
    if cut <= 0:
        return None

    candidate = text[: cut + 1] + "]"
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        return None

    logger.warning(
        "Salvaged {} CDX rows for {} after malformed JSON at char {} "
        "(dropped the trailing malformed portion)",
        len(data), domain, exc.pos,
    )
    return data


async def fetch_cdx_snapshots(
    domain: str,
    *,
    max_snapshots: int = 50000,
    deadline_seconds: int = _LEGACY_CDX_DEADLINE_SECONDS,
    request_timeout: int = 120,
    retries: int | None = None,
    server_limit: int | None = None,
    collapse: str | None = "timestamp:6",
    use_cache: bool = True,
    cache_ttl: int | None = _CACHE_TTL_SECONDS,
) -> dict:
    """Fetch every CDX page for ``domain`` with budget-aware pagination.

    *deadline_seconds* caps the whole call (init request + retries + resume
    pages); *request_timeout* caps a single HTTP request; *retries*
    overrides the global archive_retry_count for callers that need fail-
    fast behaviour (preflight UI probe). *server_limit* sets the CDX ``limit``
    param so archive.org stops early instead of scanning + collapsing the
    entire index for huge domains (lemonde.fr, wordpress.org) - that full
    scan is what makes the unbounded query time out.
    """
    if use_cache:
        cached = await _load_cache(domain, ttl=cache_ttl)
        if cached is not None:
            return cached

    params = build_cdx_params(domain, limit=server_limit, collapse=collapse)

    timeout = aiohttp.ClientTimeout(total=request_timeout)
    last_error: Exception | None = None
    all_snapshots: list[dict] = []
    deadline = time.monotonic() + deadline_seconds
    if retries is None:
        retries = settings.archive_retry_count

    # Circuit breaker: if archive.org is already in cooldown, fail fast instead
    # of adding load to a host that is struggling (protects the server IP).
    if archive_health.is_open():
        raise RuntimeError(
            f"archive.org is rate-limiting us; cooling down for "
            f"{archive_health.seconds_remaining()}s before more requests"
        )

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(1 + retries):
            # Honour the wall-clock budget BEFORE issuing another retry.
            # Without this a 120s-per-request timeout × 4 retries can sit
            # for 8+ minutes on a flaky archive.org while the user just
            # wanted a quick preflight.
            if time.monotonic() > deadline:
                break
            _attempt_start = time.monotonic()
            try:
                async with archive_rate.slot(_get_global_sem()), session.get(CDX_URL, params=params) as resp:
                    if resp.status == 429:
                        # Rate-limited: count it toward the breaker and stop
                        # early once it trips rather than sleeping + retrying
                        # into the wall.
                        archive_health.record_failure()
                        if archive_health.is_open():
                            last_error = RuntimeError("rate-limited (429), breaker open")
                            break
                        wait = 30 * (2 ** attempt)
                        logger.warning(
                            "CDX rate-limited (429), waiting {}s (attempt {}/{})",
                            wait, attempt + 1, 1 + retries,
                        )
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()
                    raw = await resp.read()

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                archive_health.record_failure()
                if archive_health.is_open():
                    # archive.org is unhealthy; bail out instead of hammering.
                    break
                delay = 5 * (2 ** attempt)
                # repr(exc) ensures bare TimeoutError() shows as "TimeoutError()"
                # instead of an empty string, which was misleading in logs.
                logger.warning(
                    "CDX request failed: {} ; retrying in {}s (attempt {}/{})",
                    repr(exc), delay, attempt + 1, 1 + retries,
                )
                await asyncio.sleep(delay)
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                # archive.org occasionally streams a truncated/malformed row
                # inside an otherwise huge payload. Try to recover the valid
                # prefix instead of failing the whole preflight.
                data = _salvage_partial_cdx_json(raw, exc, domain)
                if data is None:
                    logger.error(
                        "CDX returned malformed JSON for {} at char {} and could not be salvaged",
                        domain, exc.pos,
                    )
                    raise RuntimeError(
                        f"CDX returned malformed JSON at position {exc.pos}"
                    ) from exc

            if not data or len(data) < 2:
                logger.info("No archived snapshots found for {}", domain)
                return {"snapshots": [], "total_found": 0}

            resume_key, data = detect_and_strip_resume_key(data)
            all_snapshots = parse_cdx_rows(data)

            if resume_key:
                logger.info(
                    "CDX returned {} snapshots with resumeKey, fetching more...",
                    len(all_snapshots),
                )
                extra = await _fetch_cdx_resume(
                    session, domain, resume_key,
                    deadline=deadline,
                    remaining=max(0, max_snapshots - len(all_snapshots)),
                )
                all_snapshots.extend(extra)

            logger.info(
                "CDX returned {} snapshots for {}", len(all_snapshots), domain
            )
            archive_health.record_latency(time.monotonic() - _attempt_start)
            archive_health.record_success()
            result = {"snapshots": all_snapshots, "total_found": len(all_snapshots)}
            if use_cache:
                await _save_cache(domain, result)
            return result

    reason = str(last_error) if last_error else "rate-limited (429)"
    raise RuntimeError(
        f"CDX API unreachable after {1 + retries} attempts: {reason}"
    )


async def _fetch_cdx_resume(
    session: aiohttp.ClientSession,
    domain: str,
    resume_key: str,
    *,
    deadline: float | None = None,
    remaining: int | None = None,
) -> list[dict]:
    """Fetch remaining CDX pages, bounded by ``deadline`` and ``remaining``.

    Without a remaining-snapshot budget this loop will happily pull the
    full archive (250k+ on huge domains like wordpress.org / stripe.com),
    which dominates the user-perceived scan time on a depth=quick run.
    """
    all_extra: list[dict] = []
    current_key = resume_key
    max_pages = 50

    for page in range(max_pages):
        if deadline is not None and time.monotonic() > deadline:
            logger.warning(
                "CDX resume deadline reached for {} after page {}; stopping with {} extra rows",
                domain, page, len(all_extra),
            )
            break
        if remaining is not None and len(all_extra) >= remaining:
            logger.info(
                "CDX resume snapshot budget reached for {} ({} rows); stopping",
                domain, len(all_extra),
            )
            break
        params = build_cdx_params(domain, resume_key=current_key)

        try:
            async with archive_rate.slot(_get_global_sem()), session.get(CDX_URL, params=params) as resp:
                if resp.status == 429:
                    wait = 30 * (2 ** min(page, 3))
                    logger.warning("CDX resume rate-limited, waiting {}s", wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status != 200:
                    logger.warning("CDX resume page {} returned {}", page + 1, resp.status)
                    break
                raw = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("CDX resume fetch failed on page {}: {}", page + 1, exc)
            break

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            data = _salvage_partial_cdx_json(raw, exc, domain)
            if data is None:
                logger.warning(
                    "CDX resume page {} returned malformed JSON; stopping pagination", page + 1,
                )
                break

        next_key, data = detect_and_strip_resume_key(data)
        snapshots = parse_cdx_rows(data)
        if not snapshots:
            break

        all_extra.extend(snapshots)
        logger.info(
            "CDX resume page {} returned {} snapshots (total extra: {})",
            page + 1, len(snapshots), len(all_extra),
        )

        if next_key is None:
            break
        current_key = next_key

    return all_extra
