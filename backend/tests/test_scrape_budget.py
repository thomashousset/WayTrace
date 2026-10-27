"""The scrape phase honours an optional wall-clock budget: past the deadline it
keeps the pages already fetched ("fresh") and drops slow/pending ones, so a scan
completes (partial) instead of dragging on or losing everything.

We inject a fake aiohttp session whose responses sleep based on the URL, so the
real scrape_snapshots code path (budget loop, task cancellation) is exercised
without any network.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from config import settings
from services import scraper


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeContent:
    async def read(self, *a):
        return b"<html></html>"


class _FakeResp:
    def __init__(self, url):
        self._url = url
        self.status = 200
        self.headers = {}
        self.content = _FakeContent()

    async def __aenter__(self):
        # "slow" URLs stall well past any test budget; others return promptly.
        await asyncio.sleep(30 if "slow" in self._url else 0.01)
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, *a, **k):
        return _FakeResp(url)


@pytest.fixture(autouse=True)
def _fast_and_fake(monkeypatch):
    # No real delays / retries / global pause getting in the way of the test.
    monkeypatch.setattr(settings, "scrape_delay_min", 0.0)
    monkeypatch.setattr(settings, "scrape_delay_max", 0.0)
    monkeypatch.setattr(settings, "scrape_max_retries", 0)
    monkeypatch.setattr(scraper.aiohttp, "ClientSession", _FakeSession)
    # Keep the shared rate governor unlimited and the breaker closed, and reset
    # both so state from another test can't skip every page here.
    from services import archive_rate as _ar, archive_health as _ah
    monkeypatch.setattr(settings, "archive_rate_per_minute", 100000)
    monkeypatch.setattr(settings, "archive_rate_max", 100000)
    _ar.reset()
    with _ah._lock:
        _ah._fails.clear(); _ah._hard_fails.clear()
        _ah._open_until = 0.0; _ah._tripped_hard = False


@pytest.mark.anyio
async def test_budget_keeps_fast_drops_slow(monkeypatch):
    monkeypatch.setattr(settings, "scrape_budget_seconds", 1)
    snaps = [{"url": f"http://x/{i}", "timestamp": "20200101000000"} for i in range(5)]
    snaps += [{"url": f"http://slow/{i}", "timestamp": "20200101000000"} for i in range(3)]
    results = await scraper.scrape_snapshots(snaps, "job-budget")
    fetched = [r for r in results if r.get("html")]
    # The 5 fast pages come back with html; the 3 slow ones are cancelled.
    assert len(fetched) == 5
    assert all("slow" not in r["url"] for r in fetched)


@pytest.mark.anyio
async def test_no_budget_waits_for_all(monkeypatch):
    monkeypatch.setattr(settings, "scrape_budget_seconds", 0)
    snaps = [{"url": f"http://x/{i}", "timestamp": "20200101000000"} for i in range(6)]
    results = await scraper.scrape_snapshots(snaps, "job-nobudget")
    assert len([r for r in results if r.get("html")]) == 6


@pytest.mark.anyio
async def test_budget_keeps_all_when_fast(monkeypatch):
    # A generous budget should not drop anything when every page is quick.
    monkeypatch.setattr(settings, "scrape_budget_seconds", 30)
    snaps = [{"url": f"http://x/{i}", "timestamp": "20200101000000"} for i in range(4)]
    results = await scraper.scrape_snapshots(snaps, "job-fast")
    assert len([r for r in results if r.get("html")]) == 4


class _CountingSession(_FakeSession):
    """A fake session that records every .get() so we can assert none fire."""
    calls = 0

    def get(self, url, *a, **k):
        _CountingSession.calls += 1
        return _FakeResp(url)


@pytest.mark.anyio
async def test_hard_block_bails_out_after_the_semaphore(monkeypatch):
    # Regression: all page tasks are created up front. With low concurrency,
    # hundreds queue at the semaphore having passed the PRE-semaphore hard-block
    # check before the breaker opened; without a POST-semaphore re-check they each
    # still fired a doomed request for minutes. With the breaker hard-open, NO
    # request must fire and every page must return "blocked".
    import time as _time
    from services import archive_health as _ah

    monkeypatch.setattr(settings, "max_concurrent_scrapes", 2)
    _CountingSession.calls = 0
    monkeypatch.setattr(scraper.aiohttp, "ClientSession", _CountingSession)
    # Force the hard block open for a while.
    with _ah._lock:
        _ah._tripped_hard = True
        _ah._open_until = _time.time() + 600

    snaps = [{"url": f"http://x/{i}", "timestamp": "20200101000000"} for i in range(50)]
    results = await scraper.scrape_snapshots(snaps, "job-hardblock")

    assert _CountingSession.calls == 0, f"fired {_CountingSession.calls} requests while hard-blocked"
    assert len(results) == 50
    assert all(r.get("error") == "blocked" and r.get("html") is None for r in results)


# --- the deadline the pipeline derives from the scan timeout ----------------

@pytest.mark.anyio
async def test_a_deadline_keeps_what_was_fetched_without_any_budget(monkeypatch):
    """With SCRAPE_BUDGET_SECONDS off (the default), a scan still downloading
    at the scan timeout used to be killed with nothing saved. The pipeline now
    passes a deadline, and the scrape stops there like the budget does."""
    import time as _t
    monkeypatch.setattr(settings, "scrape_budget_seconds", 0)
    snaps = [{"url": f"http://x/{i}", "timestamp": "20200101000000"} for i in range(5)]
    snaps += [{"url": f"http://slow/{i}", "timestamp": "20200101000000"} for i in range(3)]
    stats = {}
    results = await scraper.scrape_snapshots(snaps, "job-deadline", stats=stats,
                                             deadline=_t.monotonic() + 1)
    assert len([r for r in results if r.get("html")]) == 5
    assert stats["budget_exhausted"] is True and stats["dropped"] == 3


@pytest.mark.anyio
async def test_the_earlier_of_budget_and_deadline_wins(monkeypatch):
    import time as _t
    monkeypatch.setattr(settings, "scrape_budget_seconds", 1)
    snaps = [{"url": f"http://slow/{i}", "timestamp": "20200101000000"} for i in range(2)]
    t0 = _t.monotonic()
    await scraper.scrape_snapshots(snaps, "job-min", deadline=_t.monotonic() + 600)
    assert _t.monotonic() - t0 < 5


@pytest.mark.anyio
async def test_results_come_back_in_snapshot_order(monkeypatch):
    """The page index keeps the first pages of the list: completion order
    would make it depend on which downloads happened to be fast."""
    import time as _t
    monkeypatch.setattr(settings, "scrape_budget_seconds", 0)
    order = [f"http://x/{i}" for i in range(8)]
    snaps = [{"url": u, "timestamp": f"2020010{i}000000"} for i, u in enumerate(order)]

    async def jitter_enter(self):
        await asyncio.sleep(0.05 * (8 - int(self._url.rsplit("/", 1)[1])))
        return self
    monkeypatch.setattr(_FakeResp, "__aenter__", jitter_enter)
    results = await scraper.scrape_snapshots(snaps, "job-order", deadline=_t.monotonic() + 30)
    assert [r["url"] for r in results] == order


def test_scrape_deadline_leaves_room_after_the_download(monkeypatch):
    import time as _t
    from routers.scan import _scrape_deadline
    monkeypatch.setattr(settings, "scan_timeout_seconds", 3600)
    start = _t.time() - 100          # the CDX phase already took 100 s
    left = _scrape_deadline(start) - _t.monotonic()
    assert 3600 - 100 - 300 - 2 < left <= 3600 - 100 - 300
    monkeypatch.setattr(settings, "scan_timeout_seconds", 60)
    left = _scrape_deadline(_t.time()) - _t.monotonic()
    assert 47 < left <= 48           # 20 % reserve on a short timeout


@pytest.mark.anyio
async def test_cancelling_the_scrape_cancels_every_page_task(monkeypatch):
    """Seen on a real scan deleted mid-download: the deadline loop did not
    pass the cancellation on, so page tasks outlived the session and woke up
    to "Session is closed", one "Task exception was never retrieved" each."""
    import time as _t
    monkeypatch.setattr(settings, "scrape_budget_seconds", 0)
    monkeypatch.setattr(settings, "max_concurrent_scrapes", 2)
    snaps = [{"url": f"http://slow/{i}", "timestamp": "20200101000000"} for i in range(20)]
    before = set(asyncio.all_tasks())
    task = asyncio.ensure_future(
        scraper.scrape_snapshots(snaps, "job-cancel", deadline=_t.monotonic() + 600))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    leftovers = [t for t in asyncio.all_tasks() - before if t is not asyncio.current_task()]
    assert leftovers == [], leftovers
