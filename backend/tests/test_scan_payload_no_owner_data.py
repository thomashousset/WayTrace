"""Holding the link must not reveal who ran the scan, or from where.

/api/s/{url_id} answers anyone with the link, and the HTML export is a file
people hand over. Both were serialising the whole jobs row. The API stripped
user_id by name and kept client_ip beside it; the export stripped nothing at
all, so a downloaded report carried the scanner's IP address and account id
inside a script tag, invisibly, wherever the file went next.

An allowlist, so a column added later cannot leak by default.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config import settings
from db import init_db, save_job, get_job_by_url_id
from main import app
from services.html_export import build_standalone_html

_SECRET = ("client_ip", "user_id", "notify_email", "job_id",
           "config_json", "selected_snapshots_json")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    await init_db(db_path)
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="ownerdata1", domain="x.com", client_ip="203.0.113.77",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={"snapshots_analyzed": 3},
        results={"emails": [{"value": "a@b.c", "occurrences": 1}]},
    )
    yield
    import db as _db
    _db._db_path = None


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_the_api_payload_carries_no_owner_data(client):
    r = await client.get("/api/s/ownerdata1")
    assert r.status_code == 200
    body = r.json()
    leaked = [k for k in _SECRET if k in body]
    assert leaked == [], f"the share link reveals {leaked}"
    assert "203.0.113.77" not in r.text


@pytest.mark.anyio
async def test_the_api_still_returns_what_the_report_needs(client):
    body = (await client.get("/api/s/ownerdata1")).json()
    for k in ("url_id", "domain", "status", "created_at", "expires_at",
              "completed_at", "meta", "results"):
        assert k in body, f"the report reads {k}"
    assert body["results"]["emails"][0]["value"] == "a@b.c"


@pytest.mark.anyio
async def test_the_downloaded_file_carries_no_owner_data(client):
    r = await client.get("/api/s/ownerdata1/export.html")
    assert r.status_code == 200
    assert "203.0.113.77" not in r.text
    m = re.search(r"window\.__WAYTRACE_PRELOAD__ = (\{.*?\});</script>", r.text, re.S)
    assert m, "no preload in the export"
    payload = json.loads(m.group(1).replace("<\\/", "</"))
    leaked = [k for k in _SECRET if k in payload]
    assert leaked == [], f"the handed-over file carries {leaked}"
    assert payload["domain"] == "x.com"
    assert payload["results"]["emails"][0]["value"] == "a@b.c"


@pytest.mark.anyio
async def test_the_json_export_carries_no_owner_data(client):
    r = await client.get("/api/s/ownerdata1/export.json")
    assert "203.0.113.77" not in r.text
    assert not any(k in r.json() for k in _SECRET)


@pytest.mark.anyio
async def test_an_unreadable_expiry_is_treated_as_expired(client):
    """The check parsed one timestamp format and answered "not expired" to
    anything else. Retention is a promise; a date it cannot read must not
    quietly extend it."""
    from routers.public import _is_expired
    assert _is_expired("2020-01-01T00:00:00Z") is True
    assert _is_expired("2020-01-01T00:00:00+00:00") is True     # offset form
    assert _is_expired("2020-01-01T00:00:00.123456+00:00") is True
    assert _is_expired("not a date at all") is True             # fail closed
    assert _is_expired(None) is False                           # never set
    assert _is_expired("2099-01-01T00:00:00Z") is False
