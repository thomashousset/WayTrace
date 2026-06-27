# backend/tests/test_collector.py
"""Tests for the collection pipeline (CDX crawl + HTML download)."""
import json
import os
import tempfile
import zlib
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from db import init_db
from services.collector import crawl_cdx, download_pages, discover_backup_files


@pytest_asyncio.fixture
async def test_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


FAKE_CDX_RESPONSE_PAGE1 = json.dumps([
    ["timestamp", "original", "statuscode", "mimetype", "digest"],
    ["20200101120000", "http://example.com/", "200", "text/html", "ABC123"],
    ["20200201120000", "http://example.com/about", "200", "text/html", "DEF456"],
    ["20200301120000", "http://example.com/backup.sql", "200", "application/octet-stream", "GHI789"],
]).encode()


@pytest.mark.asyncio
async def test_crawl_cdx_stores_snapshots(test_db):
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=FAKE_CDX_RESPONSE_PAGE1)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)

    result = await crawl_cdx(mock_session, "example.com", test_db)
    assert result["snapshots_indexed"] == 3

    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT count(*) FROM snapshots")
        count = (await cursor.fetchone())[0]
    assert count == 3


@pytest.mark.asyncio
async def test_crawl_cdx_creates_domain(test_db):
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=FAKE_CDX_RESPONSE_PAGE1)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)

    await crawl_cdx(mock_session, "example.com", test_db)

    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT name FROM domains WHERE id = 1")
        row = await cursor.fetchone()
    assert row[0] == "example.com"


@pytest.mark.asyncio
async def test_crawl_cdx_idempotent(test_db):
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=FAKE_CDX_RESPONSE_PAGE1)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)

    await crawl_cdx(mock_session, "example.com", test_db)
    await crawl_cdx(mock_session, "example.com", test_db)

    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT count(*) FROM snapshots")
        count = (await cursor.fetchone())[0]
    assert count == 3


@pytest.mark.asyncio
async def test_crawl_cdx_uses_wildcard_url(test_db):
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=json.dumps([
        ["timestamp", "original", "statuscode", "mimetype", "digest"],
    ]).encode())
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)

    await crawl_cdx(mock_session, "example.com", test_db)

    call_args = mock_session.get.call_args
    params = call_args[1].get("params", call_args.kwargs.get("params", {}))
    assert params["url"] == "*.example.com/*"


@pytest.mark.asyncio
async def test_download_pages_stores_html(test_db):
    async with aiosqlite.connect(test_db) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('example.com')")
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, selected) "
            "VALUES (1, 'http://example.com/', '20200101120000', 'text/html', 1)"
        )
        await db.execute("INSERT INTO pages (snapshot_id, status) VALUES (1, 'pending')")
        await db.commit()

    fake_html = "<html><body>Hello</body></html>"
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.read = AsyncMock(return_value=fake_html.encode("utf-8"))
    mock_response.headers = {}
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)

    stats = await download_pages(mock_session, 1, test_db, max_concurrent=1)
    assert stats["pages_downloaded"] == 1
    assert stats["pages_failed"] == 0

    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT html, status FROM pages WHERE id = 1")
        row = await cursor.fetchone()
    assert row[1] == "done"
    assert zlib.decompress(row[0]).decode() == fake_html


@pytest.mark.asyncio
async def test_download_pages_marks_failed(test_db):
    async with aiosqlite.connect(test_db) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('example.com')")
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, selected) "
            "VALUES (1, 'http://example.com/gone', '20200101120000', 'text/html', 1)"
        )
        await db.execute("INSERT INTO pages (snapshot_id, status) VALUES (1, 'pending')")
        await db.commit()

    mock_response = AsyncMock()
    mock_response.status = 404
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)

    stats = await download_pages(mock_session, 1, test_db, max_concurrent=1)
    assert stats["pages_failed"] == 1

    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT status FROM pages WHERE id = 1")
        row = await cursor.fetchone()
    assert row[0] == "failed"


@pytest.mark.asyncio
async def test_discover_backup_files(test_db):
    async with aiosqlite.connect(test_db) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('example.com')")
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype) VALUES "
            "(1, 'http://example.com/dump.sql', '20200101120000', 'application/octet-stream')"
        )
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype) VALUES "
            "(1, 'http://example.com/backup.zip', '20200201120000', 'application/zip')"
        )
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype) VALUES "
            "(1, 'http://example.com/index.html', '20200301120000', 'text/html')"
        )
        await db.commit()

    count = await discover_backup_files(1, test_db)
    assert count >= 2

    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT url, extension FROM backup_files ORDER BY extension")
        rows = await cursor.fetchall()
    extensions = [r[1] for r in rows]
    assert ".sql" in extensions
    assert ".zip" in extensions
