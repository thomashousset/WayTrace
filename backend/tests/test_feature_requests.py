"""Suggestions: the guardrails, one test each.

The concern that shaped this endpoint was not "does it work" but "can it be
used to spam an inbox, fill a disk, or take the service down". So the tests
are about refusal, and each names the thing it refuses.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import db
from config import settings
from db import init_db
from main import app
from services import ratelimit


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "wt.db"))
    await init_db(str(tmp_path / "wt.db"))
    ratelimit.reset_all()
    yield
    import db as _db
    _db._db_path = None


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


def _as_user(uid=7, email="someone@example.test"):
    from routers.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "email": email}


def _anonymous():
    from routers.auth import get_current_user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_an_anonymous_visitor_cannot_send_one(client):
    _anonymous()
    r = await client.post("/api/feature-request", json={"message": "x" * 40})
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "auth_required_feature"


@pytest.mark.anyio
async def test_a_one_word_suggestion_is_refused(client):
    _as_user()
    try:
        r = await client.post("/api/feature-request", json={"message": "pdf"})
        assert r.status_code == 400
    finally:
        _anonymous()


@pytest.mark.anyio
async def test_nothing_but_text_is_accepted(client):
    """No screenshot, no url, no user agent. Every attachment is a payload
    path, and a suggestion needs none of them."""
    _as_user()
    try:
        r = await client.post("/api/feature-request",
                              json={"message": "y" * 40,
                                    "screenshot": "data:image/png;base64,AAA"})
        assert r.status_code == 422
    finally:
        _anonymous()


@pytest.mark.anyio
async def test_a_long_message_is_cut_rather_than_stored_whole(client):
    _as_user()
    try:
        r = await client.post("/api/feature-request", json={"message": "z" * 9000})
        assert r.status_code == 200
        rows = await db.list_feature_requests()
        assert len(rows[0]["message"]) == 2000
    finally:
        _anonymous()


@pytest.mark.anyio
async def test_the_same_account_is_slowed_down(client):
    _as_user()
    try:
        codes = []
        for i in range(6):
            r = await client.post("/api/feature-request",
                                  json={"message": "suggestion number %d, long enough" % i})
            codes.append(r.status_code)
        assert 429 in codes, codes
        assert codes.count(200) <= 3, codes
    finally:
        _anonymous()


@pytest.mark.anyio
async def test_the_day_has_a_ceiling_so_a_flood_cannot_fill_the_disk(client, monkeypatch):
    monkeypatch.setattr(db, "_FEATURE_MAX_PER_DAY", 1)
    _as_user()
    try:
        assert (await client.post("/api/feature-request",
                                  json={"message": "the first one, long enough"})).status_code == 200
        ratelimit.reset_all()
        r = await client.post("/api/feature-request",
                              json={"message": "the second one, long enough"})
        assert r.status_code == 503
        assert r.json()["detail"]["error"] == "feature_full"
    finally:
        _anonymous()


@pytest.mark.anyio
async def test_no_email_is_sent_for_a_suggestion(client, monkeypatch):
    """Bug reports mail every admin. Suggestions must not, or a burst burns the
    quota the sign-in links depend on."""
    envois = []
    import services.mailer as mailer

    async def _fake(*a, **k):
        envois.append(a)
    monkeypatch.setattr(mailer, "send_email", _fake)
    _as_user()
    try:
        r = await client.post("/api/feature-request", json={"message": "w" * 40})
        assert r.status_code == 200
        assert envois == []
    finally:
        _anonymous()
