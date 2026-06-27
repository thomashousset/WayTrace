# backend/tests/test_internal_ips_extract.py
"""Tests for internal IP address extraction."""
from __future__ import annotations

import pytest

from services.extractor.internal_ips_extract import extract_internal_ips


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------

def test_class_a_ip_extracted():
    html = "<p>Server at 10.0.1.50 is down.</p>"
    results = extract_internal_ips(html)
    assert len(results) == 1
    assert results[0]["ip"] == "10.0.1.50"


def test_class_c_ip_extracted():
    html = "Connected to 192.168.1.100 successfully."
    results = extract_internal_ips(html)
    assert len(results) == 1
    assert results[0]["ip"] == "192.168.1.100"


def test_class_b_ip_extracted():
    html = "Gateway: 172.16.0.1"
    results = extract_internal_ips(html)
    assert len(results) == 1
    assert results[0]["ip"] == "172.16.0.1"


def test_localhost_extracted():
    html = "Listening on 127.0.0.1:8080"
    results = extract_internal_ips(html)
    assert len(results) == 1
    assert results[0]["ip"] == "127.0.0.1"


def test_multiple_ips_in_page():
    html = "Primary: 10.0.0.1, secondary: 192.168.0.254, loopback: 127.0.0.1"
    results = extract_internal_ips(html)
    ips = {r["ip"] for r in results}
    assert ips == {"10.0.0.1", "192.168.0.254", "127.0.0.1"}


def test_context_string_captured():
    html = "BEFORE_CONTEXT 10.20.30.40 AFTER_CONTEXT"
    results = extract_internal_ips(html)
    assert len(results) == 1
    assert "BEFORE_CONTEXT" in results[0]["context"]
    assert "AFTER_CONTEXT" in results[0]["context"]
    assert "10.20.30.40" in results[0]["context"]


def test_dedup_same_ip_twice():
    html = "host1=10.0.0.5 and also host2=10.0.0.5"
    results = extract_internal_ips(html)
    assert len(results) == 1
    assert results[0]["ip"] == "10.0.0.5"


# ---------------------------------------------------------------------------
# False-positive tests
# ---------------------------------------------------------------------------

def test_skip_rgba_css():
    html = "color: rgba(192, 168, 1, 0.5);"
    results = extract_internal_ips(html)
    assert results == []


def test_skip_rgb_css():
    html = "background: rgb(10, 20, 30);"
    results = extract_internal_ips(html)
    assert results == []


def test_skip_public_ip():
    html = "DNS server: 8.8.8.8"
    results = extract_internal_ips(html)
    assert results == []


def test_skip_172_15_below_rfc1918():
    html = "address 172.15.0.1 is not private"
    results = extract_internal_ips(html)
    assert results == []


def test_skip_172_32_above_rfc1918():
    html = "address 172.32.0.1 is not private"
    results = extract_internal_ips(html)
    assert results == []


def test_skip_invalid_octet():
    html = "bad IP: 10.0.0.999"
    results = extract_internal_ips(html)
    assert results == []


# ---------------------------------------------------------------------------
# Version strings in a private range must not read as internal IPs
# ---------------------------------------------------------------------------

def test_ignores_software_version_with_version_keyword():
    html = "<p>Requires version 10.0.0.1 or later to run.</p>"
    assert extract_internal_ips(html) == []


def test_ignores_v_prefixed_version():
    html = "<span>Upgraded to v10.2.0.1 today.</span>"
    assert extract_internal_ips(html) == []


def test_still_extracts_ip_without_version_marker():
    html = "<p>Bind the service to 10.0.0.1 on the LAN.</p>"
    results = extract_internal_ips(html)
    assert any(r["ip"] == "10.0.0.1" for r in results)
