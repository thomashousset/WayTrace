"""SVG geometry no longer yields internal IPs (they ranked LEAK).

Path data is a run of decimals, and two glued together read as an address:
d="M10.0.0.5 L192.168.1.1" gave 10.0.0.5 and 192.168.1.1. Real addresses
next to ordinary words, in URLs, in attributes other than geometry, stay.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor.internal_ips_extract import extract_internal_ips


def _ips(html):
    return [r["ip"] for r in extract_internal_ips(html)]


@pytest.mark.parametrize("html", [
    '<path d="M10.0.0.5 L192.168.1.1"/>',
    '<path d="M12 10.5.5 9 172.16.4.5 3z"/>',
    "<polygon points='1 2 192.168.0.1 4'/>",
    '<svg viewBox="0 0 10.0.0.1"></svg>',
    '<g transform="translate(10.0.0.3)"></g>',
    'background:url("data:image/svg+xml,%3Cpath d=%22M10.0.0.5%22")',
    'icon:"M10.0.0.5L12 4"',
    "v. 10.0.0.1",
])
def test_geometry_and_versions_are_not_addresses(html):
    assert _ips(html) == []


@pytest.mark.parametrize("html, ip", [
    ("Gateway 192.168.1.1", "192.168.1.1"),
    ("host=10.0.0.12 port 22", "10.0.0.12"),
    ('<a href="http://192.168.1.10/admin">', "192.168.1.10"),
    ("ssh admin@10.1.2.3", "10.1.2.3"),
    ('server: "172.16.0.4"', "172.16.0.4"),
    ('<div data-d="x" title="db at 10.0.0.7">', "10.0.0.7"),
    ('<path d="M1 1"/><p>NAS on 192.168.0.20</p>', "192.168.0.20"),
])
def test_real_addresses_still_come_out(html, ip):
    assert _ips(html) == [ip]
