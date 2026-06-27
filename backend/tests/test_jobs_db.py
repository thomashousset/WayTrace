"""Tests for the v2 jobs table and its CRUD helpers."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from db import (
    init_db,
    save_job,
    get_job_by_url_id,
    set_published,
    list_feed,
    delete_expired_jobs,
    expire_job_now,
)


@pytest_asyncio.fixture
async def db_ready(tmp_path):
    p = str(tmp_path / "jobs.db")
    await init_db(p)
    yield p
    # Reset module-global so subsequent tests re-init their own
    import db as _db
    _db._db_path = None


def _utc(*args, **kwargs):
    return datetime(*args, **kwargs, tzinfo=timezone.utc) if args else datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_save_and_get_by_url_id(db_ready):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="abc123",
        domain="example.com",
        client_ip="1.1.1.1",
        created_at=now,
        expires_at=now + timedelta(days=7),
        status="completed",
        meta={"snapshots_analyzed": 12, "date_first_seen": "2020-01"},
        results={"emails": [{"value": "a@b.c"}]},
    )
    job = await get_job_by_url_id("abc123")
    assert job is not None
    assert job["domain"] == "example.com"
    assert job["status"] == "completed"
    assert job["meta"]["snapshots_analyzed"] == 12
    assert job["results"]["emails"][0]["value"] == "a@b.c"
    assert job["is_published"] == 0
    assert job["published_at"] is None
    assert job["client_ip"] == "1.1.1.1"


@pytest.mark.asyncio
async def test_get_unknown_url_id_returns_none(db_ready):
    assert await get_job_by_url_id("nope") is None


@pytest.mark.asyncio
async def test_set_published_flips_flag(db_ready):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="pub1", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    ok = await set_published("pub1", True)
    assert ok is True
    job = await get_job_by_url_id("pub1")
    assert job["is_published"] == 1
    assert job["published_at"] is not None

    await set_published("pub1", False)
    job = await get_job_by_url_id("pub1")
    assert job["is_published"] == 0
    assert job["published_at"] is None


@pytest.mark.asyncio
async def test_set_published_unknown_returns_false(db_ready):
    assert await set_published("nope", True) is False


@pytest.mark.asyncio
async def test_feed_returns_only_published_sorted_desc(db_ready):
    now = datetime.now(timezone.utc)
    for i in range(3):
        uid = f"u{i}"
        await save_job(
            url_id=uid, domain=f"d{i}.com", client_ip="1.1.1.1",
            created_at=now, expires_at=now + timedelta(days=7),
            status="completed",
            meta={"date_first_seen": "2020-01", "snapshots_analyzed": i + 1},
            results={"emails": [{"value": "x@y.z"}], "subdomains": [{"value": "a"}]},
        )
        if i != 1:
            await set_published(uid, True)
        await asyncio.sleep(0.01)  # ensure distinct published_at

    feed = await list_feed(limit=10)
    assert len(feed) == 2
    assert [it["url_id"] for it in feed] == ["u2", "u0"]
    # Top categories computed correctly
    assert feed[0]["summary"]["top_categories"]
    assert feed[0]["summary"]["snapshots_analyzed"] == 3


@pytest.mark.asyncio
async def test_feed_excludes_expired(db_ready):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="fresh", domain="a.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=1),
        status="completed", meta={}, results={},
    )
    await save_job(
        url_id="stale", domain="b.com", client_ip="1.1.1.1",
        created_at=now - timedelta(days=8), expires_at=now - timedelta(hours=1),
        status="completed", meta={}, results={},
    )
    await set_published("fresh", True)
    await set_published("stale", True)

    feed = await list_feed()
    assert [it["url_id"] for it in feed] == ["fresh"]


@pytest.mark.asyncio
async def test_feed_pagination(db_ready):
    now = datetime.now(timezone.utc)
    for i in range(5):
        uid = f"p{i}"
        await save_job(
            url_id=uid, domain=f"{i}.com", client_ip="1.1.1.1",
            created_at=now, expires_at=now + timedelta(days=7),
            status="completed", meta={}, results={},
        )
        await set_published(uid, True)
        await asyncio.sleep(0.01)

    page1 = await list_feed(limit=2, offset=0)
    page2 = await list_feed(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert page1[0]["url_id"] != page2[0]["url_id"]


@pytest.mark.asyncio
async def test_delete_expired_only_removes_expired(db_ready):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="alive", domain="a.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=1),
        status="completed", meta={}, results={},
    )
    await save_job(
        url_id="dead", domain="d.com", client_ip="1.1.1.1",
        created_at=now - timedelta(days=8), expires_at=now - timedelta(hours=1),
        status="completed", meta={}, results={},
    )
    n = await delete_expired_jobs()
    assert n == 1
    assert await get_job_by_url_id("alive") is not None
    assert await get_job_by_url_id("dead") is None


@pytest.mark.asyncio
async def test_expire_job_now_works(db_ready):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="kill_me", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    ok = await expire_job_now("kill_me")
    assert ok is True
    # Still in DB but expired; cleanup should remove it
    n = await delete_expired_jobs()
    assert n == 1
    assert await get_job_by_url_id("kill_me") is None


@pytest.mark.asyncio
async def test_save_overwrite_preserves_published_flag(db_ready):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="x1", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    await set_published("x1", True)
    # Overwrite, published flag must NOT be reset
    await save_job(
        url_id="x1", domain="x.com", client_ip="2.2.2.2",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={"new": True}, results={"emails": []},
    )
    job = await get_job_by_url_id("x1")
    assert job["is_published"] == 1
    assert job["published_at"] is not None
    assert job["client_ip"] == "2.2.2.2"
    assert job["meta"]["new"] is True
