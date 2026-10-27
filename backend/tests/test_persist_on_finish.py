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


@pytest.mark.asyncio
async def test_persist_and_finish_skips_cancelled_job(fresh_db, monkeypatch):
    # A scan the user deleted while it was running (status=cancelled) must NOT
    # be re-persisted: that would resurrect the row delete_scan removed, with a
    # fresh 7-day expiry, back into My scans / the feed.
    import store as store_module
    import routers.scan as scan_module
    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    res = await fresh.create_job("gone.com", "3.3.3.3")
    await fresh.update_job(res["job_id"], status="cancelled", step="Cancelled")

    await scan_module._persist_and_finish(res["job_id"], 0.0)

    # Not written to the DB, and the per-IP slot is still freed.
    assert await get_job_by_url_id(res["url_id"]) is None
    assert fresh.per_ip_count.get("3.3.3.3", 0) == 0
    assert res["job_id"] not in fresh.active


@pytest.mark.asyncio
async def test_a_scan_deleted_while_running_stays_deleted(fresh_db, monkeypatch):
    """delete_scan cancels the live job, then the pipeline, still running,
    finishes with update_job(status="completed"). That write used to replace
    "cancelled", so _persist_and_finish re-saved the deleted row."""
    import store as store_module
    import routers.scan as scan_module
    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    async def pipeline(job_id, *a, **kw):
        await asyncio.sleep(0.2)
        await fresh.update_job(job_id, status="completed", step="Done",
                               meta={"x": 1}, results={"emails": []})
    monkeypatch.setattr(scan_module, "_scan_pipeline", pipeline)

    res = await fresh.create_job("example.com", "1.1.1.1")
    task = asyncio.create_task(scan_module.run_scan(res["job_id"]))
    await asyncio.sleep(0.05)
    assert await fresh.cancel_job(res["job_id"]) is True
    # cancel_job now stops the task too; either way the row must stay gone.
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert await get_job_by_url_id(res["url_id"]) is None
    assert fresh.per_ip_count.get("1.1.1.1", 0) == 0
    assert res["job_id"] not in fresh.active


@pytest.mark.asyncio
async def test_cancelled_is_final_but_other_fields_still_move():
    s = JobStore()
    res = await s.create_job("example.com", "1.1.1.1")
    await s.update_job(res["job_id"], status="running")
    await s.cancel_job(res["job_id"])
    await s.update_job(res["job_id"], status="completed", step="Done", progress=100)
    job = await s.get_job(res["job_id"])
    assert (job["status"], job["step"], job["progress"]) == ("cancelled", "Cancelled", 100)
