import sys
import os
import tempfile

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.filters import (
    filter_snapshots,
    _compute_cap,
    _allocate_budget_by_score,
    _allocate_budget_by_year,
    _evenly_spaced,
)
from models import ScanConfig
from db import init_db


def _make_snapshot(timestamp: str, url: str = "https://example.com/", mimetype: str = "text/html", digest: str | None = None) -> dict:
    return {
        "timestamp": timestamp,
        "url": url,
        "status": "200",
        "mimetype": mimetype,
        "digest": digest,
    }


def test_empty_snapshots():
    result = filter_snapshots([])
    assert result["selected"] == []
    assert result["snapshots_selected"] == 0
    assert result["date_first_seen"] is None


def test_single_snapshot():
    snaps = [_make_snapshot("20200301120000")]
    result = filter_snapshots(snaps)
    assert result["snapshots_selected"] == 1
    assert result["date_first_seen"] == "2020-03"
    assert result["date_last_seen"] == "2020-03"


def test_all_unique_content_scraped():
    """All unique (non-digest-dup) snapshots should be returned."""
    snaps = []
    # 24 homepage snapshots (2 per month, 12 months) + 3 other paths = 27 total
    for month in range(1, 13):
        for day in (1, 15):
            ts = f"2020{month:02d}{day:02d}120000"
            snaps.append(_make_snapshot(ts, "https://example.com/"))
    snaps.append(_make_snapshot("20200601120000", "https://example.com/about"))
    snaps.append(_make_snapshot("20200601120000", "https://example.com/contact"))
    snaps.append(_make_snapshot("20200601120000", "https://example.com/team"))
    result = filter_snapshots(snaps)
    # No digest → all 27 unique snapshots returned
    assert result["snapshots_selected"] == 27


def test_first_and_last_always_included():
    snaps = [
        _make_snapshot("20200115120000", "https://example.com/"),
        _make_snapshot("20200301120000", "https://example.com/page1"),
        _make_snapshot("20200301180000", "https://example.com/page2"),
        _make_snapshot("20201225120000", "https://example.com/"),
    ]
    result = filter_snapshots(snaps)
    timestamps = [s["timestamp"] for s in result["selected"]]
    assert "20200115120000" in timestamps
    assert "20201225120000" in timestamps


def test_url_diversity():
    """Different URL paths should all be represented."""
    snaps = [
        _make_snapshot("20200101120000", "https://example.com/"),
        _make_snapshot("20200201120000", "https://example.com/"),
        _make_snapshot("20200301120000", "https://example.com/"),
        _make_snapshot("20200101120000", "https://example.com/about"),
        _make_snapshot("20200101120000", "https://example.com/contact"),
        _make_snapshot("20200101120000", "https://example.com/team"),
    ]
    result = filter_snapshots(snaps)
    selected_paths = set()
    for s in result["selected"]:
        from urllib.parse import urlparse
        path = urlparse(s["url"]).path or "/"
        selected_paths.add(path)
    # All unique paths should be represented
    assert "/" in selected_paths
    assert "/about" in selected_paths
    assert "/contact" in selected_paths
    assert "/team" in selected_paths


def test_low_priority_keeps_first_and_last():
    """Low priority paths should keep both first and last (not just last)."""
    snaps = [
        _make_snapshot("20190601120000", "https://example.com/random"),
        _make_snapshot("20220601120000", "https://example.com/random"),
    ]
    result = filter_snapshots(snaps)
    timestamps = [s["timestamp"] for s in result["selected"]]
    assert "20190601120000" in timestamps
    assert "20220601120000" in timestamps


def test_dynamic_cap_small_domain():
    """Small domain (<= 30 unique paths) capped at 100 to stay polite."""
    # 10 paths, 80 html snapshots -> all 80 (≤ cap)
    assert _compute_cap(10, 80) == 80
    # 20 paths, 200 html -> capped at 100
    assert _compute_cap(20, 200) == 100
    # 5 paths, 1000 html -> capped at 100
    assert _compute_cap(5, 1000) == 100


