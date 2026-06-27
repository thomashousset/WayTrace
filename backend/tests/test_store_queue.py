"""Tests for the v2 JobStore queue logic."""
import pytest

from config import settings
from store import JobStore, PerIpLimitError, QueueFullError


@pytest.fixture(autouse=True)
def reset_caps(monkeypatch):
    """Force known v2 caps (some tests in this file rely on defaults of 2/3/20)."""
    monkeypatch.setattr(settings, "max_active_total", 2)
    monkeypatch.setattr(settings, "max_active_per_ip", 3)
    monkeypatch.setattr(settings, "max_queue_total", 20)


@pytest.mark.asyncio
async def test_create_job_returns_url_id_job_id_and_position():
    s = JobStore()
    res = await s.create_job("ex.com", "1.1.1.1")
    assert "url_id" in res
    assert "job_id" in res
    assert res["status"] == "queued"
    assert res["position"] == 0  # first job goes directly to active
    assert res["eta_seconds"] == 0


@pytest.mark.asyncio
async def test_third_job_goes_to_waiting_with_position_1():
    s = JobStore()
    await s.create_job("a.com", "1.1.1.1")
    await s.create_job("b.com", "2.2.2.2")
    third = await s.create_job("c.com", "3.3.3.3")
    assert third["position"] == 1
    assert len(s.active) == 2
    assert len(s.waiting) == 1


@pytest.mark.asyncio
async def test_per_ip_limit_blocks_4th_from_same_ip():
    s = JobStore()
    for _ in range(3):
        await s.create_job("ex.com", "9.9.9.9")
    with pytest.raises(PerIpLimitError):
        await s.create_job("ex.com", "9.9.9.9")
    # Different IP is fine
    res = await s.create_job("ex.com", "8.8.8.8")
    assert res["job_id"]


@pytest.mark.asyncio
async def test_global_queue_full_raises():
    s = JobStore()
    for i in range(20):
        await s.create_job("ex.com", f"10.0.0.{i}")
    with pytest.raises(QueueFullError):
        await s.create_job("ex.com", "10.0.1.1")


@pytest.mark.asyncio
async def test_take_next_returns_none_when_no_slot_free():
    s = JobStore()
    for i in range(3):
        await s.create_job("ex.com", f"7.7.7.{i}")
    # 2 active, 1 waiting
    assert await s.take_next() is None


@pytest.mark.asyncio
async def test_take_next_promotes_after_finish():
    s = JobStore()
    a = await s.create_job("ex.com", "1.1.1.1")
    b = await s.create_job("ex.com", "2.2.2.2")
    w = await s.create_job("ex.com", "3.3.3.3")
    assert len(s.waiting) == 1
    await s.finish_job(a["job_id"])
    next_jid = await s.take_next()
    assert next_jid == w["job_id"]
    assert len(s.active) == 2
    assert len(s.waiting) == 0


@pytest.mark.asyncio
async def test_finish_job_decrements_per_ip_counter():
    s = JobStore()
    res = await s.create_job("ex.com", "5.5.5.5")
    assert s.per_ip_count["5.5.5.5"] == 1
    await s.finish_job(res["job_id"])
    assert s.per_ip_count.get("5.5.5.5", 0) == 0
    assert res["job_id"] not in s.active


@pytest.mark.asyncio
async def test_get_position_for_waiting_jobs_is_1_indexed():
    s = JobStore()
    a = await s.create_job("a.com", "1.1.1.1")
    b = await s.create_job("b.com", "2.2.2.2")
    w1 = await s.create_job("c.com", "3.3.3.3")
    w2 = await s.create_job("d.com", "4.4.4.4")
    w3 = await s.create_job("e.com", "5.5.5.5")
    assert s.get_position(w1["job_id"]) == 1
    assert s.get_position(w2["job_id"]) == 2
    assert s.get_position(w3["job_id"]) == 3
    assert s.get_position(a["job_id"]) is None
    assert s.get_position("unknown") is None


@pytest.mark.asyncio
async def test_get_eta_seconds_scales_with_position_and_avg():
    s = JobStore()
    s.avg_scan_seconds = 120.0
    await s.create_job("a.com", "1.1.1.1")
    await s.create_job("b.com", "2.2.2.2")
    w1 = await s.create_job("c.com", "3.3.3.3")
    w2 = await s.create_job("d.com", "4.4.4.4")
    assert s.get_eta_seconds(w1["job_id"]) == 120
    assert s.get_eta_seconds(w2["job_id"]) == 240


@pytest.mark.asyncio
async def test_cancel_waiting_job_frees_slot():
    s = JobStore()
    await s.create_job("a.com", "1.1.1.1")
    await s.create_job("b.com", "2.2.2.2")
    w = await s.create_job("c.com", "3.3.3.3")
    cancelled = await s.cancel_job(w["job_id"])
    assert cancelled is True
    assert w["job_id"] not in s.waiting
    assert s.per_ip_count.get("3.3.3.3", 0) == 0


@pytest.mark.asyncio
async def test_cancel_unknown_returns_false():
    s = JobStore()
    assert await s.cancel_job("nope") is False


@pytest.mark.asyncio
async def test_get_job_by_url_id_resolves():
    s = JobStore()
    res = await s.create_job("x.com", "1.1.1.1")
    j = await s.get_job_by_url_id(res["url_id"])
    assert j is not None
    assert j["id"] == res["job_id"]


@pytest.mark.asyncio
async def test_finish_job_updates_avg_scan_seconds():
    s = JobStore()
    s.avg_scan_seconds = 100.0
    res = await s.create_job("x.com", "1.1.1.1")
    await s.finish_job(res["job_id"], duration_seconds=200.0)
    # EMA: 0.9*100 + 0.1*200 = 110
    assert abs(s.avg_scan_seconds - 110.0) < 0.1


@pytest.mark.asyncio
async def test_active_count_returns_active_plus_waiting():
    s = JobStore()
    await s.create_job("a.com", "1.1.1.1")
    await s.create_job("b.com", "2.2.2.2")
    await s.create_job("c.com", "3.3.3.3")
    assert await s.active_count() == 3
