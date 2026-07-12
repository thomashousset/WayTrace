"""Tests for services/scraper.py back-off and retry behaviour."""
from __future__ import annotations

import asyncio
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import scraper


def _mock_response(status: int, *, retry_after: str | None = None, html: str = ""):
    resp = AsyncMock()
    resp.status = status
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    resp.text = AsyncMock(return_value=html)
    # Body-size-cap code path reads via resp.content.read(n), so stub that too.
    resp.content = MagicMock()
    resp.content.read = AsyncMock(return_value=html.encode("utf-8"))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class _FakeStore:
    async def update_job(self, *_args, **_kwargs):
        return None


def _install_scraper_patches(monkeypatch, sleeps, responses):
    """Patch asyncio.sleep (recording), random jitter (0), ClientSession (mocked),
    and settings (low concurrency / small timeouts)."""
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(round(s, 3))
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(scraper, "store", _FakeStore())
    monkeypatch.setattr(scraper.settings, "max_concurrent_scrapes", 2)
    monkeypatch.setattr(scraper.settings, "archive_request_timeout", 5)
    monkeypatch.setattr(scraper.settings, "scrape_max_retries", 2)
    # Neutralise the shared adaptive rate governor for scraper tests: keep it
    # effectively unlimited (so it never adds spacing that would perturb the
    # back-off assertions) and reset its process-wide state so one test's
    # refusals don't leak into the next.
    from services import archive_rate as _ar
    monkeypatch.setattr(scraper.settings, "archive_rate_per_minute", 100000)
    monkeypatch.setattr(scraper.settings, "archive_rate_max", 100000)
    _ar.reset()
    # Reset the shared circuit-breaker state so a prior test's trip doesn't make
    # is_hard_block() skip every page here.
    from services import archive_health as _ah
    with _ah._lock:
        _ah._fails.clear()
        _ah._hard_fails.clear()
        _ah._open_until = 0.0
        _ah._tripped_hard = False

    session = AsyncMock()
    session.get = MagicMock(side_effect=lambda *a, **kw: responses.pop(0))

    class FakeCS:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(scraper.aiohttp, "ClientSession", FakeCS)
    return session


def test_parse_retry_after_numeric():
    assert scraper._parse_retry_after("7") == 7.0
    assert scraper._parse_retry_after("0") == 0.0


def test_parse_retry_after_garbage_returns_none():
    assert scraper._parse_retry_after(None) is None
    assert scraper._parse_retry_after("") is None
    assert scraper._parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None


@pytest.mark.asyncio
async def test_retry_after_header_honoured(monkeypatch):
    """A 429 with Retry-After: 7 must cause a >=7 s sleep on retry."""
    sleeps: list[float] = []
    responses = [
        _mock_response(429, retry_after="7"),
        _mock_response(200, html="<html>ok</html>"),
    ]
    _install_scraper_patches(monkeypatch, sleeps, responses)

    snap = {"timestamp": "20200101120000", "url": "http://example.com/"}
    results = await scraper.scrape_snapshots([snap], "job-test")
    assert results[0]["html"] == "<html>ok</html>"
    assert any(s >= 7.0 for s in sleeps), f"Retry-After 7 was not honoured: {sleeps}"


@pytest.mark.asyncio
async def test_retry_after_capped(monkeypatch):
    """A 429 with Retry-After: 10000 must be clamped to the cap, not slept for."""
    sleeps: list[float] = []
    responses = [
        _mock_response(429, retry_after="10000"),
        _mock_response(200, html="<html>ok</html>"),
    ]
    _install_scraper_patches(monkeypatch, sleeps, responses)

    snap = {"timestamp": "20200101120000", "url": "http://example.com/"}
    await scraper.scrape_snapshots([snap], "job-test")
    # Every sleep must be below the cap plus a small jitter margin.
    cap = scraper._RETRY_AFTER_CAP_SECONDS
    assert max(sleeps) <= cap * 1.25, f"Cap not honoured: {sleeps}, cap={cap}"


@pytest.mark.asyncio
async def test_retry_after_floor_prevents_immediate_retry(monkeypatch):
    """A 429 with Retry-After: 0 must still wait the floor before retrying."""
    sleeps: list[float] = []
    responses = [
        _mock_response(429, retry_after="0"),
        _mock_response(200, html="<html>ok</html>"),
    ]
    _install_scraper_patches(monkeypatch, sleeps, responses)

    snap = {"timestamp": "20200101120000", "url": "http://example.com/"}
    await scraper.scrape_snapshots([snap], "job-test")
    floor = scraper._BACKOFF_MIN_SECONDS
    assert any(s >= floor for s in sleeps), f"Floor not honoured: {sleeps}, floor={floor}"


@pytest.mark.asyncio
async def test_404_not_retried(monkeypatch):
    """A 404 is a dead page. no retries, html=None, single GET."""
    sleeps: list[float] = []
    responses = [_mock_response(404)]
    session = _install_scraper_patches(monkeypatch, sleeps, responses)

    snap = {"timestamp": "20200101120000", "url": "http://example.com/gone"}
    results = await scraper.scrape_snapshots([snap], "job-test")
    assert results[0]["html"] is None
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_body_size_cap_truncates_oversized_page(monkeypatch):
    """A multi-MB archived page must be truncated, not OOM the worker."""
    sleeps: list[float] = []
    oversized = "<html>" + ("x" * (scraper._MAX_HTML_BYTES + 1024)) + "</html>"
    responses = [_mock_response(200, html=oversized)]
    _install_scraper_patches(monkeypatch, sleeps, responses)

    snap = {"timestamp": "20200101120000", "url": "http://example.com/big"}
    results = await scraper.scrape_snapshots([snap], "job-test")
    assert results[0]["html"] is not None
    # Truncation happens on the byte payload; resulting html is ≤ cap bytes
    # (UTF-8 of ASCII → byte count == character count).
    assert len(results[0]["html"].encode("utf-8")) <= scraper._MAX_HTML_BYTES


