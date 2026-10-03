"""A failed scan records why it failed.

Twenty-three scans failed in production over thirty days and every one of them
stored an empty meta, so the only trace of the cause was in container logs that
a rebuild destroys. The answer to "why did these fail" has to survive a deploy,
and it has to be queryable, because the shape of the failures is a product
signal: nearly all of them are archive.org refusing to index a mega-domain, not
anything the tool did wrong.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from config import settings
from routers.scan import classify_failure, _failure_meta


def test_cdx_budget_exhaustion_is_named():
    """The 141-second failure signature: two attempts plus a fallback, all timing
    out. google.com, youtube.com, instagram.com, mail.ru all land here."""
    exc = RuntimeError("CDX API unreachable after 2 attempts: ")
    code, message = classify_failure(exc)
    assert code == "cdx_unreachable"
    assert "did not return" in message
    assert "Scan failed" != message   # the generic wording is what we are removing


def test_empty_body_from_archive_is_named():
    """archive.org answers 200 with an empty body on some domains (gmail.com).
    json.loads then fails at position 0 and the salvage path gives up."""
    code, _ = classify_failure(RuntimeError("CDX returned malformed JSON at position 0"))
    assert code == "cdx_malformed"


def test_breaker_open_is_named():
    exc = RuntimeError("archive.org is rate-limiting us; cooling down for 180s before more requests")
    code, message = classify_failure(exc)
    assert code == "archive_paused"
    assert "rate-limiting" in message


def test_scan_timeout_is_named():
    code, message = classify_failure(asyncio.TimeoutError())
    assert code == "scan_timeout"
    assert str(settings.scan_timeout_seconds // 60) in message


def test_anything_else_stays_unexpected():
    """Unknown failures must NOT be forced into a bucket: the taxonomy exists to
    be trusted, so a genuine bug has to stand out as unclassified."""
    code, message = classify_failure(ValueError("selectolax exploded"))
    assert code == "unexpected"
    assert message == "Scan failed"


def test_failure_meta_shape_is_queryable():
    exc = RuntimeError("CDX API unreachable after 2 attempts: ")
    meta = _failure_meta("instagram.com", "cdx_unreachable", exc, start=0.0)
    assert meta["domain"] == "instagram.com"
    assert meta["error"] == "cdx_unreachable"
    assert meta["error_detail"].startswith("RuntimeError:")
    assert meta["failed_at"].endswith("Z")
    assert isinstance(meta["scan_duration_seconds"], float)


def test_failure_detail_keeps_the_exception_type():
    """A bare TimeoutError stringifies to nothing, which is precisely what made
    the production logs unreadable. The type has to be in the record."""
    meta = _failure_meta("x.com", "cdx_unreachable", asyncio.TimeoutError(), start=0.0)
    assert meta["error_detail"] == "TimeoutError: "


def test_failure_detail_is_bounded():
    """No unbounded blob in a column we will be querying for years."""
    meta = _failure_meta("x.com", "unexpected", RuntimeError("y" * 5000), start=0.0)
    assert len(meta["error_detail"]) <= 300


def test_no_scan_content_leaks_into_the_failure_record():
    """The failure record outlives nothing it should not: domain and cause only,
    no page data, no results, no account."""
    meta = _failure_meta("target.example", "cdx_unreachable", RuntimeError("x"), start=0.0)
    assert set(meta) == {
        "domain", "error", "error_detail", "failed_at", "scan_duration_seconds",
    }
