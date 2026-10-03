import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config import settings
from main import app
from store import store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def reset_store():
    """Wipe the in-memory store between tests so per-IP cap doesn't bleed across them."""
    await store._reset_for_tests()
    yield
    # Cancel stray fire-and-forget run_scan tasks BEFORE the loop closes.
    # Since the restart-proof queue, run_scan writes to SQLite right away;
    # an orphaned task mid-write strands a non-daemon aiosqlite thread when
    # the loop closes, and that hangs interpreter shutdown after the run.
    import asyncio as _asyncio
    stray = [
        t for t in _asyncio.all_tasks()
        if t is not _asyncio.current_task()
        and getattr(t.get_coro(), "__qualname__", "").startswith("run_scan")
    ]
    for t in stray:
        t.cancel()
    if stray:
        await _asyncio.gather(*stray, return_exceptions=True)
    await store._reset_for_tests()


@pytest.fixture(autouse=True)
def relax_caps(monkeypatch):
    """Most API tests aren't about queue limits; let them all succeed."""
    monkeypatch.setattr(settings, "max_active_per_ip", 50)
    monkeypatch.setattr(settings, "max_queue_total", 100)
    monkeypatch.setattr(settings, "max_active_total", 50)
    # These tests pre-date account-required mode; scan anonymously.
    # require_account_to_scan only exists in the full (server) build; the solo
    # build has no account gate, so guard the override.
    if hasattr(settings, "require_account_to_scan"):
        monkeypatch.setattr(settings, "require_account_to_scan", False)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "active_jobs" in data
    assert "uptime_seconds" in data


@pytest.mark.anyio
async def test_health_reports_build_identity(client, monkeypatch):
    """/api/health names the image it is running, so "what is deployed" is a
    question with an answer. Empty from source, the exact commit from a built
    image; either way the fields are always present."""
    from config import settings

    resp = await client.get("/api/health")
    data = resp.json()
    assert "commit" in data and "built_at" in data
    assert data["commit"] == ""   # tests run from source, not from an image

    monkeypatch.setattr(settings, "waytrace_commit", "deadbee")
    monkeypatch.setattr(settings, "waytrace_built_at", "2026-09-06T00:00:00Z")
    data = (await client.get("/api/health")).json()
    assert data["commit"] == "deadbee"
    assert data["built_at"] == "2026-09-06T00:00:00Z"


