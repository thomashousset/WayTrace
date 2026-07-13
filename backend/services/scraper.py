from __future__ import annotations

import asyncio
import random
import time
from collections import Counter

import aiohttp
from loguru import logger

from config import settings
from services import archive_health, archive_rate
from store import store

WAYBACK_URL = "https://web.archive.org/web/{timestamp}id_/{url}"

# Single-source User-Agent reused by the CDX collector and this scraper.
from config import USER_AGENT

# Upper bound on a honored Retry-After. 600 s is long enough to survive a
# genuine server cooldown, short enough that a misbehaving or adversarial
# server cannot pin us for hours.
_RETRY_AFTER_CAP_SECONDS = 600
# Floor for any back-off wait. keeps us from retrying immediately on a
# Retry-After: 0 or on a delayed clock skew.
_BACKOFF_MIN_SECONDS = 2.0
# Cap on the HTML bytes we will materialise for a single snapshot. Archive.org
# does occasionally hold multi-megabyte pages, and selectolax on 50+ MB of HTML
# burns RAM quickly. 10 MB covers 99.9% of real pages we care about.
_MAX_HTML_BYTES = 10 * 1024 * 1024

# archive.org throttles aggressive clients by dropping the TCP connection or
# stalling, NOT only via HTTP 429. These connection-level failures are the real
# rate-limit signal for the Wayback replay endpoints and must feed the same
# back-off + circuit breaker as a 429, otherwise the scraper keeps hammering a
# server that is already refusing it (the cause of the "hundreds of errors, 0
# 429s" scans and of the IP getting connection-blocked).
_THROTTLE_ERRORS = (
    aiohttp.ClientConnectorError,
    aiohttp.ClientOSError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerConnectionError,
    aiohttp.ServerTimeoutError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError,
)


def _is_connection_refused(exc: BaseException) -> bool:
    """True when the OS actively rejected the TCP connect (errno 111) - the
    signature of archive.org firewalling our IP, versus a timeout or a mid-stream
    drop. aiohttp wraps the OS error on ClientConnectorError.os_error."""
    err = getattr(exc, "os_error", None) or exc
    return isinstance(err, ConnectionRefusedError) or getattr(err, "errno", None) == 111

# Process-wide budget of simultaneous archive.org requests, shared by every
# running scan. Each scan still holds its own per-scan semaphore
# (max_concurrent_scrapes); this one caps the aggregate so N parallel scans
# never exceed archive_global_concurrency in flight. Lazily created so it binds
# to the running event loop.
_global_sem: asyncio.Semaphore | None = None


def _get_global_sem() -> asyncio.Semaphore:
    # Bind lazily to the running loop and rebuild if the loop changed. An
    # asyncio.Semaphore is pinned to the loop that first awaited it, and tests
    # (and any future multi-loop use) run on a fresh loop per case, so a cached
    # semaphore from a dead loop would raise "bound to a different event loop".
    global _global_sem
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _global_sem is None or getattr(_global_sem, "_wt_loop", None) is not loop:
        _global_sem = asyncio.Semaphore(settings.archive_global_concurrency)
        try:
            _global_sem._wt_loop = loop
        except Exception:
            pass
    return _global_sem


