"""A scan reports how much of its own selection it actually covered.

`snapshots_analyzed` has always been the SELECTED count, so a run that stopped
at the wall-clock download budget after fetching 446 of 3000 captures looked
exactly like a complete one. For an investigator that is the difference between
"this domain has no admin panel" and "we never reached the pages where it would
be", so the truncation has to be a recorded fact, not something the reader
infers by subtracting two numbers whose meaning is not obvious.

Same fake-session harness as test_scrape_budget.py: the real scrape_snapshots
code path runs, no network involved.
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
    monkeypatch.setattr(settings, "scrape_delay_min", 0.0)
    monkeypatch.setattr(settings, "scrape_delay_max", 0.0)
    monkeypatch.setattr(settings, "scrape_max_retries", 0)
    monkeypatch.setattr(scraper.aiohttp, "ClientSession", _FakeSession)
    from services import archive_rate as _ar, archive_health as _ah
    monkeypatch.setattr(settings, "archive_rate_per_minute", 100000)
    monkeypatch.setattr(settings, "archive_rate_max", 100000)
    _ar.reset()
    with _ah._lock:
        _ah._fails.clear(); _ah._hard_fails.clear()
        _ah._open_until = 0.0; _ah._tripped_hard = False


def _fast(n, prefix="http://x"):
    return [{"url": f"{prefix}/{i}", "timestamp": "20200101000000"} for i in range(n)]


@pytest.mark.anyio
async def test_stats_keys_always_exist(monkeypatch):
    """The pipeline reads stats unconditionally. Missing keys would turn a
    zero-snapshot scan into a KeyError instead of a clean 'nothing truncated'."""
    monkeypatch.setattr(settings, "scrape_budget_seconds", 0)
    stats = {}
    assert await scraper.scrape_snapshots([], "job-empty", stats=stats) == []
    assert stats == {"budget_exhausted": False, "dropped": 0, "requested": 0}


@pytest.mark.anyio
async def test_complete_run_reports_no_truncation(monkeypatch):
    monkeypatch.setattr(settings, "scrape_budget_seconds", 30)
    stats = {}
    pages = await scraper.scrape_snapshots(_fast(4), "job-complete", stats=stats)
    assert len(pages) == 4
    assert stats["budget_exhausted"] is False
    assert stats["dropped"] == 0
    assert stats["requested"] == 4


@pytest.mark.anyio
async def test_budget_exhaustion_is_recorded(monkeypatch):
    """The counts alone cannot tell 'archive.org had nothing more' from 'we ran
    out of time'. The scraper is the only place that knows, so it has to say."""
    monkeypatch.setattr(settings, "scrape_budget_seconds", 1)
    snaps = _fast(5) + _fast(3, prefix="http://slow")
    stats = {}
    pages = await scraper.scrape_snapshots(snaps, "job-cut", stats=stats)

    assert stats["requested"] == 8
    assert stats["budget_exhausted"] is True
    assert stats["dropped"] == 3
    assert len(pages) == 5           # the slow ones never made it into the results
    assert len(pages) < stats["requested"]


@pytest.mark.anyio
async def test_stats_is_optional(monkeypatch):
    """Every existing caller passes no stats. That must keep working."""
    monkeypatch.setattr(settings, "scrape_budget_seconds", 0)
    pages = await scraper.scrape_snapshots(_fast(3), "job-nostats")
    assert len(pages) == 3


# --- the meta contract the report and the database depend on ----------------

def _meta_coverage(selected_count, pages_count, budget_exhausted):
    """The exact computation from routers/scan.py::_scan_pipeline, pinned here
    so a change to the rule breaks a test rather than a user's reading of a
    report."""
    truncated = pages_count < selected_count
    reason = None
    if truncated:
        reason = "scrape_budget" if budget_exhausted else "incomplete"
    return {"pages_attempted": pages_count, "truncated": truncated, "truncation_reason": reason}


def test_meta_marks_the_production_case():
    # aphp.fr, 2026-09-03: 3000 selected, 446 fetched, budget reached.
    assert _meta_coverage(3000, 446, True) == {
        "pages_attempted": 446, "truncated": True, "truncation_reason": "scrape_budget",
    }


def test_meta_marks_a_complete_run():
    assert _meta_coverage(120, 120, False) == {
        "pages_attempted": 120, "truncated": False, "truncation_reason": None,
    }


def test_meta_distinguishes_a_short_run_that_was_not_the_budget():
    """Cancelled mid-flight, or the pipeline stopped for another reason: still
    partial coverage, but not the budget's doing, and the report should not
    blame the budget."""
    assert _meta_coverage(500, 120, False)["truncation_reason"] == "incomplete"
