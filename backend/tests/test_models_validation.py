"""Pydantic model validation. covers extra='forbid', SnapshotRef URL/timestamp,
date_from/date_to formats, and the selected_snapshots length cap."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from models import JobCreate, ScanConfig, SnapshotRef


# ------ SnapshotRef ------------------------------------------------------

def test_snapshot_ref_accepts_plain_http_url():
    SnapshotRef(timestamp="20200101120000", url="http://example.com/")


def test_snapshot_ref_rejects_javascript_scheme():
    with pytest.raises(ValidationError):
        SnapshotRef(timestamp="20200101120000", url="javascript:alert(1)")


def test_snapshot_ref_rejects_data_url():
    with pytest.raises(ValidationError):
        SnapshotRef(timestamp="20200101120000", url="data:text/html,<script>alert(1)</script>")


def test_snapshot_ref_rejects_file_scheme():
    with pytest.raises(ValidationError):
        SnapshotRef(timestamp="20200101120000", url="file:///etc/passwd")


def test_snapshot_ref_rejects_bad_timestamp():
    with pytest.raises(ValidationError):
        SnapshotRef(timestamp="2020-01-01", url="http://example.com/")
    with pytest.raises(ValidationError):
        SnapshotRef(timestamp="' OR 1=1 --", url="http://example.com/")


def test_snapshot_ref_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SnapshotRef(
            timestamp="20200101120000",
            url="http://example.com/",
            malicious="payload",
        )


# ------ ScanConfig -------------------------------------------------------

def test_scan_config_rejects_unknown_field():
    with pytest.raises(ValidationError):
        ScanConfig(cap=10, categorie=["emails"])  # typo, not 'categories'


def test_scan_config_accepts_date_month():
    c = ScanConfig(date_from="2020-01", date_to="2024-12")
    assert c.date_from == "2020-01"


def test_scan_config_rejects_garbage_date():
    with pytest.raises(ValidationError):
        ScanConfig(date_from="' OR 1=1")
    with pytest.raises(ValidationError):
        ScanConfig(date_from="2020/01")
    with pytest.raises(ValidationError):
        ScanConfig(date_from="Jan 2020")


def test_scan_config_cap_negative_rejected():
    with pytest.raises(ValidationError):
        ScanConfig(cap=-1)


# ------ JobCreate --------------------------------------------------------

def test_job_create_selected_snapshots_capped():
    many = [
        {"timestamp": f"202001{d:02d}120000", "url": "http://x.com/"}
        for d in range(1, 32)
    ] * 200  # 6 200 items
    with pytest.raises(ValidationError):
        JobCreate(domain="example.com", selected_snapshots=many)


def test_job_create_rejects_extra_field():
    with pytest.raises(ValidationError):
        JobCreate(domain="example.com", admin=True)


