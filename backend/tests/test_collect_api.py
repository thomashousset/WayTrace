import os
import tempfile
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from db import init_db


@pytest_asyncio.fixture
async def test_app():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db_path)
    from config import settings
    original = settings.database_url
    settings.database_url = db_path
    from main import app
    yield app
    settings.database_url = original
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_collect_rejects_invalid_domain(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.post("/api/collect", json={"domain": "not valid!"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_collect_rejects_url(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.post("/api/collect", json={"domain": "https://example.com"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_collect_rejects_ip(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.post("/api/collect", json={"domain": "192.168.1.1"})
    assert resp.status_code == 422
