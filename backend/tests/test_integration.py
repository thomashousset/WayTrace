# backend/tests/test_integration.py
"""End-to-end integration test: collect -> analyze -> query results (mocked HTTP)."""
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from db import init_db
from services.collector import crawl_cdx, download_pages, discover_backup_files
from services.filters import select_snapshots_in_db
from routers.analyze import run_analysis


FAKE_CDX = json.dumps([
    ["timestamp", "original", "statuscode", "mimetype", "digest"],
    ["20200101120000", "http://example.com/", "200", "text/html", "aaa"],
    ["20200201120000", "http://example.com/contact", "200", "text/html", "bbb"],
    ["20200301120000", "http://example.com/backup.sql", "200", "application/octet-stream", "ccc"],
]).encode()

FAKE_HTML_HOME = '<html><body><p>Email: info@testcorp.org</p><a href="/api/v1">API</a></body></html>'
FAKE_HTML_CONTACT = '<html><body><p>Phone: +33 1 42 68 53 00</p><h1>Index of /uploads</h1></body></html>'


@pytest_asyncio.fixture
async def test_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_full_pipeline(test_db):
    # Phase 1a: CDX crawl (mocked)
    cdx_resp = AsyncMock()
    cdx_resp.status = 200
    cdx_resp.read = AsyncMock(return_value=FAKE_CDX)
    cdx_resp.__aenter__ = AsyncMock(return_value=cdx_resp)
    cdx_resp.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(return_value=cdx_resp)

    result = await crawl_cdx(session, "example.com", test_db)
    assert result["snapshots_indexed"] == 3

    # Phase 1b: Selection
    sel = await select_snapshots_in_db(1, test_db)
    assert sel["selected_count"] == 2  # only HTML snapshots

    # Phase 1c: Download (mocked) - need different responses for each page
    call_count = 0
    html_pages = [FAKE_HTML_HOME, FAKE_HTML_CONTACT]

    def make_response():
        nonlocal call_count
        resp = AsyncMock()
        resp.status = 200
        resp.read = AsyncMock(return_value=html_pages[min(call_count, len(html_pages) - 1)].encode("utf-8"))
        resp.headers = {}
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        call_count += 1
        return resp

    session.get = MagicMock(side_effect=lambda *a, **kw: make_response())

    dl_stats = await download_pages(session, 1, test_db, max_concurrent=1)
    assert dl_stats["pages_downloaded"] == 2

    # Phase 1d: Backup discovery
    backup_count = await discover_backup_files(1, test_db)
    assert backup_count >= 1

    # Phase 2: Analysis
    results = await run_analysis(1, test_db)

    # Verify extracted data
    emails = [e["value"] for e in results.get("emails", [])]
    assert "info@testcorp.org" in emails

    endpoints = [e["path"] for e in results.get("endpoints", [])]
    assert "/api/v1" in endpoints

    # Verify findings persisted
    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT count(*) FROM findings WHERE domain_id = 1")
        count = (await cursor.fetchone())[0]
    assert count >= 2

    # Verify backup files found
    async with aiosqlite.connect(test_db) as db:
        cursor = await db.execute("SELECT url FROM backup_files WHERE domain_id = 1")
        backups = [r[0] for r in await cursor.fetchall()]
    assert any("backup.sql" in b for b in backups)