@pytest.mark.anyio
async def test_stats(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_scans_run" in data
    assert "active_jobs" in data


@pytest.mark.anyio
async def test_scan_returns_url_id_and_position(client):
    resp = await client.post("/api/scan", json={"domain": "example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert "url_id" in data
    assert data["url"] == f"/s/{data['url_id']}"
    assert data["status"] == "queued"
    assert data["position"] == 0  # first job goes to active


@pytest.mark.anyio
async def test_scan_invalid_domain(client):
    resp = await client.post("/api/scan", json={"domain": "not_a_domain"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_scan_accepts_pasted_url(client):
    # People paste full URLs from the address bar; the host must be extracted
    # instead of rejecting the submission.
    resp = await client.post(
        "/api/scan", json={"domain": "https://www.pasted-url-test.com/some/page?q=1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_normalize_domain_url_forms():
    from models import _normalize_domain
    assert _normalize_domain("https://example.com") == "example.com"
    assert _normalize_domain("http://www.example.com/") == "example.com"
    assert _normalize_domain("https://sub.example.com/path/page.html#frag") == "sub.example.com"
    assert _normalize_domain("example.com:8080") == "example.com"
    assert _normalize_domain("user:pass@example.com") == "example.com"
    assert _normalize_domain("EXAMPLE.com.") == "example.com"


def test_normalize_domain_still_rejects_garbage():
    from models import _normalize_domain
    for bad in ["not_a_domain", "https://192.168.1.1/admin", "http://",
                "just words", "example", ""]:
        with pytest.raises(ValueError):
            _normalize_domain(bad)


@pytest.mark.anyio
async def test_scan_rejects_foreign_snapshot_url(client):
    # A selected snapshot on a different host must be rejected (no fetching an
    # unrelated domain through Wayback).
    resp = await client.post("/api/scan", json={
        "domain": "example.com",
        "selected_snapshots": [{"timestamp": "20200101000000", "url": "http://evil.com/x"}],
    })
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_scan_accepts_own_domain_and_subdomain_snapshots(client):
    resp = await client.post("/api/scan", json={
        "domain": "example.com",
        "selected_snapshots": [
            {"timestamp": "20200101000000", "url": "http://example.com/a"},
            {"timestamp": "20200101000000", "url": "http://blog.example.com/b"},
        ],
    })
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_scan_refused_with_503_when_breaker_open(client, monkeypatch):
    # When archive.org has the server IP blocked/throttled (breaker open), a new
    # scan is refused fast - queued nothing, sent no request - with a clear 503
    # so we never add load while being rate-limited.
    monkeypatch.setattr("routers.scan.archive_health.is_open", lambda: True)
    monkeypatch.setattr(
        "routers.scan.archive_health.status",
        lambda: {"state": "paused", "message": "archive.org is blocking us",
                 "cooldown_remaining": 1800},
    )
    monkeypatch.setattr("routers.scan.archive_health.seconds_remaining", lambda: 1800)
    r = await client.post("/api/scan", json={"domain": "example.com"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "archive_paused"


@pytest.mark.anyio
async def test_scan_rejects_ip(client):
    resp = await client.post("/api/scan", json={"domain": "192.168.1.1"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_job_not_found(client):
    resp = await client.get("/api/jobs/nonexistent-uuid")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_preflight_empty_archive_returns_200(client, monkeypatch):
    """A domain with no Wayback archive must yield a clean empty preflight,
    not a 500. Regression: _compute_cap(0, 0) returns 0 and ScanConfig
    rejected cap=0, so the advanced 'pick subdomains' flow surfaced an
    'Internal Server Error' instead of 'no archive for this domain'."""
    async def _empty_cdx(*args, **kwargs):
        return {"snapshots": [], "total_found": 0}

    monkeypatch.setattr("routers.scan.fetch_cdx_snapshots", _empty_cdx)
    monkeypatch.setattr("routers.scan.archive_health.is_open", lambda: False)

    resp = await client.post(
        "/api/scan/preflight", json={"domain": "no-archive-example.com"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_snapshots"] == 0
    assert data["html_snapshots"] == 0
    assert data["unique_paths"] == 0
    assert data["subdomain_groups"] == []
    assert data["path_groups"] == []


def test_hosted_ceiling_clamps_and_defaults_cap(monkeypatch):
    """On the hosted service the per-scan cap is bounded by the ceiling, and
    a scan with no cap is defaulted to it (max-by-default, but bounded)."""
    from routers.scan import _apply_hosted_ceiling
    from models import ScanConfig
    monkeypatch.setattr(settings, "hosted_snapshot_ceiling", 100)
    cfg, _ = _apply_hosted_ceiling(ScanConfig(cap=999999), None)
    assert cfg.cap == 100                      # clamped down
    cfg2, _ = _apply_hosted_ceiling(None, None)
    assert cfg2.cap == 100                     # defaulted to ceiling
    cfg3, _ = _apply_hosted_ceiling(ScanConfig(cap=40), None)
    assert cfg3.cap == 40                      # already under ceiling, untouched


def test_hosted_ceiling_trims_selected_snapshots_representatively(monkeypatch):
    from routers.scan import _apply_hosted_ceiling
    monkeypatch.setattr(settings, "hosted_snapshot_ceiling", 50)
    sel = [{"timestamp": "20130601120000", "url": f"https://e.com/o{i}"} for i in range(10)]
    sel += [{"timestamp": "20240601120000", "url": f"https://e.com/n{i}"} for i in range(400)]
    _, out = _apply_hosted_ceiling(None, sel)
    assert len(out) <= 50
    assert any(s["timestamp"].startswith("2013") for s in out)  # rare year kept


def test_hosted_ceiling_disabled_is_unlimited(monkeypatch):
    """ceiling=0 is the self-hosted/local mode: no cap forced, full scan."""
    from routers.scan import _apply_hosted_ceiling
    monkeypatch.setattr(settings, "hosted_snapshot_ceiling", 0)
    cfg, _ = _apply_hosted_ceiling(None, None)
    assert cfg is None


@pytest.mark.anyio
async def test_second_scan_same_domain_attaches_to_live_one(client):
    """Launch hardening: a domain already in flight is not scanned twice; the
    second submission attaches to the live scan (force=True bypasses)."""
    r1 = await client.post("/api/scan", json={"domain": "dup.com"})
    r2 = await client.post("/api/scan", json={"domain": "dup.com"})
    assert r2.json()["url_id"] == r1.json()["url_id"]
    assert r2.json()["reused"] is True
    assert r2.json()["live"] is True
    r3 = await client.post("/api/scan", json={"domain": "dup.com", "force": True})
    assert r3.json()["url_id"] != r1.json()["url_id"]


@pytest.mark.anyio
async def test_scan_invalid_categories(client):
    resp = await client.post("/api/scan", json={
        "domain": "example.com",
        "config": {"categories": ["emails", "not_a_category"]},
    })
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_scan_valid_categories(client):
    resp = await client.post("/api/scan", json={
        "domain": "cat-test.com",
        "config": {"categories": ["emails", "phones"]},
    })
    assert resp.status_code == 200
    assert "url_id" in resp.json()


@pytest.mark.anyio
async def test_scan_with_selected_snapshots(client):
    resp = await client.post("/api/scan", json={
        "domain": "snap-test.com",
        "selected_snapshots": [
            {"timestamp": "20220601120000", "url": "https://snap-test.com/"},
        ],
    })
    assert resp.status_code == 200
    assert "url_id" in resp.json()


@pytest.mark.anyio
async def test_scan_smart_dedup_flag(client):
    resp = await client.post("/api/scan", json={
        "domain": "dedup-flag-test.com",
        "config": {"smart_dedup": False},
    })
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_scan_smart_dedup_default(client):
    resp = await client.post("/api/scan", json={
        "domain": "dedup-default-test.com",
        "config": {},
    })
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_per_ip_limit_triggers_429(client, monkeypatch):
    monkeypatch.setattr(settings, "max_active_per_ip", 2)
    await client.post("/api/scan", json={"domain": "a.com"})
    await client.post("/api/scan", json={"domain": "b.com"})
    r = await client.post("/api/scan", json={"domain": "c.com"})
    assert r.status_code == 429
    assert r.json()["detail"]["error"] == "per_ip_limit"


@pytest.mark.anyio
async def test_queue_full_triggers_503(client, monkeypatch):
    monkeypatch.setattr(settings, "max_active_per_ip", 100)
    monkeypatch.setattr(settings, "max_queue_total", 2)
    monkeypatch.setattr(settings, "max_active_total", 1)
    await client.post("/api/scan", json={"domain": "a.com"})
    await client.post("/api/scan", json={"domain": "b.com"})
    r = await client.post("/api/scan", json={"domain": "c.com"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "service_full"
    assert "retry-after" in {k.lower() for k in r.headers}



@pytest.mark.anyio
async def test_persist_and_finish_pins_example_scan(tmp_path, monkeypatch):
    """A completed scan of the example domain persists with a far-future
    expiry so the homepage demo report never falls out of retention."""
    from datetime import datetime, timedelta, timezone
    from db import init_db, get_job_by_url_id
    from routers.scan import _persist_and_finish

    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    monkeypatch.setattr(settings, "example_scan_domain", "demo-pin.example")
    await init_db(db_path)

    await store._reset_for_tests()
    res = await store.create_job("demo-pin.example", client_ip="1.2.3.4")
    await store.update_job(res["job_id"], status="completed", progress=100)
    await _persist_and_finish(res["job_id"], start=0.0)

    persisted = await get_job_by_url_id(res["url_id"])
    assert persisted is not None
    expires = datetime.strptime(
        persisted["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    assert expires > datetime.now(timezone.utc) + timedelta(days=3650)


@pytest.mark.anyio
async def test_persist_and_finish_does_not_pin_failed_example_scan(tmp_path, monkeypatch):
    """A FAILED scan of the example domain keeps the normal retention window:
    only a completed report is worth pinning forever."""
    from datetime import datetime, timedelta, timezone
    from db import init_db, get_job_by_url_id
    from routers.scan import _persist_and_finish

    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    monkeypatch.setattr(settings, "example_scan_domain", "demo-pin.example")
    await init_db(db_path)

    await store._reset_for_tests()
    res = await store.create_job("demo-pin.example", client_ip="1.2.3.4")
    await store.update_job(res["job_id"], status="failed", step="Boom")
    await _persist_and_finish(res["job_id"], start=0.0)

    persisted = await get_job_by_url_id(res["url_id"])
    assert persisted is not None
    expires = datetime.strptime(
        persisted["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    assert expires < datetime.now(timezone.utc) + timedelta(
        days=settings.scan_retention_days + 1
    )


@pytest.mark.anyio
async def test_persist_and_finish_does_not_pin_other_domains(tmp_path, monkeypatch):
    """Completed scans of any other domain keep the normal retention window."""
    from datetime import datetime, timedelta, timezone
    from db import init_db, get_job_by_url_id
    from routers.scan import _persist_and_finish

    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    monkeypatch.setattr(settings, "example_scan_domain", "demo-pin.example")
    await init_db(db_path)

    await store._reset_for_tests()
    res = await store.create_job("ordinary.example", client_ip="1.2.3.4")
    await store.update_job(res["job_id"], status="completed", progress=100)
    await _persist_and_finish(res["job_id"], start=0.0)

    persisted = await get_job_by_url_id(res["url_id"])
    assert persisted is not None
    expires = datetime.strptime(
        persisted["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    assert expires < datetime.now(timezone.utc) + timedelta(
        days=settings.scan_retention_days + 1
    )


def test_preflight_response_structure():
    """PreflightResponse has expected fields."""
    from models import DateRange, PreflightResponse, ScanConfig

    resp = PreflightResponse(
        domain="example.com",
        total_snapshots=100,
        html_snapshots=80,
        unique_paths=10,
        unique_content=70,
        date_range=DateRange(first="2020-01", last="2024-12"),
        suggested_config=ScanConfig(cap=200),
    )
    assert resp.domain == "example.com"
    assert resp.total_snapshots == 100
    assert resp.html_snapshots == 80
    assert resp.path_groups == []
