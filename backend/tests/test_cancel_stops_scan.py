"""Cancelling a running scan stops its work, not only its label.

Before, cancel_job set status="cancelled" and nothing else: the pipeline
kept downloading from archive.org until it ended on its own (up to the scan
timeout) and kept its active slot and per-IP slot meanwhile, so cancel and
resubmit stacked up zombie scans.
"""
import asyncio

import pytest
import pytest_asyncio

from config import settings
from db import get_job_by_url_id, init_db
from models import ScanConfig
from store import JobStore


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    await init_db(str(tmp_path / "wt.db"))
    import routers.scan as scan_module
    import store as store_module
    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)
    monkeypatch.setattr(settings, "max_active_total", 2)
    monkeypatch.setattr(settings, "max_active_per_ip", 3)
    state = {"emitted": 0, "cancelled": False}

    async def endless_scrape(selected, job_id, on_page=None, stats=None, deadline=None):
        try:
            while True:
                state["emitted"] += 1
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
    monkeypatch.setattr(scan_module, "scrape_snapshots", endless_scrape)
    yield fresh, scan_module, state
    import db as _db
    _db._db_path = None


SELECTED = [{"timestamp": "20200101000000", "url": "http://ex.com/"}]


@pytest.mark.asyncio
async def test_cancelling_a_running_scan_stops_the_download_and_frees_the_slot(env):
    fresh, scan_module, state = env
    res = await fresh.create_job("ex.com", "6.6.6.6")
    task = asyncio.create_task(scan_module.run_scan(
        job_id=res["job_id"], config=ScanConfig(), selected_snapshots=SELECTED))
    await asyncio.sleep(0.2)
    assert (await fresh.get_job(res["job_id"]))["status"] == "running"
    assert await fresh.cancel_job(res["job_id"]) is True

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    n = state["emitted"]
    await asyncio.sleep(0.1)
    assert state["cancelled"] is True
    assert state["emitted"] == n, "the download kept going after the cancel"
    assert res["job_id"] not in fresh.active
    assert fresh.per_ip_count.get("6.6.6.6", 0) == 0
    assert await get_job_by_url_id(res["url_id"]) is None
    assert fresh._tasks == {}


@pytest.mark.asyncio
async def test_a_job_promoted_but_not_started_is_skipped_not_killed(env):
    """Its task has not reached the finally that frees the slot, so it must
    not be cancelled: run_scan's own start check finishes it."""
    fresh, scan_module, state = env
    res = await fresh.create_job("ex.com", "7.7.7.7")
    assert res["job_id"] in fresh.active          # promoted, not started
    assert await fresh.cancel_job(res["job_id"]) is True
    await scan_module.run_scan(job_id=res["job_id"], config=ScanConfig(), selected_snapshots=SELECTED)
    assert state["emitted"] == 0
    assert res["job_id"] not in fresh.active
    assert fresh.per_ip_count.get("7.7.7.7", 0) == 0


@pytest.mark.asyncio
async def test_a_finished_scan_leaves_no_task_behind(env, monkeypatch):
    fresh, scan_module, _ = env

    async def quick_pipeline(job_id, *a, **kw):
        await fresh.update_job(job_id, status="completed", meta={}, results={})
    monkeypatch.setattr(scan_module, "_scan_pipeline", quick_pipeline)
    res = await fresh.create_job("ex.com", "8.8.8.8")
    await scan_module.run_scan(job_id=res["job_id"], config=ScanConfig(), selected_snapshots=SELECTED)
    assert fresh._tasks == {}
    assert (await get_job_by_url_id(res["url_id"]))["status"] == "completed"
