"""Duplicate submissions of a domain already in flight attach to the live scan."""
import pytest

from config import settings
from store import JobStore


@pytest.fixture(autouse=True)
def caps(monkeypatch):
    monkeypatch.setattr(settings, "max_active_total", 2)
    monkeypatch.setattr(settings, "max_active_per_ip", 10)
    monkeypatch.setattr(settings, "max_queue_total", 100)


@pytest.mark.asyncio
async def test_finds_live_job_same_domain():
    s = JobStore()
    first = await s.create_job("dup.com", "1.1.1.1", user_id=1)
    live = await s.find_live_job_for_domain("dup.com")
    assert live is not None
    assert live["url_id"] == first["url_id"]


@pytest.mark.asyncio
async def test_no_live_job_other_domain():
    s = JobStore()
    await s.create_job("dup.com", "1.1.1.1")
    assert await s.find_live_job_for_domain("other.com") is None


@pytest.mark.asyncio
async def test_finished_job_not_returned():
    s = JobStore()
    first = await s.create_job("dup.com", "1.1.1.1")
    await s.finish_job(first["job_id"])
    assert await s.find_live_job_for_domain("dup.com") is None


@pytest.mark.asyncio
async def test_cancelled_job_not_returned():
    s = JobStore()
    first = await s.create_job("dup.com", "1.1.1.1")
    await s.cancel_job(first["job_id"])
    assert await s.find_live_job_for_domain("dup.com") is None


@pytest.mark.asyncio
async def test_oldest_live_job_wins():
    s = JobStore()
    first = await s.create_job("dup.com", "1.1.1.1")
    await s.create_job("dup.com", "2.2.2.2")   # forced duplicate (force=True path)
    live = await s.find_live_job_for_domain("dup.com")
    assert live["url_id"] == first["url_id"]


@pytest.mark.asyncio
async def test_cancel_waiting_job_removes_it_from_store():
    """A queued-then-cancelled job is fully dropped (no memory/phantom leak)."""
    s = JobStore()
    await s.create_job("a.com", "1.1.1.1")             # active
    await s.create_job("b.com", "2.2.2.2")             # active (cap=2)
    waiting = await s.create_job("wait.com", "3.3.3.3")  # waiting
    assert waiting["position"] >= 1                     # confirm it waits
    assert await s.cancel_job(waiting["job_id"]) is True
    assert await s.get_job_by_url_id(waiting["url_id"]) is None
    assert waiting["job_id"] not in s._jobs
    assert s.per_ip_count.get("3.3.3.3", 0) == 0


@pytest.mark.asyncio
async def test_cancel_running_job_left_for_finish():
    """A running job is only flagged; finish_job releases its slot once."""
    s = JobStore()
    first = await s.create_job("run.com", "1.1.1.1")     # active/running
    await s.update_job(first["job_id"], status="running")
    assert await s.cancel_job(first["job_id"]) is True
    job = await s.get_job(first["job_id"])
    assert job["status"] == "cancelled"
    assert first["job_id"] in s.active                    # still there for finish
    await s.finish_job(first["job_id"])
    assert s.per_ip_count.get("1.1.1.1", 0) == 0          # released exactly once
