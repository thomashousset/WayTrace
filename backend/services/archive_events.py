"""Incident log for archive.org, one row per abnormal response.

Why this exists. The circuit breaker (services/archive_health.py) has always
collapsed everything into one signal, `record_failure()`, and on the index path
(services/cdx.py) a 503, an expiry and a TCP refusal literally shared one
`except` clause. So the question that has to be answered before any threshold is
touched, "are they throttling us politely or refusing us", had no answer in the
data. Three causes, three opposite corrections:

  * ``http_429``      a working service asking us to slow down. Correction:
                      obey the number they give in Retry-After.
  * ``conn_refused``  the OS rejected the TCP connect (errno 111). That is a
                      damaged IP reputation. Correction: stop, be forgotten.
  * ``http_503``      their outage. Measured 2026-09-07 from a fresh residential
                      IP at under 10 % of our own ceiling: three identical
                      requests gave a 200, an expiry and a 503. Nothing we tune
                      changes it. Correction: wait.
  * ``timeout``       we gave up waiting. Ours, not theirs.

Only abnormal responses are written. A row per request would bury the signal and
grow the table for nothing; the success volume is already known elsewhere.

Nothing here may ever raise: ``record`` sits inside the scrape loop, and a
failed counter must cost a counter, never a scan.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger

# The taxonomy IS the product of this table. A free-form string would dissolve
# it within months, so an unknown kind is a programming error, not a row.
KINDS = frozenset({
    "http_429",       # throttled, politely
    "http_503",       # their service is unavailable
    "http_5xx",       # any other server-side error
    "timeout",        # we stopped waiting
    "conn_refused",   # TCP connect rejected: IP reputation
    "conn_reset",     # connection dropped or stalled mid-flight
})

# Where the request went. Kept coarse on purpose: the useful split is index
# versus content, not one label per call site.
ENDPOINTS = frozenset({"cdx", "cdx_resume", "replay", "favicon", "probe"})


def classify_status(status: int) -> str | None:
    """Kind for an HTTP status, or None when the status is not an incident.

    404 and 410 are ordinary: archive.org simply has no capture there. Counting
    them would drown the incidents in noise.
    """
    if status == 429:
        return "http_429"
    if status == 503:
        return "http_503"
    if status >= 500:
        return "http_5xx"
    return None


def classify_exception(exc: BaseException) -> str:
    """Kind for a network-level failure.

    A refusal and a mid-flight drop are deliberately NOT merged. Only the
    refusal means "this IP is being rejected", and it is the counter that
    triggers a rollback, so inflating it with ordinary drops would make the
    rollback rule fire on noise.
    """
    err = getattr(exc, "os_error", None) or exc
    if isinstance(err, ConnectionRefusedError) or getattr(err, "errno", None) == 111:
        return "conn_refused"
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, (aiohttp.ServerDisconnectedError,
                        aiohttp.ServerConnectionError,
                        aiohttp.ClientPayloadError,
                        aiohttp.ClientOSError)):
        return "conn_reset"
    return "conn_reset"


def parse_retry_after(value: str | None) -> int | None:
    """Seconds from a Retry-After value, or None when it is absent OR in the
    HTTP-date form we do not parse.

    None is meaningful and is not zero: zero would read as "retry immediately",
    which is the opposite of what the header asks. The caller stores the raw
    string alongside, so a date-form header shows up as "present but unparsed"
    instead of silently counting as "they never send one".
    """
    if value is None:
        return None
    try:
        seconds = int(float(value.strip()))
    except (AttributeError, TypeError, ValueError):
        return None
    return max(0, seconds)


async def record(
    kind: str,
    *,
    endpoint: str,
    http_status: int | None = None,
    retry_after_raw: str | None = None,
    server_time_ms: int | None = None,
    elapsed_ms: int | None = None,
    rate_at_event: float | None = None,
    domain: str | None = None,
) -> None:
    """Persist one incident. Best-effort: never raises on the hot path."""
    if kind not in KINDS:
        raise ValueError(f"unknown archive event kind {kind!r}; expected one of {sorted(KINDS)}")
    try:
        from db import get_db
        conn = await get_db()
        try:
            await conn.execute(
                "INSERT INTO archive_events (occurred_at, endpoint, kind, http_status,"
                " retry_after, retry_after_raw, server_time_ms, elapsed_ms,"
                " rate_at_event, domain) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                    endpoint, kind, http_status,
                    parse_retry_after(retry_after_raw), retry_after_raw,
                    server_time_ms, elapsed_ms, rate_at_event, domain,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception as exc:
        # A counter is worth less than the scan it is counting.
        logger.debug("archive event not recorded ({} on {}): {}", kind, endpoint, exc)


def server_time_ms_from(headers) -> int | None:
    """Their processing time, from the undocumented ``x-tr`` response header.

    Observed 2026-09-07 to track their side closely: 21250 on a reply that took
    27.3 s end to end, 2306 on one that took 7.8 s. Crossed with our own
    elapsed_ms it separates "archive.org is slow" from "we are queueing", which
    is the question underneath every latency measurement in this project.

    Undocumented, so it is read defensively and never required.
    """
    try:
        raw = headers.get("x-tr") or headers.get("X-TR")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


async def counts(hours: int = 24) -> dict[tuple[str, str], int]:
    """Incidents per (endpoint, kind) over the last *hours*.

    This is the before/after view: without it, changing a threshold is changing
    a number blind, which is exactly how the 80/min ceiling was picked.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S")
    try:
        from db import get_db
        conn = await get_db()
        try:
            cur = await conn.execute(
                "SELECT endpoint, kind, COUNT(*) AS n FROM archive_events"
                " WHERE occurred_at >= ? GROUP BY endpoint, kind",
                (since,),
            )
            return {(r["endpoint"], r["kind"]): r["n"] for r in await cur.fetchall()}
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("archive event counts unavailable: {}", exc)
        return {}


async def purge(older_than_days: int = 90) -> int:
    """Drop incidents older than *older_than_days*. Its own retention, longer
    than the scans': comparing September with July is the whole point."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime(
        "%Y-%m-%dT%H:%M:%S")
    try:
        from db import get_db
        conn = await get_db()
        try:
            cur = await conn.execute(
                "DELETE FROM archive_events WHERE occurred_at < ?", (cutoff,))
            await conn.commit()
            return cur.rowcount or 0
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("archive event purge failed: {}", exc)
        return 0
