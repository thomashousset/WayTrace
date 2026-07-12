"""Best-effort favicon hashing.

Boris feedback: surface an MD5/SHA-256 per favicon so identical brand icons can
be pivoted across sites (same operator). The favicon bytes are NOT part of the
scraped pages, so the server fetches each archived favicon once from
archive.org. This adds a tiny amount of load (a handful of small files per
scan), so it is hard-gated:

  * the circuit breaker (services.archive_health) short-circuits the whole pass
    when archive.org is already struggling - we never pile on,
  * a strict per-scan cap on the number of favicons hashed,
  * the process-wide archive.org concurrency semaphore is reused so this never
    raises the aggregate request rate,
  * every fetch is best-effort: any error just leaves that favicon un-hashed.

It must never block or fail a scan.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re

import aiohttp
from loguru import logger

from config import settings
from services import archive_health, archive_rate
from config import USER_AGENT
from services.scraper import _get_global_sem

# A favicon is tiny; cap the read so a mislabelled large asset can't hurt us.
_MAX_FAVICON_BYTES = 512 * 1024
# Hash at most this many distinct favicons per scan (keeps added archive.org
# load negligible even on huge domains).
_MAX_FAVICONS = 16
_FETCH_TIMEOUT = 15

_TS_RE = re.compile(r"/web/(\d+)")


def _mmh3_x86_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 x86 32-bit, matching the reference ``mmh3.hash()`` (signed).

    Pure-Python so the tool carries no native dependency. Fuzz-verified against
    the mmh3 C library across 1000 random inputs (0 mismatches). Used to build
    the Shodan favicon hash, which is mmh3 of the base64-encoded icon bytes.
    """
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    length = len(data)
    h1 = seed & 0xFFFFFFFF
    rounded = (length // 4) * 4
    for i in range(0, rounded, 4):
        k1 = (data[i] | (data[i + 1] << 8) | (data[i + 2] << 16) | (data[i + 3] << 24)) & 0xFFFFFFFF
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
        h1 = ((h1 << 13) | (h1 >> 19)) & 0xFFFFFFFF
        h1 = (h1 * 5 + 0xE6546B64) & 0xFFFFFFFF
    k1 = 0
    tail = length & 3
    if tail >= 3:
        k1 ^= data[rounded + 2] << 16
    if tail >= 2:
        k1 ^= data[rounded + 1] << 8
    if tail >= 1:
        k1 ^= data[rounded]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
    h1 ^= length
    h1 ^= h1 >> 16
    h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
    h1 ^= h1 >> 13
    h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
    h1 ^= h1 >> 16
    return h1 - 0x100000000 if h1 & 0x80000000 else h1


def shodan_favicon_hash(data: bytes) -> int:
    """The Shodan ``http.favicon.hash`` value for raw favicon bytes.

    Shodan hashes the RFC 2045 base64 encoding of the icon (newline every 76
    chars, as ``base64.encodebytes`` produces), not the raw bytes.
    """
    return _mmh3_x86_32(base64.encodebytes(data))


def _abs_favicon(url: str, domain: str) -> str | None:
    if not url:
        return None
    if re.match(r"^https?://", url, re.IGNORECASE):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/") and domain:
        return f"https://{domain}{url}"
    return None


def _wayback_raw_url(item: dict, domain: str) -> str | None:
    """Build the archive.org raw-bytes URL (``im_`` modifier) for a favicon."""
    abs_url = _abs_favicon(item.get("url") or "", domain)
    if not abs_url:
        return None
    ts = None
    m = _TS_RE.search(item.get("source_url") or "")
    if m:
        ts = m.group(1)
    if not ts:
        digits = re.sub(r"\D", "", item.get("first_seen") or "")
        if len(digits) >= 6:
            ts = (digits + "01000000")[:14]
    if not ts:
        return None
    return f"https://web.archive.org/web/{ts}im_/{abs_url}"


async def hash_favicons(favicons: list[dict], domain: str) -> int:
    """Attach md5 + sha256 to favicon items in place. Returns count hashed.

    Best-effort and breaker-gated: returns 0 immediately when the archive.org
    breaker is open, and silently skips any favicon that errors."""
    if not favicons:
        return 0
    if archive_health.is_open():
        logger.info("favicon hashing skipped: archive breaker open")
        return 0

    # Distinct by URL, most recent first, capped.
    seen: set[str] = set()
    targets: list[tuple[dict, str]] = []
    for f in sorted(favicons, key=lambda x: x.get("first_seen") or "", reverse=True):
        key = (f.get("url") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        raw = _wayback_raw_url(f, domain)
        if raw:
            targets.append((f, raw))
        if len(targets) >= _MAX_FAVICONS:
            break
    if not targets:
        return 0

    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT)
    headers = {"User-Agent": USER_AGENT}
    hashed = 0

    async def _one(session: aiohttp.ClientSession, item: dict, url: str) -> None:
        nonlocal hashed
        if archive_health.is_open():
            return
        try:
            async with _get_global_sem():
                await archive_rate.acquire()
                async with session.get(url) as resp:
                    if resp.status != 200:
                        if resp.status in (429, 503):
                            archive_health.record_failure()
                        return
                    data = await resp.content.read(_MAX_FAVICON_BYTES + 1)
            if not data or len(data) > _MAX_FAVICON_BYTES or len(data) < 16:
                # too big (not a real favicon) or a sub-16-byte sentinel: skip.
                archive_health.record_success()
                return
            item["md5"] = hashlib.md5(data).hexdigest()
            item["sha256"] = hashlib.sha256(data).hexdigest()
            # Shodan pivot: http.favicon.hash:<this value>
            item["shodan"] = shodan_favicon_hash(data)
            archive_health.record_success()
            hashed += 1
        except (aiohttp.ClientError, asyncio.TimeoutError):
            archive_health.record_failure()
        except Exception as exc:  # never let favicon hashing break a scan
            logger.debug("favicon hash error for {}: {}", url, exc)

    connector = aiohttp.TCPConnector(limit=settings.archive_global_concurrency)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
        await asyncio.gather(*[_one(session, it, u) for it, u in targets])

    if hashed:
        logger.info("Hashed {}/{} favicons for {}", hashed, len(targets), domain)
    return hashed
