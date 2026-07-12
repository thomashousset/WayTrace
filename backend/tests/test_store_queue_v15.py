"""v1.5 queue policy: one scan at a time, one in-flight scan per client, queue 15."""
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
async def test_one_inflight_scan_per_client():
    store = JobStore()
    await store.create_job("a.com", client_ip="1.1.1.1")
    with pytest.raises(PerIpLimitError):
        await store.create_job("b.com", client_ip="1.1.1.1")  # same IP, already in flight


@pytest.mark.asyncio
async def test_queue_hard_cap_at_15():
    store = JobStore()
    # 15 distinct clients fill 1 active + 14 waiting = 15 in flight (the cap).
    for i in range(15):
        await store.create_job(f"d{i}.com", client_ip=f"10.0.0.{i}")
    assert len(store.active) + len(store.waiting) == 15
    with pytest.raises(QueueFullError):
        await store.create_job("overflow.com", client_ip="10.0.0.200")
