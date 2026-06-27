# backend/tests/test_rate_limiter.py
"""Tests for the auto-adaptive rate limiter."""
import asyncio
import time

import pytest

from services.rate_limiter import RateLimiter


@pytest.fixture
def limiter():
    return RateLimiter(
        initial_delay=1.0,
        min_delay=0.3,
        max_delay=10.0,
        speedup_factor=0.9,
        speedup_streak=3,
        backoff_factor=2.0,
        pause_429=2.0,
    )


def test_initial_delay(limiter):
    assert limiter.delay == 1.0
    assert limiter.success_streak == 0
    assert limiter.backoff_level == 0


def test_on_success_increments_streak(limiter):
    limiter.on_success()
    assert limiter.success_streak == 1
    assert limiter.delay == 1.0


def test_on_success_streak_speeds_up(limiter):
    for _ in range(3):
        limiter.on_success()
    assert limiter.delay == pytest.approx(0.9)
    assert limiter.success_streak == 0


def test_on_success_respects_min_delay(limiter):
    limiter.delay = 0.31
    for _ in range(3):
        limiter.on_success()
    assert limiter.delay == pytest.approx(0.31 * 0.9)
    limiter.delay = 0.3
    for _ in range(3):
        limiter.on_success()
    assert limiter.delay == 0.3


def test_on_429_doubles_delay(limiter):
    limiter.on_429()
    assert limiter.delay == 2.0
    assert limiter.backoff_level == 1
    assert limiter.success_streak == 0


def test_on_429_caps_at_max(limiter):
    limiter.delay = 6.0
    limiter.on_429()
    assert limiter.delay == 10.0


def test_on_429_increases_backoff_level(limiter):
    limiter.on_429()
    limiter.on_429()
    assert limiter.backoff_level == 2


def test_on_error_moderate_backoff(limiter):
    limiter.on_error()
    assert limiter.delay == pytest.approx(1.5)
    assert limiter.success_streak == 0


def test_on_error_caps_at_max(limiter):
    limiter.delay = 8.0
    limiter.on_error()
    assert limiter.delay == 10.0


@pytest.mark.asyncio
async def test_wait_sleeps_approximately_delay(limiter):
    limiter.delay = 0.05
    start = time.monotonic()
    await limiter.wait()
    elapsed = time.monotonic() - start
    assert 0.04 <= elapsed <= 0.1


def test_get_state_returns_dict(limiter):
    state = limiter.get_state()
    assert state["delay"] == 1.0
    assert state["success_streak"] == 0
    assert state["backoff_level"] == 0


def test_restore_state(limiter):
    limiter.on_429()
    limiter.on_429()
    state = limiter.get_state()

    new_limiter = RateLimiter(
        initial_delay=1.0, min_delay=0.3, max_delay=10.0,
        speedup_factor=0.9, speedup_streak=3,
        backoff_factor=2.0, pause_429=2.0,
    )
    new_limiter.restore_state(state)
    assert new_limiter.delay == limiter.delay
    assert new_limiter.backoff_level == limiter.backoff_level
