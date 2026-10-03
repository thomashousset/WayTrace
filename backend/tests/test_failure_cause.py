"""A failed scan records why it failed.

Twenty-three scans failed in production over thirty days and every one of them
stored an empty meta, so the only trace of the cause was in container logs that
a rebuild destroys. The answer to "why did these fail" has to survive a deploy,
and it has to be queryable.

The first version of this file assumed those failures were archive.org refusing
to index mega-domains. Measured on 2026-09-06, that was wrong: the same query
took 8.7 s on one domain and over 50 s on a smaller one, index size did not
predict anything, and the domains that failed ranged from 98 index pages
(ehesp.fr) to 4.6 million (google.com). So the taxonomy separates a deadline we
set ourselves, `cdx_timeout`, from an error archive.org returned, `cdx_error`.
Only the second is theirs to answer for, and the product must not blame them for
the first.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from config import settings
from routers.scan import classify_failure, _failure_meta


def test_our_own_deadline_is_named_and_does_not_blame_archive():
    """We stopped waiting. The sentence must not present that as archive.org
    being slow, because it is our deadline that ran out."""
    exc = RuntimeError("CDX API unreachable after 3 attempts: TimeoutError")
    code, message = classify_failure(exc)
    assert code == "cdx_timeout"
    assert "did not answer within the time we allow" in message
    assert "slow" not in message.lower()


def test_an_error_from_archive_is_named_separately():
    """Archive.org answered, with a 503. Here it really is their side, and the
    sentence may say so."""
    exc = RuntimeError(
        "CDX API unreachable after 3 attempts: "
        "ClientResponseError: 503, message='Service Unavailable'"
    )
    code, message = classify_failure(exc)
    assert code == "cdx_error"
    assert "temporary on their side" in message


def test_the_two_cdx_causes_do_not_collide():
    """Both texts contain 'cdx api unreachable'; only the timeout one also
    carries the exception type, and it has to win."""
    timeout = classify_failure(
        RuntimeError("CDX API unreachable after 2 attempts: TimeoutError"))[0]
    autre = classify_failure(
        RuntimeError("CDX API unreachable after 2 attempts: ClientResponseError: 503"))[0]
    assert (timeout, autre) == ("cdx_timeout", "cdx_error")


def test_empty_body_from_archive_is_named():
    """archive.org answers 200 with an empty body on some domains (gmail.com).
    json.loads then fails at position 0 and the salvage path gives up."""
    code, _ = classify_failure(RuntimeError("CDX returned malformed JSON at position 0"))
    assert code == "cdx_malformed"


def test_breaker_open_is_named():
    exc = RuntimeError("archive.org is rate-limiting us; cooling down for 180s before more requests")
    code, message = classify_failure(exc)
    assert code == "archive_paused"
    assert "does not get blocked" in message


def test_scan_timeout_is_named():
    code, message = classify_failure(asyncio.TimeoutError())
    assert code == "scan_timeout"
    assert str(settings.scan_timeout_seconds // 60) in message


def test_anything_else_stays_unexpected():
    """Unknown failures must NOT be forced into a bucket: the taxonomy exists to
    be trusted, so a genuine bug has to stand out as unclassified."""
    code, message = classify_failure(ValueError("selectolax exploded"))
    assert code == "unexpected"
    assert "not identified yet" in message


def test_failure_meta_shape_is_queryable():
    exc = RuntimeError("CDX API unreachable after 2 attempts: ")
    meta = _failure_meta("instagram.com", "cdx_timeout", exc, start=0.0)
    assert meta["domain"] == "instagram.com"
    assert meta["error"] == "cdx_timeout"
    assert meta["error_detail"].startswith("RuntimeError:")
    assert meta["failed_at"].endswith("Z")
    assert isinstance(meta["scan_duration_seconds"], float)


def test_failure_detail_keeps_the_exception_type():
    """A bare TimeoutError stringifies to nothing, which is precisely what made
    the production logs unreadable. The type has to be in the record."""
    meta = _failure_meta("x.com", "cdx_timeout", asyncio.TimeoutError(), start=0.0)
    assert meta["error_detail"] == "TimeoutError: "


def test_failure_detail_is_bounded():
    """No unbounded blob in a column we will be querying for years."""
    meta = _failure_meta("x.com", "unexpected", RuntimeError("y" * 5000), start=0.0)
    assert len(meta["error_detail"]) <= 300


def test_no_scan_content_leaks_into_the_failure_record():
    """The failure record outlives nothing it should not: domain and cause only,
    no page data, no results, no account."""
    meta = _failure_meta("target.example", "cdx_timeout", RuntimeError("x"), start=0.0)
    assert set(meta) == {
        "domain", "error", "error_detail", "failed_at", "scan_duration_seconds",
    }
