"""A finished scan is never reused across accounts.

The deduplication guardrail exists to spare archive.org, but on this service a
domain is an investigation target: handing account B the report account A ran
discloses the findings AND the fact that somebody is looking at that target.
These tests pin the scoping so the guardrail cannot be widened back by
accident.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from db import find_public_demo_scan, find_recent_scan_for_domain, init_db, save_job


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _completed(url_id, domain, user_id, *, age_days=0, status="completed"):
    now = datetime.now(timezone.utc) - timedelta(days=age_days)
    await save_job(
        url_id=url_id, domain=domain, client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=14),
        completed_at=now, status=status, meta={}, results={}, user_id=user_id,
    )


@pytest.mark.asyncio
async def test_other_accounts_scan_is_invisible(tmp_db_path):
    await init_db(tmp_db_path)
    await _completed("aaaaaaaaaaaaaaaaaaaaaaaa", "target.example", user_id=1)

    # The owner gets their own scan back.
    assert (await find_recent_scan_for_domain("target.example", 1))["url_id"] \
        == "aaaaaaaaaaaaaaaaaaaaaaaa"
    # Anybody else gets nothing, and therefore learns nothing.
    assert await find_recent_scan_for_domain("target.example", 2) is None
    assert await find_recent_scan_for_domain("target.example", 999) is None


@pytest.mark.asyncio
async def test_anonymous_does_not_match_an_owned_scan(tmp_db_path):
    """owner_id=None means the anonymous owner, not a wildcard. An unowned
    lookup must not fall through to an account's scan."""
    await init_db(tmp_db_path)
    await _completed("bbbbbbbbbbbbbbbbbbbbbbbb", "target.example", user_id=7)
    assert await find_recent_scan_for_domain("target.example", None) is None


@pytest.mark.asyncio
async def test_self_hosted_dedupe_still_works(tmp_db_path):
    """No accounts: every scan is unowned and reuse must still hit, otherwise
    the self-hosted build would re-scan the same domain on every submission."""
    await init_db(tmp_db_path)
    await _completed("cccccccccccccccccccccccc", "mine.example", user_id=None)
    row = await find_recent_scan_for_domain("mine.example", None)
    assert row is not None and row["url_id"] == "cccccccccccccccccccccccc"


@pytest.mark.asyncio
async def test_owner_gets_the_most_recent_of_their_own(tmp_db_path):
    await init_db(tmp_db_path)
    await _completed("dddddddddddddddddddddddd", "shared.example", user_id=1, age_days=3)
    await _completed("eeeeeeeeeeeeeeeeeeeeeeee", "shared.example", user_id=2, age_days=1)
    await _completed("ffffffffffffffffffffffff", "shared.example", user_id=1, age_days=2)
    # Account 2's scan is the newest overall, and must still be skipped.
    assert (await find_recent_scan_for_domain("shared.example", 1))["url_id"] \
        == "ffffffffffffffffffffffff"
    assert (await find_recent_scan_for_domain("shared.example", 2))["url_id"] \
        == "eeeeeeeeeeeeeeeeeeeeeeee"


@pytest.mark.asyncio
async def test_failed_and_expired_scans_are_not_reused(tmp_db_path):
    await init_db(tmp_db_path)
    await _completed("gggggggggggggggggggggggg", "failed.example", user_id=1, status="failed")
    assert await find_recent_scan_for_domain("failed.example", 1) is None

    now = datetime.now(timezone.utc)
    await save_job(
        url_id="hhhhhhhhhhhhhhhhhhhhhhhh", domain="old.example", client_ip="1.1.1.1",
        created_at=now - timedelta(days=30), expires_at=now - timedelta(days=1),
        completed_at=now - timedelta(days=30), status="completed",
        meta={}, results={}, user_id=1,
    )
    assert await find_recent_scan_for_domain("old.example", 1) is None


@pytest.mark.asyncio
async def test_demo_scan_is_the_only_cross_account_lookup(tmp_db_path):
    """The operator's configured demo domain is published on purpose, so that
    one lookup ignores ownership. It is a separate function precisely so the
    exception has to be asked for by name."""
    await init_db(tmp_db_path)
    await _completed("iiiiiiiiiiiiiiiiiiiiiiii", "demo.example", user_id=42)
    assert await find_public_demo_scan("demo.example") is not None
    assert await find_recent_scan_for_domain("demo.example", None) is None
    assert await find_public_demo_scan("") is None
    assert await find_public_demo_scan("never-scanned.example") is None


@pytest.mark.asyncio
async def test_owner_id_is_a_required_argument(tmp_db_path):
    """Forgetting the scope must be a crash, not a silent cross-account read.
    This is the whole defence: the old signature defaulted to None and every
    caller took the default."""
    await init_db(tmp_db_path)
    with pytest.raises(TypeError):
        await find_recent_scan_for_domain("target.example")
