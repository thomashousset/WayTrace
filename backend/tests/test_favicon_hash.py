"""favicon_hash: URL building + breaker gating (no network)."""
from __future__ import annotations

import asyncio

from services import archive_health
from services.favicon_hash import _abs_favicon, _wayback_raw_url, hash_favicons


def test_abs_favicon_absolute():
    assert _abs_favicon("https://s/favicon.ico", "s") == "https://s/favicon.ico"


def test_abs_favicon_relative():
    assert _abs_favicon("/favicon.ico", "site.com") == "https://site.com/favicon.ico"


def test_abs_favicon_protocol_relative():
    assert _abs_favicon("//cdn/x.png", "site.com") == "https://cdn/x.png"


def test_abs_favicon_unresolvable():
    assert _abs_favicon("favicon.ico", "") is None


def test_wayback_url_from_source_url():
    item = {"url": "https://s/favicon.ico",
            "source_url": "https://web.archive.org/web/20240101120000/https://s/"}
    assert _wayback_raw_url(item, "s") == "https://web.archive.org/web/20240101120000im_/https://s/favicon.ico"


def test_wayback_url_from_first_seen_month():
    item = {"url": "/favicon.ico", "first_seen": "2024-03"}
    url = _wayback_raw_url(item, "site.com")
    assert url == "https://web.archive.org/web/20240301000000im_/https://site.com/favicon.ico"


def test_wayback_url_none_without_timestamp():
    assert _wayback_raw_url({"url": "/favicon.ico"}, "site.com") is None


def test_hash_favicons_skips_when_breaker_open(monkeypatch):
    monkeypatch.setattr(archive_health, "is_open", lambda: True)
    favs = [{"url": "https://s/favicon.ico", "first_seen": "2024-01"}]
    n = asyncio.run(hash_favicons(favs, "s"))
    assert n == 0
    assert "md5" not in favs[0]


def test_hash_favicons_empty_list():
    assert asyncio.run(hash_favicons([], "s")) == 0


# --- Shodan favicon hash (mmh3 of base64 bytes) ---

from services.favicon_hash import _mmh3_x86_32, shodan_favicon_hash


def test_mmh3_known_vectors():
    # Canonical MurmurHash3 x86_32 seed=0 signed values (match the mmh3 lib).
    assert _mmh3_x86_32(b"") == 0
    assert _mmh3_x86_32(b"foo") == -156908512
    assert _mmh3_x86_32(b"hello") == 613153351


def test_shodan_hash_is_mmh3_of_base64():
    import base64
    data = b"\x89PNG\r\n\x1a\n" + b"WAYTRACE" * 40
    assert shodan_favicon_hash(data) == _mmh3_x86_32(base64.encodebytes(data))
    # Stable, deterministic int usable as http.favicon.hash:<value>
    assert isinstance(shodan_favicon_hash(data), int)
