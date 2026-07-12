"""Shared test fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _reset_archive_singletons():
    """The circuit breaker (archive_health) and the adaptive rate governor
    (archive_rate) are process-wide singletons. Reset them around every test so
    one test's tripped breaker or lowered rate can never leak into the next
    (which otherwise makes is_hard_block() skip pages or create_scan return 503)."""
    from services import archive_health as _ah, archive_rate as _ar

    def _clear():
        _ar.reset()
        with _ah._lock:
            _ah._fails.clear()
            _ah._hard_fails.clear()
            _ah._latencies.clear()
            _ah._open_until = 0.0
            _ah._tripped_hard = False
            _ah._last_latency = 0.0
            _ah._last_latency_at = 0.0

    _clear()
    yield
    _clear()
