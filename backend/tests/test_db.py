# backend/tests/test_db.py
"""Tests for the SQLite database module."""
import asyncio
import os
import tempfile

import pytest
import aiosqlite

from db import init_db, get_db


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_init_db_creates_all_tables(tmp_db_path):
    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]
    # Live schema.
    assert "jobs" in tables
    assert "scan_pages_fts" in tables
    # The v1 collect/analyze tables were removed and must not be recreated.
    for dead in ("domains", "snapshots", "pages", "findings", "crawl_state", "backup_files"):
        assert dead not in tables, f"dead v1 table {dead} still created"


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_db_path):
    await init_db(tmp_db_path)
    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        )
        count = (await cursor.fetchone())[0]
    assert count >= 6


@pytest.mark.asyncio
async def test_maintain_runs_clean(tmp_db_path):
    from db import maintain
    await init_db(tmp_db_path)
    await maintain()   # PRAGMA optimize + WAL checkpoint, must not raise


@pytest.mark.asyncio
async def test_get_db_returns_connection(tmp_db_path):
    await init_db(tmp_db_path)
    db = await get_db(tmp_db_path)
    try:
        cursor = await db.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_init_db_applies_migrations(tmp_db_path):
    from db import MIGRATIONS

    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("PRAGMA user_version")
        version = (await cursor.fetchone())[0]
    expected = max(v for v, _ in MIGRATIONS) if MIGRATIONS else 0
    assert version == expected


@pytest.mark.asyncio
async def test_init_db_idempotent_respects_user_version(tmp_db_path):
    """A second init_db call must not re-run already-applied migrations."""
    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("PRAGMA user_version")
        version_before = (await cursor.fetchone())[0]

    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("PRAGMA user_version")
        version_after = (await cursor.fetchone())[0]

    assert version_before == version_after


@pytest.mark.asyncio
async def test_migration_failure_preserves_last_good_version(tmp_db_path, monkeypatch):
    """If migration v2 fails, user_version must remain at the last successful
    version so the next startup re-attempts only what didn't apply."""
    import db as db_module

    await init_db(tmp_db_path)
    async with aiosqlite.connect(tmp_db_path) as d:
        before = (await (await d.execute("PRAGMA user_version")).fetchone())[0]
    assert before >= 1

    # Inject a broken migration after the baseline
    broken = [*db_module.MIGRATIONS, (before + 1, "NOT VALID SQL")]
    monkeypatch.setattr(db_module, "MIGRATIONS", broken)

    with pytest.raises(aiosqlite.OperationalError):
        await init_db(tmp_db_path)

    async with aiosqlite.connect(tmp_db_path) as d:
        after = (await (await d.execute("PRAGMA user_version")).fetchone())[0]
    assert after == before  # not bumped past the failing migration


@pytest.mark.asyncio
async def test_legacy_migration_survives_already_applied_column(tmp_db_path):
    """Pre-migration DBs had response_headers added by a startup hook with
    user_version still 0. Running init_db must not fail in that case."""
    async with aiosqlite.connect(tmp_db_path) as db:
        # Simulate a pre-migration DB: full schema (incl. the column that
        # migration v1 adds) but user_version pinned to 0.
        from db import SCHEMA_SQL
        await db.executescript(SCHEMA_SQL)
        await db.execute("PRAGMA user_version = 0")
        await db.commit()

    # Must not raise even though ALTER TABLE ... ADD COLUMN would fail.
    await init_db(tmp_db_path)

    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] >= 1
