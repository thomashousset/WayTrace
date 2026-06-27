"""Tests for v2 public-mode tuning settings."""
from config import settings


def test_max_active_total_default():
    assert settings.max_active_total == 4


def test_max_queue_total_default():
    assert settings.max_queue_total == 20


def test_max_active_per_ip_default():
    assert settings.max_active_per_ip == 3


def test_scan_retention_days_default():
    assert settings.scan_retention_days == 7


def test_cleanup_interval_seconds_default():
    assert settings.cleanup_interval_seconds == 3600
