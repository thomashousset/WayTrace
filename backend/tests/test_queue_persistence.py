"""The waiting queue survives a container restart (persisted in SQLite)."""
import pytest
import pytest_asyncio

import db as dbmod
from config import settings
from models import ScanConfig
from store import JobStore


@pytest.fixture(autouse=True)
def caps(monkeypatch):
    monkeypatch.setattr(settings, "max_active_total", 1)
    monkeypatch.setattr(settings, "max_active_per_ip", 10)
    monkeypatch.setattr(settings, "max_queue_total", 100)


@pytest_asyncio.fixture()
async def tmp_db(tmp_path):
    await dbmod.init_db(str(tmp_path / "t.db"))
    yield


@pytest.mark.asyncio
async def test_queued_job_restored_after_restart(tmp_db):
    s1 = JobStore()
    await s1.create_job("live.com", "1.1.1.1")          # goes active
    second = await s1.create_job(
        "waiting.com", "2.2.2.2", user_id=7,
        config=ScanConfig(cap=500), publish_on_complete=True,
    )
    # "restart": a brand-new store restores from the DB
    s2 = JobStore()
    n = await s2.restore_pending_jobs()
    assert n == 2
    job = await s2.get_job_by_url_id(second["url_id"])
    assert job is not None
    assert job["id"] == second["job_id"]                 # same job_id
    assert job["status"] == "queued"
    assert job["config"].cap == 500
    assert job["publish_on_complete"] is True
    assert job["user_id"] == 7
    assert len(s2.waiting) == 2                          # both wait; worker promotes


@pytest.mark.asyncio
async def test_restore_is_idempotent(tmp_db):
    s1 = JobStore()
    await s1.create_job("a.com", "1.1.1.1")
    s2 = JobStore()
    assert await s2.restore_pending_jobs() == 1
    assert await s2.restore_pending_jobs() == 0
    assert len(s2._jobs) == 1


@pytest.mark.asyncio
async def test_finished_jobs_not_restored(tmp_db):
    s1 = JobStore()
    first = await s1.create_job("done.com", "1.1.1.1")
    await dbmod.update_job_queue_status(first["url_id"], "completed")
    s2 = JobStore()
    assert await s2.restore_pending_jobs() == 0


@pytest.mark.asyncio
async def test_running_job_restored_as_queued(tmp_db):
    s1 = JobStore()
    first = await s1.create_job("mid.com", "1.1.1.1")
    await dbmod.update_job_queue_status(first["url_id"], "running")
    s2 = JobStore()
    assert await s2.restore_pending_jobs() == 1
    job = await s2.get_job_by_url_id(first["url_id"])
    assert job["status"] == "queued"                     # restarts from zero


@pytest.mark.asyncio
async def test_migration_v7_idempotent_after_partial_apply(tmp_path):
    """A crash between v7's ALTERs and the version bump must not crash-loop:
    re-running init_db on a DB already carrying the new columns is a no-op."""
    import aiosqlite
    p = str(tmp_path / "partial.db")
    await dbmod.init_db(p)                       # full schema at v7
    conn = await aiosqlite.connect(p)
    await conn.execute("PRAGMA user_version = 6")  # rewind so v7 re-runs
    await conn.commit()
    await conn.close()
    await dbmod.init_db(p)                       # must NOT raise duplicate column
    conn = await aiosqlite.connect(p)
    cur = await conn.execute("PRAGMA user_version")
    ver = (await cur.fetchone())[0]
    await conn.close()
    assert ver == 7