def _parse_retry_after(value: str | None) -> float | None:
    """Return seconds from a Retry-After header value (int seconds only)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


# Wayback preserves the original upstream response headers, prefixed with
# ``x-archive-orig-``. The http_headers extractor wants them prefix-stripped
# and lower-cased (e.g. "server", "x-powered-by", "set-cookie").
_ARCHIVE_ORIG_PREFIX = "x-archive-orig-"


def _orig_response_headers(headers) -> dict:
    out: dict[str, str] = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl.startswith(_ARCHIVE_ORIG_PREFIX):
            name = kl[len(_ARCHIVE_ORIG_PREFIX):]
            out[name] = (out[name] + ", " + v) if name in out else v
    return out


async def scrape_snapshots(
    snapshots: list[dict], job_id: str, on_page=None
) -> list[dict]:
    """Scrape the selected archived pages.

    `on_page`, if given, is an async callback invoked with each page result as it
    completes (best-effort, wrapped so it can never affect scraping). It lets the
    caller start extraction WHILE pages are still downloading (overlap), for a
    live-findings scan. It touches none of the anti-block logic below."""
    semaphore = asyncio.Semaphore(settings.max_concurrent_scrapes)
    timeout = aiohttp.ClientTimeout(total=settings.archive_request_timeout)
    connector = aiohttp.TCPConnector(
        limit=settings.max_concurrent_scrapes + 10,
        limit_per_host=settings.max_concurrent_scrapes,
        keepalive_timeout=60,
    )
    total = len(snapshots)
    completed = 0
    update_every = max(1, total // 200)  # ~200 progress updates so the bar moves smoothly

    # Adaptive delay: starts low, increases on 429s, decreases on success streaks.
    # Global pause lets one task signal every other that the server is upset,
    # so we don't keep hammering while one worker already backed off.
    _delay_state = {"min": settings.scrape_delay_min, "max": settings.scrape_delay_max}
    _rate_limit_hits = {"count": 0}
    _pause_until = {"ts": 0.0}
    # Per-outcome tally so the final log explains exactly why pages failed
    # (connection-throttled vs 404 vs 5xx vs timeout), instead of a bare count.
    outcomes: Counter = Counter()

    async def _respect_global_pause() -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        remaining = _pause_until["ts"] - now
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _set_global_pause(seconds: float) -> None:
        loop = asyncio.get_running_loop()
        target = loop.time() + seconds
        if target > _pause_until["ts"]:
            _pause_until["ts"] = target

    async def _wait_outside_semaphore(wait: float) -> None:
        """Sleep for *wait* seconds with a small random jitter, with the
        semaphore slot released so other tasks can make progress. A jitter of
        up to 20 % prevents all workers from thundering back in lockstep after
        a shared Retry-After wait.
        """
        jittered = wait + random.uniform(0, wait * 0.2)
        _set_global_pause(jittered)
        await asyncio.sleep(jittered)

    async def fetch_one(
        session: aiohttp.ClientSession, snap: dict
    ) -> dict:
        nonlocal completed
        url = WAYBACK_URL.format(
            timestamp=snap["timestamp"], url=snap["url"]
        )
        result = None

        for attempt in range(1 + settings.scrape_max_retries):
            # Any back-off that is longer than the post-request jitter happens
            # outside the semaphore so blocked workers don't hold their slot.
            await _respect_global_pause()

            # Hard IP block: the server is refused at the TCP level and will not
            # recover within this scan. Don't wait (that would burn 20s per
            # remaining page for nothing) - give up on this page immediately so
            # the scan finishes fast with whatever was already fetched.
            if archive_health.is_hard_block():
                result = {
                    "timestamp": snap["timestamp"], "url": snap["url"],
                    "html": None, "error": "blocked",
                }
                break

            # Soft throttle (429/timeout): the breaker is open but archive.org
            # should recover soon, so pause this worker briefly instead of firing
            # doomed requests. Bounded so a stuck-open breaker can't hang forever;
            # the caller's scrape budget cancels any stragglers.
            _breaker_waits = 0
            while archive_health.is_open() and not archive_health.is_hard_block() and _breaker_waits < 15:
                await _wait_outside_semaphore(min(archive_health.seconds_remaining() or 10, 20))
                _breaker_waits += 1

            # Per-scan slot first, then the process-wide budget: a single scan
            # is bounded to max_concurrent_scrapes while the aggregate across
            # all scans never exceeds archive_global_concurrency.
            async with semaphore, _get_global_sem():
                try:
                    # Process-wide rate ceiling: spaces requests so a burst
                    # never exceeds archive.org's tolerance (IP-block guard).
                    await archive_rate.acquire()
                    async with session.get(url) as resp:
                        status = resp.status
                        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                        if status == 429:
                            archive_health.record_failure()
                            _rate_limit_hits["count"] += 1
                            if retry_after is not None:
                                # Honour the server's number, but clamp to a
                                # sane range. too small = immediate retry,
                                # too large = denial of service against us.
                                wait = max(retry_after, _BACKOFF_MIN_SECONDS)
                                wait = min(wait, _RETRY_AFTER_CAP_SECONDS)
                            else:
                                base_wait = min(5 * (attempt + 1), 30)
                                extra = min(_rate_limit_hits["count"] * 2, 60)
                                wait = base_wait + extra
                            logger.warning(
                                "Rate-limited (429) on {}, waiting {:.1f}s (attempt {}/{}, total 429s: {}{})",
                                snap["url"], wait, attempt + 1,
                                1 + settings.scrape_max_retries,
                                _rate_limit_hits["count"],
                                " [Retry-After honoured]" if retry_after is not None else "",
                            )
                            _delay_state["min"] = min(_delay_state["min"] * 2, 2.0)
                            _delay_state["max"] = min(_delay_state["max"] * 2, 4.0)
                        elif status in (404, 410):
                            result = {"timestamp": snap["timestamp"], "url": snap["url"], "html": None, "error": "http_404"}
                            break
                        elif status == 503:
                            if retry_after is not None:
                                wait = max(retry_after, _BACKOFF_MIN_SECONDS)
                                wait = min(wait, _RETRY_AFTER_CAP_SECONDS)
                            else:
                                wait = 3 * (attempt + 1)
                            archive_health.record_failure()
                            if attempt >= settings.scrape_max_retries:
                                result = {"timestamp": snap["timestamp"], "url": snap["url"], "html": None, "error": "http_503"}
                                break
                        elif status >= 500:
                            archive_health.record_failure()
                            if attempt >= settings.scrape_max_retries:
                                result = {"timestamp": snap["timestamp"], "url": snap["url"], "html": None, "error": "http_5xx"}
                                break
                            wait = 3 * (attempt + 1)
                        elif status >= 400:
                            result = {"timestamp": snap["timestamp"], "url": snap["url"], "html": None, "error": "http_4xx"}
                            break
                        else:
                            # Read with a cap so a pathological multi-MB page
                            # can't OOM the worker. If the response exceeds the
                            # cap we treat it as scraped but truncated.
                            raw = await resp.content.read(_MAX_HTML_BYTES + 1)
                            if len(raw) > _MAX_HTML_BYTES:
                                logger.warning(
                                    "Page {} exceeded {} bytes cap. truncating",
                                    snap["url"], _MAX_HTML_BYTES,
                                )
                                raw = raw[:_MAX_HTML_BYTES]
                            html = raw.decode("utf-8", errors="replace")
                            result = {
                                "timestamp": snap["timestamp"],
                                "url": snap["url"],
                                "html": html,
                                "response_headers": _orig_response_headers(resp.headers),
                            }
                            # A clean response clears the failure streak so the
                            # breaker closes once archive.org is healthy again,
                            # and feeds the adaptive rate governor (creep up).
                            archive_health.record_success()
                            archive_rate.report_success()
                            # Gradually recover delays on success
                            if _delay_state["min"] > settings.scrape_delay_min:
                                _delay_state["min"] = max(
                                    settings.scrape_delay_min,
                                    _delay_state["min"] * 0.95,
                                )
                                _delay_state["max"] = max(
                                    settings.scrape_delay_max,
                                    _delay_state["max"] * 0.95,
                                )
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    is_throttle = isinstance(exc, _THROTTLE_ERRORS)
                    logger.debug(
                        "Scrape failed for {} (attempt {}/{}): {}",
                        url, attempt + 1, 1 + settings.scrape_max_retries, exc,
                    )
                    if _is_connection_refused(exc):
                        logger.warning(
                            "archive.org REFUSED the connection for {} (errno 111) "
                            "- adaptive_rate={}/min. Backing off + feeding the breaker.",
                            url, archive_rate.current_rate_per_minute(),
                        )
                        # Our IP is being refused: this is a block, not a blip.
                        # Halve the adaptive rate at once, feed the hard-block
                        # breaker, and stop immediately - retrying only confirms
                        # the block and deepens it.
                        archive_rate.report_refusal()
                        archive_health.record_hard_block()
                        result = {
                            "timestamp": snap["timestamp"], "url": snap["url"],
                            "html": None, "error": "blocked",
                        }
                        break
                    if is_throttle:
                        # Connection drop / stall = archive.org throttling us.
                        # Feed the breaker and back off hard + globally so every
                        # worker slows down together, exactly like a 429.
                        archive_health.record_failure()
                        _rate_limit_hits["count"] += 1
                        _delay_state["min"] = min(_delay_state["min"] * 2, 2.0)
                        _delay_state["max"] = min(_delay_state["max"] * 2, 4.0)
                    if attempt >= settings.scrape_max_retries:
                        result = {
                            "timestamp": snap["timestamp"], "url": snap["url"],
                            "html": None, "error": "conn" if is_throttle else "client",
                        }
                        break
                    wait = (8 * (attempt + 1)) if is_throttle else (3 * (attempt + 1))

            # Slot released. do the back-off outside so others can progress.
            if result is not None:
                break
            await _wait_outside_semaphore(wait)

        if result is None:
            result = {"timestamp": snap["timestamp"], "url": snap["url"], "html": None, "error": "exhausted"}

        outcomes["ok" if result.get("html") is not None else result.get("error", "failed")] += 1

        # Progress + delay after the slot is free.
        completed += 1
        if completed % update_every == 0 or completed == total:
            progress = 15 + int((completed / total) * 60)
            await store.update_job(
                job_id, progress=progress, step=f"Scraping page {completed}/{total}"
            )

        await asyncio.sleep(
            random.uniform(_delay_state["min"], _delay_state["max"])
        )

        # Hand the finished page to the caller (overlapped extraction). Best-effort:
        # a failing callback must never break the scrape.
        if on_page is not None:
            try:
                await on_page(result)
            except Exception as exc:
                logger.debug("on_page callback raised (ignored): {}", exc)

        return result

    headers = {"User-Agent": USER_AGENT}
    # Optional wall-clock budget for the scrape phase. archive.org latency is
    # erratic (a single page can stall 30s+), so rather than let one slow scan
    # drag on, or trip the hard job timeout and lose everything, we stop once the
    # budget is spent and keep whatever pages we already have ("fresh"). The
    # pipeline then extracts that subset and the scan completes (partial).
    budget = settings.scrape_budget_seconds
    deadline = (time.monotonic() + budget) if budget and budget > 0 else None

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
        tasks = [asyncio.ensure_future(fetch_one(session, snap)) for snap in snapshots]
        results: list[dict] = []
        if deadline is None:
            results = list(await asyncio.gather(*tasks))
        else:
            pending = set(tasks)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(
                    pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    break  # budget exhausted with nothing newly completed
                for t in done:
                    try:
                        results.append(t.result())
                    except Exception:
                        pass
            if pending:
                dropped = len(pending)
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                logger.warning(
                    "Scrape budget {}s reached for job {}: kept {} pages, "
                    "dropped {} slow/pending", budget, job_id, len(results), dropped,
                )

    success = sum(1 for r in results if r["html"] is not None)
    dropped = total - sum(outcomes.values())
    if dropped > 0:
        outcomes["budget_dropped"] += dropped
    # INFO-level breakdown so failing scans are diagnosable without DEBUG:
    # e.g. "conn=812 http_404=90 ok=298" points straight at connection
    # throttling vs missing captures.
    breakdown = " ".join(f"{k}={v}" for k, v in outcomes.most_common())
    logger.info(
        "Scraped {}/{} pages successfully (429 hits: {}) [{}] adaptive_rate={}/min",
        success, total, _rate_limit_hits["count"], breakdown,
        archive_rate.current_rate_per_minute(),
    )
    return list(results)
