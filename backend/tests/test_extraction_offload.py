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

    # Deliberately slow, BLOCKING extraction (~0.4s of synchronous work).
    BLOCK = 0.4
    def _slow_extract(pages, domain, categories=None):
        time.sleep(BLOCK)
        return {"emails": []}
    monkeypatch.setattr(scan_module, "extract_all", _slow_extract)
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
