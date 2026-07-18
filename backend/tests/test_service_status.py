"""/api/service-status states: ok, busy, maintenance; flag persistence."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import db as dbmod
from config import settings
from services import maintenance
from store import store


@pytest_asyncio.fixture(autouse=True)
async def _reset(tmp_path, monkeypatch):
    await dbmod.init_db(str(tmp_path / "t.db"))
    monkeypatch.setattr(settings, "max_active_total", 1)
    monkeypatch.setattr(settings, "max_queue_total", 100)
    monkeypatch.setattr(settings, "max_active_per_ip", 50)
    await store._reset_for_tests()
    await maintenance.set_state(False, "")
    yield
    await store._reset_for_tests()
    await maintenance.set_state(False, "")


@pytest_asyncio.fixture()
async def client():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_ok_state(client):
    r = await client.get("/api/service-status")
    assert r.status_code == 200
    d = r.json()
    assert d["service"]["state"] == "ok"
    assert d["service"]["retention_days"] == settings.scan_retention_days
    assert d["service"]["maintenance"] is False
    assert "state" in d["archive"]


@pytest.mark.asyncio
async def test_busy_when_queue_backs_up(client):
    for i in range(4):   # 1 active + 3 waiting
        await store.create_job(f"busy{i}.com", f"10.0.0.{i}")
    r = await client.get("/api/service-status")
    d = r.json()["service"]
    assert d["state"] == "busy"
    assert d["waiting"] >= 3


@pytest.mark.asyncio
async def test_maintenance_wins(client):
    for i in range(4):   # busy queue AND maintenance: maintenance wins
        await store.create_job(f"busy{i}.com", f"10.0.0.{i}")
    await maintenance.set_state(True, "DB upgrade")
    r = await client.get("/api/service-status")
    d = r.json()["service"]
    assert d["state"] == "maintenance"
    assert d["maintenance_message"] == "DB upgrade"


@pytest.mark.asyncio
async def test_flag_survives_reload():
    await maintenance.set_state(True, "hello")
    maintenance._state.update({"enabled": False, "message": ""})
    await maintenance.load_from_db()
    assert maintenance.is_enabled() is True
    assert maintenance.message() == "hello"
