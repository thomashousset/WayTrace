"""End-to-end tests for the public /api/s/{url_id} and /api/feed endpoints."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config import settings
from db import init_db, save_job, set_published
from main import app
from store import store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def reset_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_active_per_ip", 50)
    monkeypatch.setattr(settings, "max_queue_total", 100)
    # These tests pre-date account-required mode; scan anonymously.
    # require_account_to_scan only exists in the full (server) build; the solo
    # build has no account gate, so guard the override.
    if hasattr(settings, "require_account_to_scan"):
        monkeypatch.setattr(settings, "require_account_to_scan", False)
    await store._reset_for_tests()
    # Use a fresh DB for each test
    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    await init_db(db_path)
    yield
    await store._reset_for_tests()
    import db as _db
    _db._db_path = None


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------- GET /api/s/{url_id} ----------

@pytest.mark.anyio
async def test_get_s_returns_live_queued_scan(client):
    r = await client.post("/api/scan", json={"domain": "live.com"})
    uid = r.json()["url_id"]
    s = await client.get(f"/api/s/{uid}")
    assert s.status_code == 200
    body = s.json()
    assert body["url_id"] == uid
    assert body["status"] in ("queued", "running")


@pytest.mark.anyio
async def test_get_s_returns_persisted_completed_scan(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="persisted1", domain="p.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={"snapshots_analyzed": 5}, results={"emails": []},
    )
    r = await client.get("/api/s/persisted1")
    assert r.status_code == 200
    body = r.json()
    assert body["domain"] == "p.com"
    assert body["status"] == "completed"
    assert body["meta"]["snapshots_analyzed"] == 5


@pytest.mark.anyio
async def test_get_s_returns_404_when_unknown(client):
    r = await client.get("/api/s/nonexistent_id_x")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_get_s_returns_410_when_expired(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="oldscan", domain="x.com", client_ip="1.1.1.1",
        created_at=now - timedelta(days=8),
        expires_at=now - timedelta(hours=1),
        status="completed", meta={}, results={},
    )
    r = await client.get("/api/s/oldscan")
    assert r.status_code == 410


# ---------- POST /api/s/{url_id}/publish ----------

@pytest.mark.anyio
async def test_publish_then_unpublish(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="pubme", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    r = await client.post("/api/s/pubme/publish", json={"published": True})
    assert r.status_code == 200
    assert r.json()["published"] is True

    r = await client.post("/api/s/pubme/publish", json={"published": False})
    assert r.status_code == 200
    assert r.json()["published"] is False


@pytest.mark.anyio
async def test_publish_unknown_scan_returns_404(client):
    r = await client.post("/api/s/nope/publish", json={"published": True})
    assert r.status_code == 404


# ---------- DELETE /api/s/{url_id} ----------

@pytest.mark.anyio
async def test_delete_persisted_scan(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="killme", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    r = await client.delete("/api/s/killme")
    assert r.status_code == 200
    # Delete now hard-removes the row, so a subsequent GET is a clean 404.
    r2 = await client.get("/api/s/killme")
    assert r2.status_code == 404


@pytest.mark.anyio
async def test_delete_unknown_returns_404(client):
    r = await client.delete("/api/s/nope")
    assert r.status_code == 404


# ---------- GET /api/feed ----------

@pytest.mark.anyio
async def test_feed_lists_only_published(client):
    import asyncio
    now = datetime.now(timezone.utc)
    for i in range(3):
        uid = f"feedid{i}"
        await save_job(
            url_id=uid, domain=f"d{i}.com", client_ip="1.1.1.1",
            created_at=now, expires_at=now + timedelta(days=7),
            status="completed",
            meta={"date_first_seen": "2020-01"},
            results={"emails": [{"value": "a@b.c"}]},
        )
        if i != 1:
            await set_published(uid, True)
        await asyncio.sleep(0.01)

    r = await client.get("/api/feed")
    body = r.json()
    assert body["count"] == 2
    ids = [it["url_id"] for it in body["items"]]
    assert "feedid1" not in ids


@pytest.mark.anyio
async def test_feed_pagination(client):
    import asyncio
    now = datetime.now(timezone.utc)
    for i in range(5):
        uid = f"p{i}"
        await save_job(
            url_id=uid, domain=f"d{i}.com", client_ip="1.1.1.1",
            created_at=now, expires_at=now + timedelta(days=7),
            status="completed", meta={}, results={},
        )
        await set_published(uid, True)
        await asyncio.sleep(0.01)

    r = await client.get("/api/feed?limit=2&offset=2")
    body = r.json()
    assert body["count"] == 2


@pytest.mark.anyio
async def test_feed_caps_limit_at_100(client):
    r = await client.get("/api/feed?limit=500")
    # Just ensure no crash; empty feed is fine
    assert r.status_code == 200


# ---------- export.json / export.csv ----------

@pytest.mark.anyio
async def test_export_json_returns_results(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="expjson", domain="ex.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7), status="completed",
        meta={"snapshots_analyzed": 3},
        results={"emails": [{"value": "a@ex.com", "first_seen": "2020-01", "last_seen": "2021-02", "occurrences": 2}]},
    )
    r = await client.get("/api/s/expjson/export.json")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.json"')
    body = r.json()
    assert body["domain"] == "ex.com"
    assert body["results"]["emails"][0]["value"] == "a@ex.com"


@pytest.mark.anyio
async def test_export_csv_flattens_findings(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="expcsv", domain="ex.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7), status="completed",
        meta={}, results={"emails": [{"value": "a@ex.com", "first_seen": "2020-01", "last_seen": "2021-02", "occurrences": 2}]},
    )
    r = await client.get("/api/s/expcsv/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    text = r.text
    assert "category,value,first_seen,last_seen,occurrences,source" in text
    assert "emails,a@ex.com" in text


@pytest.mark.anyio
async def test_export_json_404_unknown(client):
    r = await client.get("/api/s/nope/export.json")
    assert r.status_code == 404




@pytest.mark.anyio
async def test_local_scans_lists_all_published_and_private():
    # Solo/self-hosted "My scans" must list EVERY scan, not just published ones.
    from db import list_recent_scans, save_job
    now = datetime.now(timezone.utc)
    await save_job(url_id="pubd", domain="a.com", client_ip="1.1.1.1", created_at=now,
                   expires_at=now + timedelta(days=7), status="completed", meta={}, results={})
    await save_job(url_id="priv", domain="b.com", client_ip="1.1.1.1", created_at=now,
                   expires_at=now + timedelta(days=7), status="completed", meta={}, results={})
    from db import set_published
    await set_published("pubd", True)
    scans = await list_recent_scans()
    ids = {s["url_id"] for s in scans}
    assert {"pubd", "priv"} <= ids          # both, published or not


@pytest.mark.anyio
async def test_scan_reuses_existing_recent_scan(client):
    # Guardrail: a completed, non-expired scan for the domain already exists, so
    # POST /api/scan returns it (reused=True) instead of launching a new one.
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="reuse_me", domain="reuse.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={"snapshots_analyzed": 3}, results={"emails": []},
    )
    r = await client.post("/api/scan", json={"domain": "reuse.com"})
    assert r.status_code == 200
    data = r.json()
    assert data["reused"] is True
    assert data["url_id"] == "reuse_me"
    assert data["status"] == "completed"


@pytest.mark.anyio
async def test_scan_force_bypasses_the_guardrail(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="reuse_me2", domain="fresh.com", client_ip="2.2.2.2",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={"emails": []},
    )
    r = await client.post("/api/scan", json={"domain": "fresh.com", "force": True})
    assert r.status_code == 200
    data = r.json()
    assert data.get("reused") in (False, None)
    assert data["url_id"] != "reuse_me2"   # a brand-new scan


@pytest.mark.anyio
async def test_scan_does_not_reuse_a_different_domain(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="other_dom", domain="a.com", client_ip="3.3.3.3",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={"emails": []},
    )
    r = await client.post("/api/scan", json={"domain": "b.com"})
    assert r.status_code == 200
    assert r.json().get("reused") in (False, None)