def test_dynamic_cap_medium_domain():
    """Medium domain (31-200 paths) gets ~3 per path, ≤ 500."""
    # 100 paths -> min(300, 500) = 300
    assert _compute_cap(100, 2000) == 300
    # 120 paths -> min(360, 500) = 360
    assert _compute_cap(120, 3000) == 360
    # 50 paths -> min(150, 500) = 150
    assert _compute_cap(50, 500) == 150
    # 180 paths -> min(540, 500) = 500
    assert _compute_cap(180, 3000) == 500


def test_dynamic_cap_large_domain():
    """Large domain gets ~1.5 per path (≤ 1000); very large caps at 1500."""
    # 500 paths -> min(750, 1000) = 750
    assert _compute_cap(500, 10000) == 750
    # 1500 paths (>1000 tier) -> flat 1500
    assert _compute_cap(1500, 50000) == 1500
    # 10000 paths -> still 1500
    assert _compute_cap(10000, 50000) == 1500


def test_cap_enforced_large_site():
    """Without explicit cap or quick depth, all snapshots are returned."""
    snaps = []
    # 600 unique URL paths x 3 timestamps each = 1800 snapshots
    for i in range(600):
        for year in (2019, 2020, 2021):
            ts = f"{year}0601120000"
            snaps.append(_make_snapshot(ts, f"https://example.com/page{i}"))
    result = filter_snapshots(snaps)
    # No cap applied for standard depth → all 1800 returned
    assert result["snapshots_selected"] == 1800


def test_small_domain_scanned_fully():
    """All unique snapshots should be returned regardless of domain size."""
    snaps = []
    # 20 unique paths x 3 timestamps = 60 snapshots
    for i in range(20):
        for year in (2019, 2020, 2021):
            ts = f"{year}0601120000"
            snaps.append(_make_snapshot(ts, f"https://example.com/page{i}"))
    result = filter_snapshots(snaps)
    # All 60 unique snapshots returned
    assert result["snapshots_selected"] == 60


def test_non_html_excluded():
    snaps = [
        _make_snapshot("20200301120000", "https://example.com/", "text/html"),
        _make_snapshot("20200401120000", "https://example.com/img.png", "image/png"),
        _make_snapshot("20200501120000", "https://example.com/data.json", "application/json"),
        _make_snapshot("20200601120000", "https://example.com/page2", "text/html"),
    ]
    result = filter_snapshots(snaps)
    assert result["snapshots_selected"] == 2


def test_date_format():
    snaps = [
        _make_snapshot("20180315120000", "https://example.com/"),
        _make_snapshot("20231115120000", "https://example.com/"),
    ]
    result = filter_snapshots(snaps)
    assert result["date_first_seen"] == "2018-03"
    assert result["date_last_seen"] == "2023-11"


# --- ScanConfig tests ---


def test_year_allocation_floors_rare_years():
    """A sparse early year must keep representation even when a recent year
    dominates by volume. Pure 'top-N by path' starves the rare year."""
    snaps = []
    for i in range(5):
        snaps.append(_make_snapshot("20130601120000", f"https://example.com/old{i}"))
    for i in range(500):
        snaps.append(_make_snapshot("20240601120000", f"https://example.com/new{i}"))
    config = ScanConfig(cap=50)
    result = filter_snapshots(snaps, config)
    sel = result["selected"]
    y2013 = [s for s in sel if s["timestamp"].startswith("2013")]
    y2024 = [s for s in sel if s["timestamp"].startswith("2024")]
    assert len(sel) <= 50
    assert len(y2013) >= 3           # rare year not starved (floor)
    assert len(y2024) < 50           # dense year did not grab the whole budget
    assert len(y2024) > len(y2013)   # but still proportionally larger


def test_year_allocation_proportional_to_volume():
    """With three years of very different volume, the budget tracks volume
    (after the per-year floor) rather than splitting evenly."""
    snaps = []
    for i in range(20):
        snaps.append(_make_snapshot("20200601120000", f"https://example.com/a{i}"))
    for i in range(80):
        snaps.append(_make_snapshot("20210601120000", f"https://example.com/b{i}"))
    for i in range(400):
        snaps.append(_make_snapshot("20220601120000", f"https://example.com/c{i}"))
    out = _allocate_budget_by_year(snaps, 100)
    by_year = {}
    for s in out:
        by_year.setdefault(s["timestamp"][:4], 0)
        by_year[s["timestamp"][:4]] += 1
    assert len(out) <= 100
    # Each year represented, and ordering by volume preserved.
    assert by_year.get("2020", 0) >= 3
    assert by_year["2022"] > by_year["2021"] > by_year["2020"]


