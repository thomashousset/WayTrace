"""Real internal addresses the SVG geometry rule dropped, and must keep.

The geometry attribute check also matched JavaScript variables named like
SVG attributes (var d = "192.168.10.5", var points = "10.20.0.14"), the
glued path-letter check dropped an address written after a lone capital in
prose, and "v." as a version marker also ate "NAS v. 192.168.0.20". All of
these ranked LEAK before the rule and vanished after it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor.internal_ips_extract import extract_internal_ips


def _ips(html):
    return [r["ip"] for r in extract_internal_ips(html)]


@pytest.mark.parametrize("html, ip", [
    ('<script>var d = "192.168.10.5"; var srv=d+":8080";</script>', "192.168.10.5"),
    ('<script>function ping(){ var points = "10.20.0.14"; }</script>', "10.20.0.14"),
    ('<script>cfg.transform="172.16.3.3"</script>', "172.16.3.3"),
    ("<p>Serveur NAS v. 192.168.0.20 (ancienne adresse)</p>", "192.168.0.20"),
    ("<p>VPN via L2TP: L10.2.3.4</p>", "10.2.3.4"),
    ("<p>Tickets: Q192.168.7.7 and more</p>", "192.168.7.7"),
])
def test_addresses_outside_geometry_come_out(html, ip):
    assert _ips(html) == [ip]


@pytest.mark.parametrize("html", [
    '<svg><path class="i" d="M10.0.0.5 L192.168.1.1"/></svg>',
    '<path\nd="M12 10.5.5 9 172.16.4.5 3z"/>',
    'var icon = "M10.0.0.5 L192.168.1.1";',
    'var icon = "M2 4 L192.168.1.1";',
    "Firmware v. 10.0.0.1",
])
def test_geometry_written_outside_a_tag_and_versions_stay_out(html):
    assert _ips(html) == []
