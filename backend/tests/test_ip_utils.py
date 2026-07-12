"""Tests for the spoof-resistant get_client_ip helper."""
from types import SimpleNamespace

import pytest

from config import settings
from services.ip_utils import get_client_ip


def _req(headers=None, client_host="1.2.3.4"):
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=client_host) if client_host else None,
    )


@pytest.fixture
def no_cloudflare(monkeypatch):
    monkeypatch.setattr(settings, "trust_cloudflare", False)


@pytest.fixture
def with_cloudflare(monkeypatch):
    monkeypatch.setattr(settings, "trust_cloudflare", True)


def test_uses_x_real_ip(no_cloudflare):
    r = _req({"X-Real-IP": "20.0.0.1"})
    assert get_client_ip(r) == "20.0.0.1"


def test_ignores_forgeable_cf_header_by_default(no_cloudflare):
    # A direct client can set CF-Connecting-IP; it must NOT override the
    # proxy-set X-Real-IP (spoofing the per-IP caps).
    r = _req({"CF-Connecting-IP": "10.0.0.1", "X-Real-IP": "20.0.0.1"})
    assert get_client_ip(r) == "20.0.0.1"


def test_ignores_forgeable_xff_by_default(no_cloudflare):
    # X-Forwarded-For is client-forgeable without a trusted proxy: ignored,
    # falls through to the real connection host.
    r = _req({"X-Forwarded-For": "30.0.0.1, 40.0.0.1"})
    assert get_client_ip(r) == "1.2.3.4"


def test_prefers_cf_when_cloudflare_trusted(with_cloudflare):
    r = _req({"CF-Connecting-IP": "10.0.0.1", "X-Real-IP": "20.0.0.1"})
    assert get_client_ip(r) == "10.0.0.1"


def test_falls_back_to_client_host(no_cloudflare):
    assert get_client_ip(_req()) == "1.2.3.4"


def test_returns_zero_when_no_info(no_cloudflare):
    r = SimpleNamespace(headers={}, client=None)
    assert get_client_ip(r) == "0.0.0.0"


def test_handles_empty_x_real_ip(no_cloudflare):
    r = _req({"X-Real-IP": ""})
    assert get_client_ip(r) == "1.2.3.4"