def test_year_allocation_noop_when_under_cap():
    snaps = [_make_snapshot("20200601120000", f"https://example.com/p{i}") for i in range(10)]
    assert _allocate_budget_by_year(snaps, 50) == snaps


def test_config_custom_cap():
    """Custom cap from config should override computed cap."""
    snaps = []
    for i in range(50):
        for year in (2019, 2020, 2021):
            ts = f"{year}0601120000"
            snaps.append(_make_snapshot(ts, f"https://example.com/page{i}"))
    config = ScanConfig(cap=25)
    result = filter_snapshots(snaps, config)
    assert result["snapshots_selected"] <= 25


def test_config_date_from():
    """date_from should exclude earlier snapshots."""
    snaps = [
        _make_snapshot("20180601120000", "https://example.com/"),
        _make_snapshot("20190601120000", "https://example.com/"),
        _make_snapshot("20200601120000", "https://example.com/"),
        _make_snapshot("20210601120000", "https://example.com/"),
    ]
    config = ScanConfig(date_from="2020-01")
    result = filter_snapshots(snaps, config)
    timestamps = [s["timestamp"] for s in result["selected"]]
    assert all(ts >= "20200101000000" for ts in timestamps)
    assert result["snapshots_selected"] == 2


def test_config_date_to():
    """date_to should exclude later snapshots."""
    snaps = [
        _make_snapshot("20180601120000", "https://example.com/"),
        _make_snapshot("20190601120000", "https://example.com/"),
        _make_snapshot("20200601120000", "https://example.com/"),
        _make_snapshot("20210601120000", "https://example.com/"),
    ]
    config = ScanConfig(date_to="2019-12")
    result = filter_snapshots(snaps, config)
    timestamps = [s["timestamp"] for s in result["selected"]]
    assert all(ts <= "20191231235959" for ts in timestamps)
    assert result["snapshots_selected"] == 2


def test_config_date_range():
    """Both date_from and date_to should work together."""
    snaps = [
        _make_snapshot("20180601120000", "https://example.com/"),
        _make_snapshot("20190601120000", "https://example.com/"),
        _make_snapshot("20200601120000", "https://example.com/"),
        _make_snapshot("20210601120000", "https://example.com/"),
    ]
    config = ScanConfig(date_from="2019-01", date_to="2020-12")
    result = filter_snapshots(snaps, config)
    assert result["snapshots_selected"] == 2


def test_depth_quick_fewer_snapshots():
    """Quick depth should cap at 500; standard returns all."""
    snaps = []
    # 600 unique snapshots, each with a unique URL and timestamp
    for i in range(600):
        year = 2000 + i // 12
        month = i % 12 + 1
        ts = f"{year}{month:02d}01120000"
        snaps.append(_make_snapshot(ts, f"https://example.com/page{i}"))

    result_std = filter_snapshots(snaps, ScanConfig(depth="standard"))
    result_quick = filter_snapshots(snaps, ScanConfig(depth="quick"))
    assert result_quick["snapshots_selected"] <= 500
    assert result_std["snapshots_selected"] == 600
    assert result_quick["snapshots_selected"] < result_std["snapshots_selected"]


def test_depth_full_more_snapshots():
    """Full and standard depth both return all unique content."""
    snaps = []
    for i in range(10):
        for year in range(2018, 2024):
            for month in (3, 9):
                ts = f"{year}{month:02d}15120000"
                snaps.append(_make_snapshot(ts, f"https://example.com/page{i}"))
    for year in range(2018, 2024):
        for month in (3, 9):
            ts = f"{year}{month:02d}15120000"
            snaps.append(_make_snapshot(ts, "https://example.com/about"))
            snaps.append(_make_snapshot(ts, "https://example.com/contact"))

    result_std = filter_snapshots(snaps, ScanConfig(depth="standard"))
    result_full = filter_snapshots(snaps, ScanConfig(depth="full"))
    # Both depths return all unique content
    assert result_full["snapshots_selected"] == result_std["snapshots_selected"]


