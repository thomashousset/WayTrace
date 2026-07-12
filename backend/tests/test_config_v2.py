"""Tests for v2 public-mode tuning settings."""
from config import settings


def test_max_active_total_default():
    # v1.5: one scan at a time so aggregate archive.org load stays minimal; the
    # global adaptive rate governor is the real ceiling.
    assert settings.max_active_total == 1


def test_max_queue_total_default():
    assert settings.max_queue_total == 15


def test_max_active_per_ip_default():
    # v1.5: one in-flight scan per client - a user can't stack a second.
    assert settings.max_active_per_ip == 1


def test_scan_retention_days_default():
    assert settings.scan_retention_days == 7


def test_cleanup_interval_seconds_default():
    assert settings.cleanup_interval_seconds == 3600
