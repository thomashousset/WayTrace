"""Tests for the get_client_ip helper."""
from types import SimpleNamespace

from services.ip_utils import get_client_ip


def _req(headers=None, client_host="1.2.3.4"):
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=client_host) if client_host else None,
    )


def test_prefers_cf_connecting_ip():
    r = _req({"CF-Connecting-IP": "10.0.0.1", "X-Real-IP": "20.0.0.1"})
    assert get_client_ip(r) == "10.0.0.1"


def test_falls_back_to_x_real_ip():
    r = _req({"X-Real-IP": "20.0.0.1"})
    assert get_client_ip(r) == "20.0.0.1"


def test_falls_back_to_x_forwarded_for_first_value():
    r = _req({"X-Forwarded-For": "30.0.0.1, 40.0.0.1, 50.0.0.1"})
    assert get_client_ip(r) == "30.0.0.1"


def test_strips_whitespace_in_forwarded_for():
    r = _req({"X-Forwarded-For": "  30.0.0.1  "})
    assert get_client_ip(r) == "30.0.0.1"


def test_falls_back_to_client_host():
    r = _req()
    assert get_client_ip(r) == "1.2.3.4"


def test_returns_zero_when_no_info():
    r = SimpleNamespace(headers={}, client=None)
    assert get_client_ip(r) == "0.0.0.0"


def test_handles_empty_header_values():
    r = _req({"CF-Connecting-IP": "", "X-Real-IP": "20.0.0.1"})
    assert get_client_ip(r) == "20.0.0.1"