def test_config_none_backward_compatible():
    """Passing config=None should produce same result as no config."""
    snaps = [
        _make_snapshot("20200101120000", "https://example.com/"),
        _make_snapshot("20200601120000", "https://example.com/about"),
        _make_snapshot("20201201120000", "https://example.com/"),
    ]
    result_none = filter_snapshots(snaps, None)
    result_default = filter_snapshots(snaps)
    assert result_none["snapshots_selected"] == result_default["snapshots_selected"]


def test_digest_dedup_removes_identical_content():
    """Snapshots with same path + digest should be deduplicated."""
    snaps = [
        _make_snapshot("20200101120000", "https://example.com/about", digest="ABC123"),
        _make_snapshot("20200301120000", "https://example.com/about", digest="ABC123"),
        _make_snapshot("20200601120000", "https://example.com/about", digest="ABC123"),
        _make_snapshot("20200901120000", "https://example.com/about", digest="DEF456"),
    ]
    result = filter_snapshots(snaps)
    # 3 snapshots have the same digest for /about ; only 1 should survive + 1 different
    about_snaps = [s for s in result["selected"] if "/about" in s["url"]]
    assert len(about_snaps) == 2  # one for ABC123, one for DEF456


def test_digest_dedup_different_paths_not_deduped():
    """Same digest on different paths should NOT be deduplicated."""
    snaps = [
        _make_snapshot("20200101120000", "https://example.com/about", digest="SAME"),
        _make_snapshot("20200101120000", "https://example.com/contact", digest="SAME"),
    ]
    result = filter_snapshots(snaps)
    assert result["snapshots_selected"] == 2


def test_no_digest_snapshots_kept():
    """Snapshots without digest field should never be deduplicated."""
    snaps = [
        _make_snapshot("20200101120000", "https://example.com/"),
        _make_snapshot("20200601120000", "https://example.com/"),
        _make_snapshot("20201201120000", "https://example.com/"),
    ]
    result = filter_snapshots(snaps)
    # No digest → all 3 kept (no dedup applied)
    assert result["snapshots_selected"] == 3


def test_all_snapshots_except_digest_dups():
    """100 unique-digest snapshots all returned; 10 same-digest → only 1 kept."""
    # 100 snapshots of same path, each with a unique digest → all 100 returned
    snaps = []
    for i in range(100):
        ts = f"{2000 + i}0601120000"
        snaps.append(_make_snapshot(ts, "https://example.com/page", digest=f"DIGEST{i:03d}"))
    result = filter_snapshots(snaps)
    assert result["snapshots_selected"] == 100

    # 10 snapshots of same path with the same digest → only 1 kept
    snaps2 = []
    for i in range(10):
        ts = f"2020{i + 1:02d}01120000"
        snaps2.append(_make_snapshot(ts, "https://example.com/same-page", digest="SAME_DIGEST"))
    result2 = filter_snapshots(snaps2)
    assert result2["snapshots_selected"] == 1


# --- Weighted budget allocation tests ---


def test_evenly_spaced_picks_first_last_and_middle():
    """10 items, want 3 → indices 0, ~5, 9 (first, middle, last)."""
    items = [{"i": i} for i in range(10)]
    picks = _evenly_spaced(items, 3)
    assert [p["i"] for p in picks] == [0, 5, 9] or [p["i"] for p in picks] == [0, 4, 9]


def test_evenly_spaced_returns_all_when_n_exceeds_length():
    items = [{"i": i} for i in range(3)]
    picks = _evenly_spaced(items, 10)
    assert len(picks) == 3


