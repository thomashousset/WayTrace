"""merge_analytics_ids folds analytics_ids into analytics_trackers, deduping."""
from __future__ import annotations

from services.extractor.finalize import merge_analytics_ids


def test_duplicate_id_is_deduped():
    res = {
        "analytics_trackers": [{"type": "GTM", "id": "GTM-MNGXPB5R", "occurrences": 14,
                                 "first_seen": "2025-08", "last_seen": "2026-04"}],
        "analytics_ids": [{"platform": "gtm", "id_value": "GTM-MNGXPB5R", "occurrences": 14,
                            "pivot_url": "https://x", "first_seen": "2025-07", "last_seen": "2026-04"}],
    }
    merge_analytics_ids(res)
    assert res["analytics_ids"] == []
    trk = res["analytics_trackers"]
    assert len(trk) == 1
    assert trk[0]["id"] == "GTM-MNGXPB5R"
    assert trk[0]["pivot_url"] == "https://x"      # enriched from analytics_ids
    assert trk[0]["first_seen"] == "2025-07"        # widened earlier


def test_measurement_id_with_no_tracker_is_surfaced():
    res = {
        "analytics_trackers": [],
        "analytics_ids": [{"platform": "matomo", "id_value": "7", "occurrences": 3,
                            "pivot_url": "https://m", "first_seen": "2024-01", "last_seen": "2024-05"}],
    }
    merge_analytics_ids(res)
    assert res["analytics_ids"] == []
    trk = res["analytics_trackers"]
    assert len(trk) == 1
    assert trk[0]["id"] == "7"
    assert trk[0]["type"] == "matomo"


def test_case_insensitive_dedup():
    res = {
        "analytics_trackers": [{"type": "GA4", "id": "G-ABCDE12345", "occurrences": 2}],
        "analytics_ids": [{"platform": "ga4", "id_value": "g-abcde12345", "occurrences": 5}],
    }
    merge_analytics_ids(res)
    assert len(res["analytics_trackers"]) == 1
    assert res["analytics_trackers"][0]["occurrences"] == 5  # max


def test_distinct_ids_both_kept():
    res = {
        "analytics_trackers": [{"type": "GTM", "id": "GTM-AAAAAA", "occurrences": 1}],
        "analytics_ids": [{"platform": "ga4", "id_value": "G-ZZZZZZZZZZ", "occurrences": 1}],
    }
    merge_analytics_ids(res)
    ids = {t["id"] for t in res["analytics_trackers"]}
    assert ids == {"GTM-AAAAAA", "G-ZZZZZZZZZZ"}


def test_noop_when_no_analytics_ids():
    res = {"analytics_trackers": [{"type": "GTM", "id": "GTM-X", "occurrences": 1}], "analytics_ids": []}
    merge_analytics_ids(res)
    assert len(res["analytics_trackers"]) == 1
    assert res["analytics_ids"] == []
