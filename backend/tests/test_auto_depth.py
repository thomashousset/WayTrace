"""Tests for the auto-depth helper and the cheap CDX size probe."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.filters import auto_depth


# ---------------------------------------------------------------------------
# auto_depth threshold table
# ---------------------------------------------------------------------------


def test_auto_depth_tiny_picks_max():
    """Tiny domains (< 5k raw records) get the completeness-first 'max'."""
    depth, cap = auto_depth(0)
    # 0 = probe failed -> safe default 'standard'.
    assert depth == "standard"
    depth, cap = auto_depth(100)
    assert depth == "max"


def test_auto_depth_small_picks_full():
    depth, cap = auto_depth(10_000)
    assert depth == "full"
    assert cap >= 500


def test_auto_depth_medium_picks_standard():
    depth, cap = auto_depth(50_000)
    assert depth == "standard"


def test_auto_depth_big_picks_quick():
    depth, cap = auto_depth(500_000)
    assert depth == "quick"


def test_auto_depth_huge_falls_back_to_quick():
    depth, cap = auto_depth(20_000_000)  # stripe.com territory
    assert depth == "quick"
    assert cap <= 300


def test_auto_depth_monotonic():
    """The picked cap should not increase as the estimate grows past the
    'medium' threshold. bigger domains get sampled harder, not softer."""
    _, cap_small = auto_depth(10_000)
    _, cap_med = auto_depth(50_000)
    _, cap_big = auto_depth(500_000)
    _, cap_huge = auto_depth(50_000_000)
    assert cap_med >= cap_big >= cap_huge
    # 'small' (full) is allowed to be bigger than the rest. that's the point.
    assert cap_small >= cap_big


def test_auto_depth_force_thorough_bumps_one_tier():
    """force_thorough=True lifts the depth preset one level so the
    'rescan thorough' banner CTA actually does more work."""
    base_depth, _ = auto_depth(500_000)        # 'quick'
    bumped_depth, _ = auto_depth(500_000, force_thorough=True)  # → 'standard'
    assert base_depth == "quick"
    assert bumped_depth == "standard"


def test_auto_depth_force_thorough_caps_at_max():
    base_depth, _ = auto_depth(100)            # 'max' already
    bumped_depth, _ = auto_depth(100, force_thorough=True)
    assert bumped_depth == "max"


# ---------------------------------------------------------------------------
# cdx_size_probe. async, mocks archive.org over aiohttp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cdx_size_probe_parses_integer_response(monkeypatch):
    """Probe should parse the bare integer body returned by archive.org."""
    from services import cdx as cdx_mod

    class _FakeResp:
        status = 200
        async def text(self):
            return "5738\n"
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    class _FakeSession:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, url):
            return _FakeResp()

    monkeypatch.setattr(cdx_mod.aiohttp, "ClientSession", _FakeSession)

    out = await cdx_mod.cdx_size_probe("stripe.com")
    assert out["ok"] is True
    assert out["page_count"] == 5738
    assert out["estimated_records"] == 5738 * 3000


@pytest.mark.asyncio
async def test_cdx_size_probe_returns_error_on_unexpected_payload(monkeypatch):
    from services import cdx as cdx_mod

    class _FakeResp:
        status = 200
        async def text(self):
            return "<html>unavailable</html>"
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    class _FakeSession:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def get(self, url): return _FakeResp()

    monkeypatch.setattr(cdx_mod.aiohttp, "ClientSession", _FakeSession)

    out = await cdx_mod.cdx_size_probe("example.com")
    assert out["ok"] is False
    assert "unexpected probe payload" in out["error"]


@pytest.mark.asyncio
async def test_cdx_size_probe_returns_error_on_http_5xx(monkeypatch):
    from services import cdx as cdx_mod

    class _FakeResp:
        status = 502
        async def text(self):
            return ""
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    class _FakeSession:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def get(self, url): return _FakeResp()

    monkeypatch.setattr(cdx_mod.aiohttp, "ClientSession", _FakeSession)

    out = await cdx_mod.cdx_size_probe("example.com")
    assert out["ok"] is False
    assert "HTTP 502" in out["error"]


# ---------------------------------------------------------------------------
# Pass F: budget-driven truncation in crawl_cdx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_cdx_stops_on_snapshot_cap():
    """Synthetic CDX returns 50 rows per page with an infinite resume chain.
    With max_snapshots=20 the crawl must stop after the first page and
    surface stop_reason='snapshot_cap_*'."""
    import json as _json
    import os
    import tempfile
    from unittest.mock import AsyncMock, MagicMock

    from db import init_db
    from services.collector import crawl_cdx

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db_path)
    try:
        # 50 rows per page + always a resumeKey -> infinite supply.
        rows = [["timestamp", "original", "statuscode", "mimetype", "digest"]]
        rows.extend([
            [f"2020{(i%12)+1:02d}{(i%28)+1:02d}120000",
             f"http://example.com/p{i}",
             "200", "text/html", f"DIGEST{i}"]
            for i in range(50)
        ])
        rows.append(["next-resume-key-blob-larger-than-twenty-chars"])
        payload = _json.dumps(rows).encode()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=payload)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        result = await crawl_cdx(
            mock_session, "example.com", db_path,
            max_snapshots=20,
            deadline_seconds=30,
        )
    finally:
        os.unlink(db_path)

    # First page already has 50 rows so we hit the cap immediately after
    # the first batch. the loop bails before the second request.
    assert result["snapshots_indexed"] == 50  # one full page got committed
    assert result["stop_reason"].startswith("snapshot_cap_")
    assert result["depth_budget_max_snapshots"] == 20


# ---------------------------------------------------------------------------
# CDX urlkey-bias fix: collapse=timestamp:6 by default
# ---------------------------------------------------------------------------


def test_build_cdx_params_includes_collapse_by_default():
    """Without collapse, archive.org sorts by urlkey alphabetically and a
    20k snapshot budget on stripe.com gets eaten by ~19 800 homepage
    captures. every other path is invisible. Default to monthly
    granularity per URL so the path-scoring filter sees real diversity."""
    from services.cdx import build_cdx_params
    params = build_cdx_params("example.com")
    assert params["collapse"] == "timestamp:6"


def test_build_cdx_params_collapse_overridable():
    from services.cdx import build_cdx_params
    # Caller can opt out with collapse=None (e.g. when explicit per-URL
    # temporal density is wanted).
    params = build_cdx_params("example.com", collapse=None)
    assert "collapse" not in params
    # Or change granularity (monthly -> yearly).
    params = build_cdx_params("example.com", collapse="timestamp:4")
    assert params["collapse"] == "timestamp:4"


@pytest.mark.asyncio
async def test_crawl_cdx_stops_on_deadline():
    """deadline_seconds=0 means we stop before issuing any new request."""
    import os
    import tempfile
    from unittest.mock import AsyncMock, MagicMock

    from db import init_db
    from services.collector import crawl_cdx

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db_path)
    try:
        mock_session = AsyncMock()
        mock_session.get = MagicMock()  # never called
        result = await crawl_cdx(
            mock_session, "example.com", db_path,
            max_snapshots=1_000_000,
            deadline_seconds=0,
        )
    finally:
        os.unlink(db_path)

    assert result["snapshots_indexed"] == 0
    assert result["stop_reason"].startswith("deadline_")
    # No HTTP request was issued because the deadline pre-empted the loop.
    mock_session.get.assert_not_called()
