"""archive_health: 'slow' must reflect SUSTAINED slowness, not single spikes."""
from __future__ import annotations

import time

from services import archive_health as ah


def _reset():
    with ah._lock:
        ah._fails.clear()
        ah._open_until = 0.0
        ah._latencies.clear()
        ah._last_latency = 0.0
        ah._last_latency_at = 0.0


def setup_function():
    _reset()


def test_ok_by_default():
    assert ah.status()["state"] == "ok"


def test_single_slow_spike_stays_ok():
    # One 15s CDX call (archive.org jitter) must NOT trip the banner.
    ah.record_latency(15.0)
    assert ah.status()["state"] == "ok"


def test_two_slow_samples_still_ok_below_min():
    ah.record_latency(20.0)
    ah.record_latency(20.0)
    # Below _SLOW_MIN_SAMPLES (3): not enough evidence yet.
    assert ah.status()["state"] == "ok"


def test_sustained_slow_median_trips():
    for _ in range(3):
        ah.record_latency(12.0)
    s = ah.status()
    assert s["state"] == "slow"
    assert s["last_latency"] >= 8.0


def test_one_spike_among_fast_calls_stays_ok():
    for _ in range(5):
        ah.record_latency(1.0)
    ah.record_latency(45.0)  # single spike
    assert ah.status()["state"] == "ok"  # median ~1s


def test_very_slow_label_when_median_doubles_threshold():
    for _ in range(4):
        ah.record_latency(20.0)
    assert "very slow" in ah.status()["message"]


def test_single_failure_stays_ok():
    ah.record_failure()
    assert ah.status()["state"] == "ok"


def test_two_failures_read_as_slow():
    ah.record_failure()
    ah.record_failure()
    assert ah.status()["state"] == "slow"


def test_three_failures_pause_the_breaker():
    for _ in range(3):
        ah.record_failure()
    s = ah.status()
    assert s["state"] == "paused"
    assert s["cooldown_remaining"] > 0
    assert ah.is_open()


def test_stale_latency_samples_age_out():
    ah.record_latency(30.0)
    ah.record_latency(30.0)
    ah.record_latency(30.0)
    assert ah.status()["state"] == "slow"
    # Backdate every sample beyond the window: should recover to ok.
    with ah._lock:
        old = time.time() - ah._LAT_WINDOW - 10
        ah._latencies[:] = [(old, sec) for (_, sec) in ah._latencies]
        ah._last_latency_at = old
    assert ah.status()["state"] == "ok"


def test_success_clears_failure_streak_but_not_latency_history():
    ah.record_latency(12.0)
    ah.record_latency(12.0)
    ah.record_failure()
    ah.record_success()  # called after every clean CDX response
    # Failure streak cleared, but the two slow samples are still remembered.
    with ah._lock:
        assert len(ah._fails) == 0
        assert len(ah._latencies) == 2
