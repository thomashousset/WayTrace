"""Process-wide, self-tuning rate governor for archive.org.

Concurrency caps bound how many requests run at once; they do NOT bound the
request *rate*. A burst - CDX pagination, several scans, a wave of retries - can
still spike the aggregate rate past archive.org's tolerance and get the whole
server IP throttled or connection-blocked (which has happened).

Every archive.org call in the process (CDX, page scrape, favicon, probe) draws a
token from ONE shared bucket, so the aggregate rate is bounded regardless of how
many scans or users are active.

The rate is not a fixed guess. archive.org publishes no limit and its tolerance
is dynamic, so we ADAPT it (AIMD, like TCP congestion control):

  * start at settings.archive_rate_per_minute,
  * additive-increase by _step after _increase_interval seconds with no refusal,
  * multiplicative-decrease (halve) on the FIRST connection-refusal,
  * clamped to [_min, _max].

The bias is deliberately slow-up / fast-down: we creep toward the sustainable
rate and retreat hard the instant archive.org pushes back, so the IP is never
driven past the edge. Callers feed the loop via report_success()/report_refusal().
"""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager

from config import settings


class _AdaptiveRate:
    """Holds the current requests-per-second, adjusted by AIMD feedback."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rate = settings.archive_rate_per_minute / 60.0
        self._last_refusal = 0.0
        self._last_increase = time.monotonic()

    def rate_per_sec(self) -> float:
        return max(self._rate, 1e-6)

    def _bounds(self) -> tuple[float, float]:
        return settings.archive_rate_min / 60.0, settings.archive_rate_max / 60.0

    def report_success(self) -> None:
        """A clean archive.org response. After a long enough quiet streak (no
        refusals, no recent bump), nudge the rate up one small step."""
        now = time.monotonic()
        lo, hi = self._bounds()
        interval = settings.archive_rate_increase_interval
        with self._lock:
            if (
                now - self._last_refusal >= interval
                and now - self._last_increase >= interval
                and self._rate < hi
            ):
                self._rate = min(hi, self._rate + settings.archive_rate_step / 60.0)
                self._last_increase = now

    def report_refusal(self) -> None:
        """archive.org refused a connection (throttle/block signal). Halve the
        rate at once and hold off increasing for a full interval."""
        now = time.monotonic()
        lo, _hi = self._bounds()
        with self._lock:
            self._last_refusal = now
            self._last_increase = now
            self._rate = max(lo, self._rate * settings.archive_rate_decrease_factor)

    def reset(self) -> None:
        with self._lock:
            self._rate = settings.archive_rate_per_minute / 60.0
            self._last_refusal = 0.0
            self._last_increase = time.monotonic()


_controller = _AdaptiveRate()


class TokenBucket:
    """Token bucket whose refill rate is read live from the adaptive controller,
    so a rate change (up or down) takes effect on the very next acquire()."""

    def __init__(self, capacity: float):
        self._capacity = max(capacity, 1.0)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                rate = _controller.rate_per_sec()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / rate
            await asyncio.sleep(wait)


_bucket: TokenBucket | None = None


def _get_bucket() -> TokenBucket:
    global _bucket
    if _bucket is None:
        _bucket = TokenBucket(capacity=settings.archive_rate_burst)
    return _bucket


async def acquire() -> None:
    """Wait for a slot before making an archive.org request."""
    await _get_bucket().acquire()


@asynccontextmanager
async def slot(semaphore: asyncio.Semaphore):
    """Hold ONE archive.org request slot for the duration of the block: a
    process-wide concurrency permit (the passed semaphore) AND a rate token.
    Bounds both simultaneous connections and request rate on every path (scan
    scrape, CDX preflight/enumeration, favicon fetch). The semaphore is passed
    in (not imported) to avoid an import cycle."""
    async with semaphore:
        await acquire()
        yield


def report_success() -> None:
    """Feedback: a clean archive.org response (drives the adaptive increase)."""
    _controller.report_success()


def report_refusal() -> None:
    """Feedback: archive.org refused a connection (drives the hard decrease)."""
    _controller.report_refusal()


def current_rate_per_minute() -> float:
    """Live rate, for logging / the admin monitor."""
    return round(_controller.rate_per_sec() * 60.0, 1)


def reset() -> None:
    """Reset the bucket + controller to the configured start. Used by tests and
    safe to call after a settings change."""
    global _bucket
    _bucket = None
    _controller.reset()
