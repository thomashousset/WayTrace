"""Full-text search over scanned page content (FTS5)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import db
from config import settings
from db import init_db, index_scan_pages, search_scan_pages, save_job, delete_job
from main import app
from store import store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def fresh_db(tmp_path, monkeypatch):
    p = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", p)
    await init_db(p)
    await store._reset_for_tests()
    yield p
    db._db_path = None


PAGES = [
    ("20200101000000", "http://x.com/", "Contact admin@x.com. Société de cybersécurité à Paris."),
    ("20210101000000", "http://x.com/careers", "We are hiring engineers. Careers and jobs."),
]


@pytest.mark.anyio
async def test_index_and_search_basic():
    assert await index_scan_pages("s1", PAGES) == 2
    res = await search_scan_pages("s1", "careers")
    assert len(res) == 1
    assert res[0]["url"] == "http://x.com/careers"
    assert "<mark>Careers</mark>" in res[0]["snippet"] or "<mark>careers</mark>" in res[0]["snippet"].lower()


@pytest.mark.anyio
async def test_search_is_accent_insensitive():
    await index_scan_pages("s2", PAGES)
    res = await search_scan_pages("s2", "cybersecurite")  # no accents in query
    assert len(res) == 1
    assert "<mark>" in res[0]["snippet"]


@pytest.mark.anyio
async def test_search_is_scoped_to_the_scan():
    await index_scan_pages("s3", PAGES)
    await index_scan_pages("other", [("20200101000000", "http://y.com/", "careers elsewhere")])
    res = await search_scan_pages("s3", "careers")
    assert all(r["url"].startswith("http://x.com") for r in res)


@pytest.mark.anyio
async def test_delete_job_purges_the_index():
    await index_scan_pages("s4", PAGES)
    await delete_job("s4")
    assert await search_scan_pages("s4", "careers") == []


@pytest.mark.anyio
async def test_empty_query_returns_nothing():
    await index_scan_pages("s5", PAGES)
    assert await search_scan_pages("s5", "   ") == []


@pytest.mark.anyio
async def test_punctuation_queries_do_not_throw():
    # A raw email / URL / hyphen / bare operator is invalid FTS5 MATCH syntax and
    # used to raise a 'malformed MATCH' error ("Search failed" in the UI). These
    # must all run and, where the tokens are present, match.
    await index_scan_pages("s6", PAGES)
    for q in ["admin@x.com", "http://x.com/careers", "cyber-sécurité", 'careers "', "AND", "café * ("]:
        res = await search_scan_pages("s6", q)   # must not raise
        assert isinstance(res, list)
    # The email's tokens (admin, x, com) appear on page 1 -> a hit.
    assert any(r["url"] == "http://x.com/" for r in await search_scan_pages("s6", "admin@x.com"))


@pytest.mark.anyio
async def test_prefix_search_as_you_type():
    await index_scan_pages("s7", PAGES)
    # "care" should already match "Careers" via the trailing-token prefix.
    res = await search_scan_pages("s7", "care")
    assert any(r["url"].endswith("/careers") for r in res)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_search_endpoint(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="ftsapi", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    await index_scan_pages("ftsapi", PAGES)
    r = await client.get("/api/s/ftsapi/search", params={"q": "careers"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["results"][0]["url"] == "http://x.com/careers"


@pytest.mark.anyio
async def test_search_endpoint_unknown_scan_404(client):
    r = await client.get("/api/s/nope/search", params={"q": "x"})
    assert r.status_code == 404
