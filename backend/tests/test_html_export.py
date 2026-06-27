"""Tests for the standalone HTML export builder."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config import settings
from db import init_db, save_job
from main import app
from services.html_export import build_standalone_html


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_inlines_preload_data_as_valid_json():
    html = build_standalone_html({
        "url_id": "abc",
        "domain": "x.com",
        "status": "completed",
        "results": {"emails": []},
    })
    assert "window.__WAYTRACE_PRELOAD__ = " in html
    # Extract the JSON between the marker and the closing script tag
    marker = "window.__WAYTRACE_PRELOAD__ = "
    start = html.find(marker) + len(marker)
    end = html.find(";</script>", start)
    payload_raw = html[start:end]
    # Replace escaped </ back to validate; this round-trip should yield valid JSON
    payload = json.loads(payload_raw.replace("<\\/", "</"))
    assert payload["domain"] == "x.com"


def test_escapes_script_break_in_domain_field():
    payload_str = "evil</script><script>alert(1)//"
    html = build_standalone_html({
        "url_id": "x",
        "domain": payload_str,
        "status": "completed",
        "results": {},
    })
    # The literal </script><script> must not appear in the inlined block
    marker = "window.__WAYTRACE_PRELOAD__ = "
    start = html.find(marker)
    end = html.find(";</script>", start)
    block = html[start:end]
    assert "</script><script>alert" not in block


def test_inline_appears_before_head_close():
    html = build_standalone_html({"url_id": "a", "domain": "b.com",
                                   "status": "completed", "results": {}})
    if "</head>" in html:
        idx_inline = html.find("__WAYTRACE_PRELOAD__")
        idx_head_close = html.find("</head>")
        assert idx_inline < idx_head_close


@pytest_asyncio.fixture(autouse=True)
async def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    await init_db(db_path)
    yield
    import db as _db
    _db._db_path = None


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_export_endpoint_returns_html_attachment(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="exp1", domain="example.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed",
        meta={"snapshots_analyzed": 5},
        results={"emails": [{"value": "a@b.c"}]},
    )
    r = await client.get("/api/s/exp1/export.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert "waytrace-example.com" in cd
    body = r.text
    assert "__WAYTRACE_PRELOAD__" in body
    assert "example.com" in body


@pytest.mark.anyio
async def test_export_endpoint_404_when_unknown(client):
    r = await client.get("/api/s/nope/export.html")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_export_endpoint_410_when_expired(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="oldexp", domain="x.com", client_ip="1.1.1.1",
        created_at=now - timedelta(days=8),
        expires_at=now - timedelta(hours=1),
        status="completed", meta={}, results={},
    )
    r = await client.get("/api/s/oldexp/export.html")
    assert r.status_code == 410


@pytest.mark.anyio
async def test_export_sanitizes_unsafe_domain_in_filename(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="weird", domain="../../etc/passwd",
        client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    r = await client.get("/api/s/weird/export.html")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # No slashes / colons / quotes in the suggested filename
    assert "/" not in cd.split('"')[1]
    assert "\\" not in cd.split('"')[1]
