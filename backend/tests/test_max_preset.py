"""`depth='max'`. completeness-first preset. No dedup, keep everything
up to the 10000 upper bound."""
from __future__ import annotations

import pytest

from services.filters import filter_snapshots
from models import ScanConfig


def _make(ts, url, digest=None):
    return {"timestamp": ts, "url": url, "status": "200",
            "mimetype": "text/html", "digest": digest}


def test_max_preset_keeps_identical_digests():
    """Same (path, digest) on two snapshots usually collapses to 1 under
    standard dedup. under max it must keep both for temporal signal."""
    snaps = [
        _make("20200101120000", "https://x.com/", digest="AAA"),
        _make("20220601120000", "https://x.com/", digest="AAA"),
    ]
    std = filter_snapshots(snaps, ScanConfig(depth="standard"))
    mx = filter_snapshots(snaps, ScanConfig(depth="max"))
    assert std["snapshots_selected"] == 1  # dedup kept 1
    assert mx["snapshots_selected"] == 2   # max kept both
    assert mx["pages_deduped"] == 0


def test_max_preset_keeps_all_under_cap():
    """200 paths * 5 snapshots = 1000 snaps, well below the 10k cap → all kept."""
    snaps = []
    for i in range(200):
        for yr in (2019, 2020, 2021, 2022, 2023):
            snaps.append(_make(f"{yr}0601120000", f"https://x.com/p{i}",
                               digest=f"D{i}-{yr}"))
    result = filter_snapshots(snaps, ScanConfig(depth="max"))
    assert result["snapshots_selected"] == 1000


def test_max_preset_caps_at_upper_bound():
    """>10000 snapshots still cap via _allocate_budget_by_score."""
    snaps = []
    for i in range(1500):
        for yr in range(2010, 2025):
            snaps.append(_make(f"{yr}0601120000", f"https://x.com/p{i}",
                               digest=f"D{i}-{yr}"))
    result = filter_snapshots(snaps, ScanConfig(depth="max"))
    assert result["snapshots_selected"] == 10000


def test_max_preset_is_opt_in():
    """By default (standard), dedup + caps still apply as before."""
    snaps = [_make(f"20200601{h:02d}0000", "https://x.com/", digest="AAA")
             for h in range(1, 11)]
    result = filter_snapshots(snaps, ScanConfig())
    # 10 identical digests collapse to 1 under smart_dedup
    assert result["snapshots_selected"] == 1
    assert result["pages_deduped"] == 9


def test_max_preset_rejects_via_extra_forbid():
    """Typos on the depth literal still get rejected by Pydantic."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ScanConfig(depth="maximal")
