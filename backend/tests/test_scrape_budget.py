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
