"""Per-IP caps count an IPv6 client by its /64, not by its full address.

A provider hands each IPv6 subscriber a whole /64 and the client picks any
address inside it, so a cap keyed on the full address gave one person billions
of fresh buckets.
"""
import pytest

from config import settings
from services.ip_utils import rate_key
from store import JobStore, PerIpLimitError


def test_ipv6_collapses_to_its_64():
    a = rate_key("2001:db8:1:2:aaaa::1")
    b = rate_key("2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert a == b == "2001:db8:1:2::/64"


def test_neighbouring_64s_stay_apart():
    assert rate_key("2001:db8:1:2::1") != rate_key("2001:db8:1:3::1")


def test_ipv4_and_non_ip_keys_are_unchanged():
    assert rate_key("203.0.113.7") == "203.0.113.7"
    assert rate_key("someone@example.com") == "someone@example.com"
    assert rate_key("u42") == "u42"
    assert rate_key("12") == "12"
    assert rate_key("") == ""


def test_ipv4_mapped_ipv6_counts_as_the_ipv4():
    assert rate_key("::ffff:203.0.113.7") == "203.0.113.7"


@pytest.mark.asyncio
async def test_active_scan_cap_holds_across_one_64(monkeypatch):
    monkeypatch.setattr(settings, "max_active_total", 10)
    monkeypatch.setattr(settings, "max_active_per_ip", 2)
    monkeypatch.setattr(settings, "max_queue_total", 100)
    s = JobStore()
    await s.create_job("a.com", "2001:db8:1:2::1")
    await s.create_job("b.com", "2001:db8:1:2::2")
    with pytest.raises(PerIpLimitError):
        await s.create_job("c.com", "2001:db8:1:2::3")
    # Another subscriber is unaffected.
    await s.create_job("d.com", "2001:db8:9:9::1")


@pytest.mark.asyncio
async def test_finishing_a_scan_frees_the_64(monkeypatch):
    monkeypatch.setattr(settings, "max_active_total", 10)
    monkeypatch.setattr(settings, "max_active_per_ip", 1)
    monkeypatch.setattr(settings, "max_queue_total", 100)
    s = JobStore()
    first = await s.create_job("a.com", "2001:db8:1:2::1")
    await s.finish_job(first["job_id"])
    await s.create_job("b.com", "2001:db8:1:2::2")