@pytest.mark.asyncio
async def test_global_pause_shared_across_workers(monkeypatch):
    """A 429 on one request must install a global pause the other worker
    respects before its own first request."""
    sleeps: list[float] = []
    # worker A hits 429 with Retry-After 5, then gets 200
    # worker B, which starts concurrently, should see the global pause
    responses = [
        _mock_response(429, retry_after="5"),
        _mock_response(200, html="<html>a</html>"),
        _mock_response(200, html="<html>b</html>"),
    ]
    _install_scraper_patches(monkeypatch, sleeps, responses)

    snaps = [
        {"timestamp": "20200101120000", "url": "http://example.com/a"},
        {"timestamp": "20200101120000", "url": "http://example.com/b"},
    ]
    results = await scraper.scrape_snapshots(snaps, "job-test")
    successes = [r for r in results if r["html"] is not None]
    assert len(successes) == 2
    # The global pause was installed and respected
    assert any(s >= 5.0 for s in sleeps)


def _conn_error_response():
    """A response whose context-manager entry raises a connection-level error,
    simulating archive.org dropping the TCP connection (its real throttle)."""
    resp = AsyncMock()
    err = scraper.aiohttp.ClientConnectorError(MagicMock(), OSError("connection refused"))
    resp.__aenter__ = AsyncMock(side_effect=err)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.mark.asyncio
async def test_connection_error_feeds_breaker_and_tags_conn(monkeypatch):
    from services import archive_health
    archive_health.record_success()  # ensure breaker closed / streak reset
    fails = {"n": 0}
    monkeypatch.setattr(archive_health, "record_failure", lambda: fails.__setitem__("n", fails["n"] + 1))
    monkeypatch.setattr(archive_health, "is_open", lambda: False)

    sleeps: list[float] = []
    # scrape_max_retries=2 -> 3 attempts, all raising a connection error.
    responses = [_conn_error_response() for _ in range(3)]
    _install_scraper_patches(monkeypatch, sleeps, responses)

    results = await scraper.scrape_snapshots(
        [{"timestamp": "20200101000000", "url": "http://example.com/"}], "job-conn"
    )

    assert results[0]["html"] is None
    assert results[0].get("error") == "conn"          # tagged as connection throttle
    assert fails["n"] >= 1                              # fed the circuit breaker
    assert any(s >= 8 for s in sleeps)                 # coordinated hard back-off (8*(attempt+1))


def _refused_response():
    """A response whose connect is REFUSED at the TCP level (errno 111),
    simulating archive.org firewalling our IP."""
    resp = AsyncMock()
    err = scraper.aiohttp.ClientConnectorError(MagicMock(), ConnectionRefusedError(111, "refused"))
    resp.__aenter__ = AsyncMock(side_effect=err)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.mark.asyncio
async def test_connection_refused_is_hard_block_no_retry(monkeypatch):
    from services import archive_health
    archive_health.record_success()
    hard = {"n": 0}
    monkeypatch.setattr(archive_health, "record_hard_block", lambda: hard.__setitem__("n", hard["n"] + 1))
    monkeypatch.setattr(archive_health, "is_open", lambda: False)

    sleeps: list[float] = []
    # Provide 4 refusals; a hard block must give up on the FIRST (no retry), so
    # only one is consumed.
    responses = [_refused_response() for _ in range(4)]
    _install_scraper_patches(monkeypatch, sleeps, responses)

    results = await scraper.scrape_snapshots(
        [{"timestamp": "20200101000000", "url": "http://example.com/"}], "job-blocked"
    )

    assert results[0]["html"] is None
    assert results[0].get("error") == "blocked"   # tagged as an IP block
    assert hard["n"] == 1                          # hard-block breaker fed
    assert len(responses) == 3                     # only ONE attempt consumed, no retries


@pytest.mark.asyncio
async def test_hard_block_skips_pages_without_requesting(monkeypatch):
    # When the breaker is open due to a hard IP block, pages are skipped
    # immediately - no request is issued and no back-off wait happens.
    from services import archive_health
    monkeypatch.setattr(archive_health, "is_hard_block", lambda: True)
    monkeypatch.setattr(archive_health, "is_open", lambda: True)

    sleeps: list[float] = []
    responses: list = []  # none should ever be consumed
    session = _install_scraper_patches(monkeypatch, sleeps, responses)

    results = await scraper.scrape_snapshots(
        [{"timestamp": "20200101000000", "url": f"http://example.com/{i}"} for i in range(5)],
        "job-hardblock",
    )

    assert all(r["html"] is None and r.get("error") == "blocked" for r in results)
    assert session.get.call_count == 0          # no archive.org request made
    assert all(s < 1 for s in sleeps)           # no 20s-per-page breaker back-off
