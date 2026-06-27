import json
import os
import tempfile
import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from db import init_db


@pytest_asyncio.fixture
async def seeded_app():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('example.com')")
        await db.execute(
            "INSERT INTO findings (domain_id, category, value, first_seen, last_seen, occurrences, severity) "
            "VALUES (1, 'emails', 'admin@example.com', '2020-01', '2020-06', 5, 'HIGH')"
        )
        await db.execute(
            "INSERT INTO findings (domain_id, category, value, first_seen, last_seen, occurrences) "
            "VALUES (1, 'endpoints', '/api/v1', '2020-01', '2020-03', 2)"
        )
        await db.commit()
    from config import settings
    original = settings.database_url
    settings.database_url = db_path
    from main import app
    yield app
    settings.database_url = original
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_list_domains(seeded_app):
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/domains")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "example.com"


@pytest.mark.asyncio
async def test_get_domain_findings(seeded_app):
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/domains/1/findings?category=emails")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["value"] == "admin@example.com"


@pytest.mark.asyncio
async def test_get_domain_findings_filter_severity(seeded_app):
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/domains/1/findings?severity=HIGH")
    assert resp.status_code == 200
    data = resp.json()
    assert all(f["severity"] == "HIGH" for f in data)


@pytest.mark.asyncio
async def test_export_json(seeded_app):
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/domains/1/export?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert "emails" in data
    assert "endpoints" in data


@pytest_asyncio.fixture
async def seeded_page_app():
    """Fixture seeding a domain + snapshot + archived page with inline script."""
    import zlib
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('xss-test.example')")
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype) "
            "VALUES (1, 'http://xss-test.example/', '20200101000000', 'text/html')"
        )
        payload = b"<html><script>alert(1)</script><body>ok</body></html>"
        await db.execute(
            "INSERT INTO pages (snapshot_id, html, status) VALUES (1, ?, 'done')",
            (zlib.compress(payload),),
        )
        await db.commit()
    from config import settings
    original = settings.database_url
    settings.database_url = db_path
    from main import app
    yield app
    settings.database_url = original
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_view_page_security_headers(seeded_page_app):
    """The archived-page viewer must always ship with a CSP sandbox + iframe
    + content-type lock down, even if the archived body contains a <script>.
    Regression guard for the stored-XSS fix."""
    async with AsyncClient(transport=ASGITransport(app=seeded_page_app), base_url="http://test") as client:
        resp = await client.get("/api/pages/1/view")
    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "")
    assert csp.startswith("sandbox;"), f"CSP missing sandbox directive: {csp!r}"
    assert "default-src 'none'" in csp
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    # Body is served as-is. the CSP handles the neutralisation, not the
    # response body. Just confirm the response is served, not sanitised.
    assert b"<script>alert(1)</script>" in resp.content


@pytest.mark.asyncio
async def test_view_page_404_on_missing(seeded_page_app):
    async with AsyncClient(transport=ASGITransport(app=seeded_page_app), base_url="http://test") as client:
        resp = await client.get("/api/pages/9999/view")
    assert resp.status_code == 404
