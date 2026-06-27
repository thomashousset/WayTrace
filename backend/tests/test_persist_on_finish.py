"""Integration test for run_scan -> _persist_and_finish:
verifies that completed/failed jobs end up in the DB and queue slots free.
"""
import asyncio

import pytest
import pytest_asyncio

from config import settings
from db import init_db, get_job_by_url_id
from store import JobStore


@pytest_asyncio.fixture
async def fresh_db(tmp_path):
    p = str(tmp_path / "wt.db")
    await init_db(p)
    yield p
    import db as _db
    _db._db_path = None


@pytest.fixture(autouse=True)
def reset_caps(monkeypatch):
    monkeypatch.setattr(settings, "max_active_total", 2)
    monkeypatch.setattr(settings, "max_active_per_ip", 3)
    monkeypatch.setattr(settings, "max_queue_total", 20)


@pytest.mark.asyncio
async def test_persist_and_finish_writes_completed_job_to_db(fresh_db, monkeypatch):
    # Use a fresh JobStore and patch routers.scan.store to point at it
    import store as store_module
    import routers.scan as scan_module
    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    res = await fresh.create_job("example.com", "1.1.1.1")
    job_id = res["job_id"]
    url_id = res["url_id"]

    # Simulate scan completion: update job state in store, then call finish
    await fresh.update_job(job_id, status="completed", meta={"x": 1}, results={"emails": []})

    start = 0.0  # any sentinel; duration is computed from time.time() - start
    await scan_module._persist_and_finish(job_id, start)

    job = await get_job_by_url_id(url_id)
    assert job is not None
    assert job["status"] == "completed"
    assert job["meta"] == {"x": 1}
    assert job["results"] == {"emails": []}
    assert job["completed_at"] is not None
    # Queue slot freed
    assert fresh.per_ip_count.get("1.1.1.1", 0) == 0
    assert job_id not in fresh.active


@pytest.mark.asyncio
async def test_persist_and_finish_handles_failed_job(fresh_db, monkeypatch):
    import store as store_module
    import routers.scan as scan_module
    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    res = await fresh.create_job("err.com", "2.2.2.2")
    await fresh.update_job(res["job_id"], status="failed", step="Scan failed")

    await scan_module._persist_and_finish(res["job_id"], 0.0)

    job = await get_job_by_url_id(res["url_id"])
    assert job is not None
    assert job["status"] == "failed"
    assert job["completed_at"] is not None
    assert fresh.per_ip_count.get("2.2.2.2", 0) == 0


@pytest.mark.asyncio
async def test_persist_and_finish_noop_for_unknown_job(fresh_db, monkeypatch):
    import store as store_module
    import routers.scan as scan_module
    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    await scan_module._persist_and_finish("nope", 0.0)
    # Should not raise; DB should remain empty
