"""Queue policy (launch hardening): one scan runs at a time, two in-flight
scans per IP (abuse net; per-account fairness is max_active_per_user), and a
deep waiting queue of 100 (waiting jobs send archive.org nothing)."""
import pytest

from store import JobStore, PerIpLimitError, QueueFullError


@pytest.mark.asyncio
async def test_one_scan_active_rest_wait():
    store = JobStore()
    a = await store.create_job("a.com", client_ip="1.1.1.1")
    b = await store.create_job("b.com", client_ip="2.2.2.2")
    assert a["position"] == 0          # first is active
    assert b["position"] == 1          # second waits (max_active_total=1)
    assert len(store.active) == 1
    assert len(store.waiting) == 1


@pytest.mark.asyncio
async def test_two_inflight_scans_per_client_max():
    store = JobStore()
    await store.create_job("a.com", client_ip="1.1.1.1")
    await store.create_job("b.com", client_ip="1.1.1.1")
    with pytest.raises(PerIpLimitError):
        await store.create_job("c.com", client_ip="1.1.1.1")  # 3rd from same IP


@pytest.mark.asyncio
async def test_queue_hard_cap_at_100():
    store = JobStore()
    # 100 distinct clients fill 1 active + 99 waiting = 100 in flight (the cap).
    for i in range(100):
        await store.create_job(f"d{i}.com", client_ip=f"10.0.{i // 250}.{i % 250}")
    assert len(store.active) + len(store.waiting) == 100
    with pytest.raises(QueueFullError):
        await store.create_job("overflow.com", client_ip="10.9.9.200")
