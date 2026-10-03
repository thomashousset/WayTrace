import os
import re
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio

import db as dbmod
from services import identity


@pytest_asyncio.fixture(autouse=True)
async def _clean(tmp_path):
    await dbmod.init_db(str(tmp_path / "t.db"))
    identity.reset_state_for_tests()
    yield
    identity.reset_state_for_tests()


@pytest.mark.asyncio
async def test_instance_id_is_stable_and_persisted():
    a = await identity.get_instance_id()
    b = await identity.get_instance_id()
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{6,}", a)


@pytest.mark.asyncio
async def test_instance_id_survives_a_cache_reset(monkeypatch):
    a = await identity.get_instance_id()
    identity.reset_state_for_tests()   # drop the in-memory cache, keep app_state
    b = await identity.get_instance_id()
    assert a == b   # reloaded from app_state, not regenerated


def test_build_user_agent_with_contact():
    ua = identity.build_user_agent("1.8.2", "me@example.com", "abc123")
    assert ua == "WayTrace/1.8.2 (+me@example.com; id:abc123)"


def test_build_user_agent_falls_back_to_project_url():
    ua = identity.build_user_agent("1.8.2", "", "abc123")
    assert "github.com/thomashousset/WayTrace" in ua
    assert "id:abc123" in ua


def test_build_user_agent_strips_crlf_and_control_chars():
    # A contact with CR/LF must never survive into the UA (header safety):
    # control chars are dropped, so the CRLF collapses and cannot inject.
    ua = identity.build_user_agent("1.8.2", "ops@x.io\r\nEvil: 1", "abc123")
    assert "\r" not in ua and "\n" not in ua
    assert "ops@x.ioEvil: 1" in ua   # CRLF stripped, remainder concatenated
    assert ua.startswith("WayTrace/1.8.2 (+ops@x.io")


@pytest.mark.asyncio
async def test_current_user_agent_reflects_contact_and_id(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "operator_contact", "ops@team.io", raising=False)
    identity.reset_ua_cache()
    ua = await identity.current_user_agent()
    assert "ops@team.io" in ua
    assert re.search(r"id:[0-9a-f]{6,}", ua)


@pytest.mark.asyncio
async def test_ua_cache_reset_picks_up_new_contact(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "operator_contact", "", raising=False)
    identity.reset_ua_cache()
    first = await identity.current_user_agent()
    assert "github.com/thomashousset" in first
    monkeypatch.setattr(settings, "operator_contact", "new@x.io", raising=False)
    identity.reset_ua_cache()
    second = await identity.current_user_agent()
    assert "new@x.io" in second


@pytest.mark.asyncio
async def test_a_fresh_instance_id_is_wide_enough_to_stay_unique(monkeypatch):
    """Two instances sharing an id defeats the whole mechanism: archive.org can
    no longer tell a polite instance from an impolite one, which is the exact
    situation the per-instance id exists to avoid. At 3 bytes a thousand
    installs already carry a ~3% chance of a collision somewhere in the fleet.
    """
    identity.reset_state_for_tests()
    seen = set()
    for _ in range(200):
        identity.reset_state_for_tests()
        monkeypatch.setattr(identity, "_instance_id", None, raising=False)
        new = secrets.token_hex(identity._ID_BYTES)
        assert len(new) >= 8, "an id shorter than 8 hex chars collides too easily"
        seen.add(new)
    assert len(seen) == 200, "generated ids must not repeat"


@pytest.mark.asyncio
async def test_an_existing_instance_id_is_never_rotated(monkeypatch):
    """A rotating id reads as evasion. An instance that already introduced
    itself to archive.org with a short id keeps it, whatever _ID_BYTES becomes.
    """
    identity.reset_state_for_tests()
    calls = []

    async def _get(key):
        calls.append(key)
        return "abc123"          # a legacy 3-byte id

    async def _set(key, value):  # pragma: no cover - must never run
        raise AssertionError("an existing instance id was overwritten")

    import db as _db
    monkeypatch.setattr(_db, "get_app_state", _get, raising=False)
    monkeypatch.setattr(_db, "set_app_state", _set, raising=False)
    assert await identity.get_instance_id() == "abc123"
