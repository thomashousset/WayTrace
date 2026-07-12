"""archive_health: 'slow' must reflect SUSTAINED slowness, not single spikes."""
from __future__ import annotations

import time

from services import archive_health as ah


def _reset():
    with ah._lock:
        ah._fails.clear()
        ah._hard_fails.clear()
        ah._open_until = 0.0
        ah._tripped_hard = False
        ah._hard_streak = 0
        ah._last_hard_trip_at = 0.0
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


def test_threshold_failures_pause_the_breaker():
    for _ in range(ah._FAIL_THRESHOLD):
        ah.record_failure()
    s = ah.status()
    assert s["state"] == "paused"
    assert s["cooldown_remaining"] > 0
    assert ah.is_open()


def test_below_threshold_does_not_pause():
    for _ in range(ah._FAIL_THRESHOLD - 1):
        ah.record_failure()
    assert not ah.is_open()


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


# ---- hard IP-block detection (connection refused) ----

def test_hard_block_trips_fast_at_base_cooldown():
    # Fewer refusals than the soft threshold, but it still trips - as a hard
    # block, on the (short) base cooldown for a first/isolated refusal.
    from config import settings
    for _ in range(ah._HARD_FAIL_THRESHOLD):
        ah.record_hard_block()
    assert ah.is_open()
    assert ah.is_hard_block()
    assert abs(ah.seconds_remaining() - settings.archive_hard_cooldown_base) <= 2


def test_hard_block_message_says_blocked():
    for _ in range(ah._HARD_FAIL_THRESHOLD):
        ah.record_hard_block()
    msg = ah.status()["message"].lower()
    assert "block" in msg


def test_single_hard_block_below_threshold_stays_closed():
    ah.record_hard_block()  # one refusal is not yet a confirmed block
    assert not ah.is_open()


def test_success_does_not_reopen_during_hard_cooldown():
    # A stray success while the hard pause is in force must NOT reopen the gate;
    # the cooldown has to elapse first.
    for _ in range(ah._HARD_FAIL_THRESHOLD):
        ah.record_hard_block()
    assert ah.is_open()
    ah.record_success()
    assert ah.is_open()
    assert ah._tripped_hard is True


def test_success_clears_hard_flag_after_cooldown():
    for _ in range(ah._HARD_FAIL_THRESHOLD):
        ah.record_hard_block()
    # Force the cooldown to have elapsed, then a clean response recovers.
    with ah._lock:
        ah._open_until = 0.0
    ah.record_success()
    assert not ah.is_open()
    assert ah._tripped_hard is False


def test_intermittent_refusals_still_trip_the_breaker():
    # The 2600.eu case: refusals interleaved with successes. A success used to
    # reset the hard-fail streak, so it never tripped. It must now trip.
    ah.record_hard_block()
    ah.record_success()
    ah.record_hard_block()   # 2 refusals within the window, despite the success
    assert ah.is_open()
    assert ah._tripped_hard is True


# ---- escalating hard-block cooldown (2 min first, doubling on consecutive) ----

def _trip(now, monkeypatch):
    """Force a hard-block trip 'at time now' by feeding the threshold of refusals.
    monkeypatch restores ah.time.time after the test so real-time tests are safe."""
    monkeypatch.setattr(ah.time, "time", lambda: now)
    for _ in range(ah._HARD_FAIL_THRESHOLD):
        ah.record_hard_block()


def test_first_hard_block_is_two_minutes(monkeypatch):
    _trip(1000.0, monkeypatch)
    assert 118 <= ah.seconds_remaining() <= 121  # base 120s, not the old 1800
    assert ah.is_hard_block() is True


def test_consecutive_blocks_escalate(monkeypatch):
    _trip(1000.0, monkeypatch)             # streak 0 -> 120s
    _trip(1000.0 + 130, monkeypatch)       # within streak_reset -> streak 1 -> 240s
    monkeypatch.setattr(ah.time, "time", lambda: 1000.0 + 130)
    assert 238 <= ah.seconds_remaining() <= 241


def test_quiet_gap_resets_to_base(monkeypatch):
    _trip(1000.0, monkeypatch)             # 120s
    _trip(1000.0 + 5000, monkeypatch)      # gap > streak_reset(900) -> back to streak 0 -> 120s
    monkeypatch.setattr(ah.time, "time", lambda: 1000.0 + 5000)
    assert 118 <= ah.seconds_remaining() <= 121


def test_cooldown_is_capped(monkeypatch):
    t = 1000.0
    for _ in range(8):                     # many consecutive trips
        _trip(t, monkeypatch)
        t += 130
    monkeypatch.setattr(ah.time, "time", lambda: t - 130)
    assert ah.seconds_remaining() <= 1800
