"""Extraction must not block the event loop.

A large scan's extraction is CPU-bound and used to run inline on the single
worker, freezing health checks and every other user's progress polling for
minutes. It now runs in a worker thread (asyncio.to_thread). This test proves it:
while a deliberately slow, synchronous extract_all runs, a concurrent heartbeat
coroutine must keep ticking — if extraction ran inline, the heartbeat would be
frozen for the whole extraction.
"""
import asyncio
import time

import pytest
import pytest_asyncio

from db import init_db
from models import ScanConfig
from store import JobStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def fresh_db(tmp_path):
    p = str(tmp_path / "wt.db")
    await init_db(p)
    yield p
    import db as _db
    _db._db_path = None


@pytest.mark.asyncio
async def test_extraction_runs_off_the_event_loop(fresh_db, monkeypatch):
    import store as store_module
    import routers.scan as scan_module

    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    # Two fake pages, returned instantly (no network).
    async def _fake_scrape(selected, job_id, **kw):
        return [
            {"timestamp": "20200101000000", "url": "http://ex.com/", "html": "<html></html>", "error": None},
            {"timestamp": "20210101000000", "url": "http://ex.com/a", "html": "<html></html>", "error": None},
        ]
    monkeypatch.setattr(scan_module, "scrape_snapshots", _fake_scrape)

    # Deliberately slow, BLOCKING per-page extraction (the streamed path calls
    # process_page in batches via asyncio.to_thread). Two pages * 0.2s = ~0.4s.
    def _slow_process(page, domain, accum, cat_set, page_seq):
        time.sleep(0.2)
        return True
    monkeypatch.setattr(scan_module, "process_page", _slow_process)
    monkeypatch.setattr(scan_module, "mine_subdomains", lambda pages, d, a, c: None)
    monkeypatch.setattr(scan_module, "finalize_accum", lambda accum, categories=None: {"emails": []})
    monkeypatch.setattr(scan_module, "merge_analytics_ids", lambda r: None)
    monkeypatch.setattr(scan_module, "compute_highlights", lambda r, d: [])

    async def _noop_favicons(*a, **k):
        return None
    monkeypatch.setattr(scan_module, "hash_favicons", _noop_favicons)

    async def _noop_index(*a, **k):
        return None
    monkeypatch.setattr(scan_module, "index_scan_pages", _noop_index)

    res = await fresh.create_job("ex.com", "9.9.9.9")
    job_id = res["job_id"]
    selected = [
        {"timestamp": "20200101000000", "url": "http://ex.com/"},
        {"timestamp": "20210101000000", "url": "http://ex.com/a"},
    ]

    # Heartbeat: counts how many times the loop got control (every ~10ms).
    ticks = {"n": 0}
    stop = asyncio.Event()

    async def _heartbeat():
        while not stop.is_set():
            ticks["n"] += 1
            await asyncio.sleep(0.01)

    hb = asyncio.create_task(_heartbeat())
    await scan_module.run_scan(job_id, ScanConfig(), selected_snapshots=selected)
    stop.set()
    await hb

    # If extraction had run inline, the loop would have been frozen for ~0.4s and
    # the heartbeat would have missed ~40 ticks. Offloaded, it keeps ticking, so we
    # must see clearly more than a handful of ticks across the run.
    assert ticks["n"] > 15, f"event loop looked blocked during extraction (ticks={ticks['n']})"

    # The scan still finishes correctly (completed job persisted to the DB; the
    # live in-memory record is dropped on finish, so read from the DB).
    from db import get_job_by_url_id
    persisted = await get_job_by_url_id(res["url_id"])
    assert persisted is not None and persisted["status"] == "completed"


@pytest.mark.asyncio
async def test_live_counts_pushed_during_extraction(fresh_db, monkeypatch):
    """The extraction phase streams per-category counts to the job so findings
    appear on the loading page without a refresh."""
    import store as store_module
    import routers.scan as scan_module

    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    async def _fake_scrape(selected, job_id, **kw):
        # 60 fake html pages -> multiple extraction batches (BATCH=25).
        return [
            {"timestamp": f"20{10+i:02d}0101000000", "url": f"http://ex.com/{i}", "html": "<html></html>", "error": None}
            for i in range(60)
        ]
    monkeypatch.setattr(scan_module, "scrape_snapshots", _fake_scrape)

    # process_page adds one distinct email per page into accum["emails"].
    def _fake_process(page, domain, accum, cat_set, page_seq):
        accum["emails"][page["url"]] = {"first_seen": "2020-01"}
        return True
    monkeypatch.setattr(scan_module, "process_page", _fake_process)
    monkeypatch.setattr(scan_module, "mine_subdomains", lambda pages, d, a, c: None)
    monkeypatch.setattr(scan_module, "finalize_accum", lambda accum, categories=None: {"emails": list(accum["emails"].values())})
    monkeypatch.setattr(scan_module, "merge_analytics_ids", lambda r: None)
    monkeypatch.setattr(scan_module, "compute_highlights", lambda r, d: [])

    # Capture live_counts pushed via update_job.
    seen = []
    orig_update = fresh.update_job
    async def _spy(job_id, **kw):
        if "live_counts" in kw:
            seen.append(kw["live_counts"])
        return await orig_update(job_id, **kw)
    monkeypatch.setattr(fresh, "update_job", _spy)

    res = await fresh.create_job("ex.com", "8.8.8.8")
    selected = [{"timestamp": "20200101000000", "url": "http://ex.com/0"}]
    await scan_module.run_scan(job_id=res["job_id"], config=ScanConfig(), selected_snapshots=selected)

    # Several live-count updates, with the emails count strictly growing across
    # batches (25 -> 50 -> 60).
    assert len(seen) >= 2, seen
    email_counts = [c.get("emails", 0) for c in seen]
    assert email_counts == sorted(email_counts) and email_counts[-1] == 60, email_counts
