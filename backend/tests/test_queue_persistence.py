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
        config=ScanConfig(cap=500),
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
    latest = max(v for v, _ in dbmod.MIGRATIONS)
    await dbmod.init_db(p)                       # full schema at latest version
    conn = await aiosqlite.connect(p)
    await conn.execute("PRAGMA user_version = 6")  # rewind so v7+ re-run
    await conn.commit()
    await conn.close()
    await dbmod.init_db(p)                       # must NOT raise duplicate column
    conn = await aiosqlite.connect(p)
    cur = await conn.execute("PRAGMA user_version")
    ver = (await cur.fetchone())[0]
    await conn.close()
    assert ver == latest


# --- a job that keeps bringing the process down is not restarted forever ----

async def _crash_mid_run(url_id):
    """What a run that dies with the process leaves behind: the row moved to
    running (one more start counted) and never finished."""
    await dbmod.update_job_queue_status(url_id, "running")


@pytest.mark.asyncio
async def test_a_job_started_three_times_is_not_restored_a_fourth(tmp_db):
    s1 = JobStore()
    job = await s1.create_job("heavy.com", "1.1.1.1")
    for boot in range(3):
        await _crash_mid_run(job["url_id"])
        s = JobStore()
        restored = await s.restore_pending_jobs()
        if boot < 2:
            assert restored == 1, f"boot {boot}: a deploy or a rollback must not cost the scan"
    assert restored == 0
    row = await dbmod.get_job_by_url_id(job["url_id"])
    assert row["status"] == "failed"
    assert row["meta"]["error"] == "restart_loop"
    # and it stays out on every later boot
    assert await JobStore().restore_pending_jobs() == 0


@pytest.mark.asyncio
async def test_queued_jobs_never_started_are_not_counted(tmp_db):
    s1 = JobStore()
    await s1.create_job("live.com", "1.1.1.1")
    waiting = await s1.create_job("waiting.com", "2.2.2.2")
    for _ in range(5):
        assert await JobStore().restore_pending_jobs() == 2
    row = await dbmod.get_job_by_url_id(waiting["url_id"])
    assert row["status"] == "queued"


@pytest.mark.asyncio
async def test_migration_18_adds_the_counter_to_existing_rows(tmp_db):
    s1 = JobStore()
    job = await s1.create_job("old.com", "1.1.1.1")
    conn = await dbmod.get_db()
    try:
        cur = await conn.execute("SELECT start_attempts FROM jobs WHERE url_id = ?", (job["url_id"],))
        assert (await cur.fetchone())[0] == 0
    finally:
        await conn.close()
