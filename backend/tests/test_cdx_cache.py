"""CDX on-disk cache: TTL freshness."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import cdx


def test_cache_hit_within_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    cdx._save_cache_sync("ex.com", {"snapshots": [1, 2], "total_found": 2})
    out = cdx._load_cache_sync("ex.com", ttl=3600)
    assert out is not None
    assert out["total_found"] == 2


def test_cache_miss_when_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    cdx._save_cache_sync("ex.com", {"snapshots": [1], "total_found": 1})
    # Backdate the stored timestamp beyond the TTL.
    import gzip, json
    p = cdx._cache_path("ex.com")
    with gzip.open(p, "rt", encoding="utf-8") as f:
        data = json.load(f)
    data["cached_at"] = time.time() - 10_000
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    assert cdx._load_cache_sync("ex.com", ttl=3600) is None


def test_cache_no_ttl_always_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    cdx._save_cache_sync("ex.com", {"snapshots": [], "total_found": 0})
    assert cdx._load_cache_sync("ex.com", ttl=None) is not None


def test_saved_result_is_not_mutated(tmp_path, monkeypatch):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    result = {"snapshots": [], "total_found": 0}
    cdx._save_cache_sync("ex.com", result)
    assert "cached_at" not in result   # only the file carries the timestamp
