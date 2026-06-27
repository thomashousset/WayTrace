"""Covers the malformed-JSON recovery path in services/cdx.py that lets
preflight survive an archive.org half-baked response. The original bug was
seen live on oteria.fr and iana.org: a 43 MB CDX payload with one
truncated row at the tail raised JSONDecodeError and returned HTTP 500."""
from __future__ import annotations

import json

import pytest

from services.cdx import (
    _salvage_partial_cdx_json,
    detect_and_strip_resume_key,
    parse_cdx_rows,
)


def _make_bad_payload() -> tuple[bytes, json.JSONDecodeError]:
    """Construct a payload with the canonical CDX header + 2 rows + truncated tail."""
    valid = (
        '[["timestamp","original","statuscode","mimetype","digest"],'
        '["20200101000000","http://a.com/","200","text/html","AAA"],'
        '["20200201000000","http://a.com/b","200","text/html","BBB"],'
        '["2020030'
    )
    raw = valid.encode("utf-8")
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        return raw, exc
    raise AssertionError("payload must be malformed for the test")


def test_salvage_recovers_valid_prefix():
    raw, exc = _make_bad_payload()
    data = _salvage_partial_cdx_json(raw, exc, "example.com")
    assert data is not None
    rows = parse_cdx_rows(data)
    assert len(rows) == 2
    assert rows[0]["url"] == "http://a.com/"
    assert rows[1]["digest"] == "BBB"


def test_salvage_returns_none_on_total_garbage():
    """If no complete row boundary (]) exists before the error, return None."""
    raw = b'[["ts","url","st'
    try:
        json.loads(raw)
        pytest.fail("payload must be malformed")
    except json.JSONDecodeError as exc:
        assert _salvage_partial_cdx_json(raw, exc, "example.com") is None


def test_detect_and_strip_resume_key_trailing():
    data = [
        ["timestamp", "original", "statuscode", "mimetype", "digest"],
        ["20200101000000", "http://a/", "200", "text/html", "AAA"],
        ["a-long-resume-key-exceeding-20-chars-xyz"],
    ]
    key, remainder = detect_and_strip_resume_key(data)
    assert key == "a-long-resume-key-exceeding-20-chars-xyz"
    assert len(remainder) == 2


def test_detect_and_strip_resume_key_absent():
    data = [
        ["timestamp", "original", "statuscode", "mimetype", "digest"],
        ["20200101000000", "http://a/", "200", "text/html", "AAA"],
    ]
    key, remainder = detect_and_strip_resume_key(data)
    assert key is None
    assert remainder is data