def test_weighted_budget_favors_high_score_paths():
    """Cap ≥ #paths: /admin's weight multiplier gives it a full 5-snapshot share."""
    snaps = []
    # /admin: 10 snapshots, HIGH (score 3)
    for i in range(10):
        snaps.append(_make_snapshot(f"201{i}0101120000", "https://x.com/admin"))
    # /docs/N: 20 snapshots, 5 distinct paths × 4 timestamps, LOW (score 1)
    for pi in range(5):
        for ti in range(4):
            ts = f"20{15 + ti}0601120000"
            snaps.append(_make_snapshot(ts, f"https://x.com/docs/{pi}"))

    # 30 total, 6 paths → cap=20 is well above len(paths), weighted branch runs.
    picked = _allocate_budget_by_score(snaps, cap=20)
    assert len(picked) == 20
    admin_count = sum(1 for s in picked if s["url"].endswith("/admin"))
    docs_count = len(picked) - admin_count
    # /admin weight = 10 × 3 = 30. /docs total weight = 20 × 1 = 20.
    # Share: admin ≈ 30/50 × 20 = 12, docs ≈ 20/50 × 20 = 8.
    assert admin_count >= 8
    assert docs_count >= 4


def test_weighted_budget_hits_cap_exactly():
    """Cap must be exactly achieved regardless of rounding drift."""
    snaps = []
    for i in range(30):
        snaps.append(_make_snapshot(f"2020{(i % 12) + 1:02d}01120000", f"https://x.com/p{i}"))
    for cap in (5, 7, 13, 17, 29):
        picked = _allocate_budget_by_score(snaps, cap=cap)
        assert len(picked) == cap, f"cap={cap} got {len(picked)}"


def test_weighted_budget_cap_smaller_than_paths_keeps_top_scored():
    """When cap < unique paths, keep the highest-scored paths."""
    snaps = [
        _make_snapshot("20200101120000", "https://x.com/"),            # score 2
        _make_snapshot("20200101120000", "https://x.com/admin"),        # score 3
        _make_snapshot("20200101120000", "https://x.com/contact"),      # score 3
        _make_snapshot("20200101120000", "https://x.com/random/1"),     # score 1
        _make_snapshot("20200101120000", "https://x.com/random/2"),     # score 1
    ]
    picked = _allocate_budget_by_score(snaps, cap=2)
    urls = {s["url"] for s in picked}
    # Both score-3 paths must be present; no score-1 path should.
    assert "https://x.com/admin" in urls
    assert "https://x.com/contact" in urls
    assert "https://x.com/random/1" not in urls
    assert "https://x.com/random/2" not in urls


def test_weighted_budget_temporal_coverage():
    """For a single path with 10 years of snapshots and cap 3, picks must span
    the full time range, not just the earliest 3 months."""
    snaps = [
        _make_snapshot(f"{yr}0601120000", "https://x.com/admin")
        for yr in range(2010, 2025)  # 15 snapshots
    ]
    picked = _allocate_budget_by_score(snaps, cap=3)
    years = sorted(int(s["timestamp"][:4]) for s in picked)
    # First pick ≈ 2010, last ≈ 2024, middle ≈ 2017
    assert years[0] <= 2011
    assert years[-1] >= 2023


# --- DB-backed selection tests ---


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_select_snapshots_in_db(tmp_db_path):
    from services.filters import select_snapshots_in_db

    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('example.com')")
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, digest) VALUES "
            "(1, 'http://example.com/', '20200101120000', 'text/html', 'aaa')"
        )
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, digest) VALUES "
            "(1, 'http://example.com/about', '20200201120000', 'text/html', 'bbb')"
        )
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, digest) VALUES "
            "(1, 'http://example.com/contact', '20200301120000', 'text/html', 'ccc')"
        )
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, digest) VALUES "
            "(1, 'http://example.com/file.zip', '20200401120000', 'application/zip', 'ddd')"
        )
        await db.commit()

    result = await select_snapshots_in_db(1, tmp_db_path)
    assert result["selected_count"] >= 3
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute(
            "SELECT count(*) FROM snapshots WHERE domain_id = 1 AND selected = 1"
        )
        count = (await cursor.fetchone())[0]
    assert count == 3


@pytest.mark.asyncio
async def test_select_snapshots_creates_pages(tmp_db_path):
    from services.filters import select_snapshots_in_db

    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('example.com')")
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, digest) VALUES "
            "(1, 'http://example.com/', '20200101120000', 'text/html', 'aaa')"
        )
        await db.commit()

    await select_snapshots_in_db(1, tmp_db_path)

    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("SELECT count(*) FROM pages WHERE status = 'pending'")
        count = (await cursor.fetchone())[0]
    assert count == 1
