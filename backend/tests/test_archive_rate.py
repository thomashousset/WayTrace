"""Tests for the adaptive (AIMD) archive.org rate governor."""
import asyncio
import time

import pytest

from config import settings
from services import archive_rate


@pytest.fixture(autouse=True)
def _reset():
    archive_rate.reset()
    yield
    archive_rate.reset()


# ---- AIMD: multiplicative decrease on refusal ----

def test_refusal_halves_the_rate(monkeypatch):
    monkeypatch.setattr(settings, "archive_rate_per_minute", 120)  # start 2/s
    monkeypatch.setattr(settings, "archive_rate_min", 30)          # floor 0.5/s
    monkeypatch.setattr(settings, "archive_rate_decrease_factor", 0.5)
    archive_rate.reset()
    assert archive_rate.current_rate_per_minute() == 120
    archive_rate.report_refusal()
    assert archive_rate.current_rate_per_minute() == 60
    archive_rate.report_refusal()
    assert archive_rate.current_rate_per_minute() == 30   # hit the floor
    archive_rate.report_refusal()
    assert archive_rate.current_rate_per_minute() == 30   # clamped at the floor


# ---- AIMD: additive increase after a clean interval ----

def test_success_increases_only_after_interval(monkeypatch):
    monkeypatch.setattr(settings, "archive_rate_per_minute", 90)
    monkeypatch.setattr(settings, "archive_rate_max", 150)
    monkeypatch.setattr(settings, "archive_rate_step", 30)
    monkeypatch.setattr(settings, "archive_rate_increase_interval", 0.05)
    archive_rate.reset()

    time.sleep(0.06)
    archive_rate.report_success()
    assert archive_rate.current_rate_per_minute() == 120   # +30
    # a second success within the interval must NOT bump again
    archive_rate.report_success()
    assert archive_rate.current_rate_per_minute() == 120
    time.sleep(0.06)
    archive_rate.report_success()
    assert archive_rate.current_rate_per_minute() == 150   # +30, capped at max
    time.sleep(0.06)
    archive_rate.report_success()
    assert archive_rate.current_rate_per_minute() == 150   # clamped at the cap


def test_refusal_holds_off_the_next_increase(monkeypatch):
    monkeypatch.setattr(settings, "archive_rate_per_minute", 90)
    monkeypatch.setattr(settings, "archive_rate_max", 300)
    monkeypatch.setattr(settings, "archive_rate_step", 30)
    monkeypatch.setattr(settings, "archive_rate_min", 30)
    monkeypatch.setattr(settings, "archive_rate_increase_interval", 0.05)
    archive_rate.reset()

    archive_rate.report_refusal()                 # rate -> 45, clock reset
    r = archive_rate.current_rate_per_minute()
    archive_rate.report_success()                 # too soon after the refusal
    assert archive_rate.current_rate_per_minute() == r
    time.sleep(0.06)
    archive_rate.report_success()                 # interval elapsed -> may rise
    assert archive_rate.current_rate_per_minute() > r


# ---- the bucket honours the (adaptive) rate ----

@pytest.mark.asyncio
async def test_bucket_spaces_requests_at_current_rate(monkeypatch):
    monkeypatch.setattr(settings, "archive_rate_per_minute", 1200)  # 20/s
    monkeypatch.setattr(settings, "archive_rate_burst", 1)
    monkeypatch.setattr(settings, "archive_rate_min", 60)
    monkeypatch.setattr(settings, "archive_rate_decrease_factor", 0.5)
    archive_rate.reset()
    # Drop the rate hard, then time 6 acquires: at ~10/s they take >= ~0.45s.
    archive_rate.report_refusal()   # 20 -> 10/s
    start = time.monotonic()
    for _ in range(6):
        await archive_rate.acquire()
    assert time.monotonic() - start >= 0.4


# ---- concurrency cap via slot() (the 5-users guarantee) ----

@pytest.mark.asyncio
async def test_slot_bounds_concurrent_connections(monkeypatch):
    monkeypatch.setattr(settings, "archive_rate_per_minute", 60000)  # rate not the limiter
    monkeypatch.setattr(settings, "archive_rate_burst", 100)
    archive_rate.reset()
    sem = asyncio.Semaphore(2)
    inside = 0
    peak = 0

    async def caller():
        nonlocal inside, peak
        async with archive_rate.slot(sem):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.02)
            inside -= 1

    await asyncio.gather(*(caller() for _ in range(10)))
    assert peak <= 2
