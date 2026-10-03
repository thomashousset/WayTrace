"""Tests for the queue worker + cleanup background tasks."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from config import settings
from db import init_db, save_job, get_job_by_url_id, delete_expired_jobs
from services.background_tasks import (
    queue_worker_tick,
    queue_worker_loop,
    cleanup_loop,
)
from store import JobStore


@pytest.fixture(autouse=True)
def reset_caps(monkeypatch):
    monkeypatch.setattr(settings, "max_active_total", 2)
    monkeypatch.setattr(settings, "max_active_per_ip", 3)
    monkeypatch.setattr(settings, "max_queue_total", 20)


@pytest_asyncio.fixture
async def fresh_db(tmp_path):
    p = str(tmp_path / "wt.db")
    await init_db(p)
    yield p
    import db as _db
    _db._db_path = None


# ---------- queue worker ----------

@pytest.mark.asyncio
async def test_tick_returns_none_when_no_waiting():
    s = JobStore()
    runs = []

    async def fake_run(jid):
        runs.append(jid)

    promoted = await queue_worker_tick(s, fake_run)
    assert promoted is None
    assert runs == []


@pytest.mark.asyncio
async def test_tick_promotes_waiting_to_active_after_slot_free():
    s = JobStore()
    a = await s.create_job("e.com", "1.1.1.1")
    b = await s.create_job("e.com", "2.2.2.2")
    w = await s.create_job("e.com", "3.3.3.3")
    assert len(s.waiting) == 1

    runs = []

    async def fake_run(jid):
        runs.append(jid)

    # No slot free yet
    promoted = await queue_worker_tick(s, fake_run)
    assert promoted is None

    # Free a slot, then tick
    await s.finish_job(a["job_id"])
    promoted = await queue_worker_tick(s, fake_run)
    assert promoted == w["job_id"]
    await asyncio.sleep(0)  # let the scheduled task run
    assert runs == [w["job_id"]]


@pytest.mark.asyncio
async def test_worker_loop_can_be_cancelled():
    s = JobStore()

    async def fake_run(jid):
        pass

    task = asyncio.create_task(queue_worker_loop(s, fake_run, tick_seconds=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_worker_loop_swallows_errors_and_keeps_running():
    s = JobStore()
    calls = []

    async def fake_run(jid):
        pass

    # Wait for the third tick instead of sleeping a fixed 0.1s and hoping three
    # 0.01s ticks fit inside it. They did on an idle machine and did not under
    # the full suite, which made this fail for reasons that had nothing to do
    # with the behaviour under test. The generous ceiling is only there so a
    # loop that really does die still fails the test rather than hanging.
    reached = asyncio.Event()

    async def fake_take_next_raises_and_signal():
        calls.append(1)
        if len(calls) >= 3:
            reached.set()
        if len(calls) < 3:
            raise RuntimeError("boom")
        return None

    s.take_next = fake_take_next_raises_and_signal  # type: ignore[assignment]

    task = asyncio.create_task(queue_worker_loop(s, fake_run, tick_seconds=0.01))
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert len(calls) >= 3  # loop kept ticking past the exceptions


# ---------- cleanup ----------

@pytest.mark.asyncio
async def test_cleanup_loop_runs_at_least_once(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "cleanup_interval_seconds", 0.05)

    now = datetime.now(timezone.utc)
    await save_job(
        url_id="dead", domain="x.com", client_ip="1.1.1.1",
        created_at=now - timedelta(days=8),
        expires_at=now - timedelta(hours=1),
        status="completed", meta={}, results={},
    )
    assert await get_job_by_url_id("dead") is not None

    task = asyncio.create_task(cleanup_loop())
    await asyncio.sleep(0.15)  # give the loop a chance to tick
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert await get_job_by_url_id("dead") is None
