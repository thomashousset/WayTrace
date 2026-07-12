# backend/db.py
"""SQLite database module ; schema creation and connection helpers."""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone

import aiosqlite
from loguru import logger

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id),
    url TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    digest TEXT,
    mimetype TEXT,
    status_code TEXT,
    selected INTEGER DEFAULT 0,
    source TEXT DEFAULT 'archive',
    source_url TEXT,
    warc_filename TEXT,
    warc_offset INTEGER,
    warc_length INTEGER,
    UNIQUE(domain_id, url, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_domain ON snapshots(domain_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_digest ON snapshots(digest);
CREATE INDEX IF NOT EXISTS idx_snapshots_selected ON snapshots(domain_id, selected);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER UNIQUE NOT NULL REFERENCES snapshots(id),
    html BLOB,
    status TEXT DEFAULT 'pending',
    scraped_at TEXT,
    error TEXT,
    response_headers TEXT
);
CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);

CREATE TABLE IF NOT EXISTS backup_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id),
    url TEXT NOT NULL,
    extension TEXT NOT NULL,
    timestamp TEXT,
    digest TEXT,
    UNIQUE(domain_id, url, timestamp)
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id),
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    metadata TEXT,
    first_seen TEXT,
    last_seen TEXT,
    occurrences INTEGER DEFAULT 1,
    severity TEXT,
    source_page_id INTEGER REFERENCES pages(id),
    UNIQUE(domain_id, category, value)
);
CREATE INDEX IF NOT EXISTS idx_findings_domain_cat ON findings(domain_id, category);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);

CREATE TABLE IF NOT EXISTS crawl_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER UNIQUE NOT NULL REFERENCES domains(id),
    phase TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    progress REAL DEFAULT 0,
    total_snapshots INTEGER DEFAULT 0,
    snapshots_indexed INTEGER DEFAULT 0,
    pages_downloaded INTEGER DEFAULT 0,
    pages_failed INTEGER DEFAULT 0,
    current_delay REAL DEFAULT 1.0,
    cdx_resume_key TEXT,
    started_at TEXT,
    updated_at TEXT,
    completed_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    url_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    client_ip TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    completed_at TEXT,
    is_published INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    meta TEXT,
    results TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_published ON jobs(is_published, published_at);
CREATE INDEX IF NOT EXISTS idx_jobs_expires ON jobs(expires_at);
CREATE INDEX IF NOT EXISTS idx_jobs_client_ip ON jobs(client_ip);

-- Full-text index over the visible text of a scan's pages, so users can search
-- the archived CONTENT (not only the extracted pivots). Standalone FTS5 table:
-- it stores the text so snippet() can return highlighted excerpts. Keyed by the
-- scan's url_id (UNINDEXED, filtered alongside MATCH); rows are removed with the
-- scan (delete_job / delete_expired_jobs). remove_diacritics folds accents so
-- "societe" matches "société".
CREATE VIRTUAL TABLE IF NOT EXISTS scan_pages_fts USING fts5(
    text,
    url_id UNINDEXED,
    timestamp UNINDEXED,
    url UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

"""


# Ordered list of schema migrations. Each entry is (version, sql). The
# version is stored in SQLite's PRAGMA user_version so re-running init_db
# on an already-migrated database is a no-op. Migrations that may collide
# with a column already added out-of-band swallow OperationalError and
# still stamp the version.
MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE pages ADD COLUMN response_headers TEXT"),
    # Auto-depth + truncation transparency: each crawl_state row carries
    # a coverage record so the UI can surface "sampled X of estimated Y"
    # and offer a thorough rescan when the automatic depth pick truncated.
    (2, """
        ALTER TABLE crawl_state ADD COLUMN auto_depth TEXT;
        ALTER TABLE crawl_state ADD COLUMN total_estimate INTEGER;
        ALTER TABLE crawl_state ADD COLUMN sampled_snapshots INTEGER;
        ALTER TABLE crawl_state ADD COLUMN truncated INTEGER DEFAULT 0;
        ALTER TABLE crawl_state ADD COLUMN truncation_reason TEXT;
    """),
    # v3 accounts: scans owned by a user (nullable for legacy/anonymous rows).
    (3, """
        ALTER TABLE jobs ADD COLUMN user_id INTEGER;
        CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
    """),
]

# Migrations whose failure with OperationalError means "already applied
# by the previous ad-hoc hook" rather than a real error.
_LEGACY_ADHOC_MIGRATIONS: set[int] = {1}

_db_path: str | None = None


async def _get_user_version(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def _set_user_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute(f"PRAGMA user_version = {int(version)}")


async def _apply_migrations(db: aiosqlite.Connection) -> None:
    current = await _get_user_version(db)
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        try:
            await db.executescript(sql)
            logger.info("Applied schema migration v{}", version)
        except aiosqlite.OperationalError as exc:
            if version in _LEGACY_ADHOC_MIGRATIONS:
                logger.debug(
                    "Migration v{} raised OperationalError ({}); "
                    "treating as already-applied by legacy startup hook",
                    version, exc,
                )
            else:
                raise
        await _set_user_version(db, version)


async def init_db(db_path: str) -> None:
    """Create all tables if they don't exist, then apply pending migrations."""
    global _db_path
    _db_path = db_path
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await _apply_migrations(db)
        await db.commit()
    logger.info("Database initialized at {}", db_path)


async def get_db(db_path: str | None = None) -> aiosqlite.Connection:
    """Get a new database connection. Caller must close it."""
    path = db_path or _db_path
    if path is None:
        raise RuntimeError("Database not initialized ; call init_db first")
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    # Wait for a lock instead of failing instantly: with several scans persisting
    # concurrently plus the hourly cleanup, a bare connection would raise
    # "database is locked". NORMAL sync is safe under WAL and cuts fsyncs.
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA synchronous=NORMAL")
    return db


async def maintain() -> None:
    """Periodic SQLite hygiene: let the planner refresh its stats and truncate
    the WAL so it doesn't grow without bound. Cheap; safe to call on a schedule.
    """
    db = await get_db()
    try:
        await db.execute("PRAGMA optimize")
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await db.commit()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# v2 jobs persistence (7-day retention, public lookup by url_id)
# ---------------------------------------------------------------------------

def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_job(row) -> dict:
    return {
        "url_id": row["url_id"],
        "domain": row["domain"],
        "client_ip": row["client_ip"],
        "status": row["status"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "completed_at": row["completed_at"],
        "is_published": row["is_published"],
        "published_at": row["published_at"],
        "meta": _json.loads(row["meta"]) if row["meta"] else None,
        "results": _json.loads(row["results"]) if row["results"] else None,
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
    }


async def save_job(
    *,
    url_id: str,
    domain: str,
    client_ip: str | None,
    created_at: datetime,
    expires_at: datetime,
    status: str,
    meta: dict | None,
    results: dict | None,
    completed_at: datetime | None = None,
    user_id: int | None = None,
) -> None:
    """Insert or update a job row, preserving is_published/published_at on update."""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO jobs
                 (url_id, domain, client_ip, status, created_at, expires_at,
                  completed_at, is_published, published_at, meta, results, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)
               ON CONFLICT(url_id) DO UPDATE SET
                 domain = excluded.domain,
                 client_ip = excluded.client_ip,
                 status = excluded.status,
                 created_at = excluded.created_at,
                 expires_at = excluded.expires_at,
                 completed_at = excluded.completed_at,
                 meta = excluded.meta,
                 results = excluded.results,
                 user_id = COALESCE(excluded.user_id, jobs.user_id)
               """,
            (
                url_id, domain, client_ip, status,
                _iso(created_at), _iso(expires_at), _iso(completed_at),
                _json.dumps(meta) if meta is not None else None,
                _json.dumps(results) if results is not None else None,
                user_id,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def get_job_by_url_id(url_id: str) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM jobs WHERE url_id = ?", (url_id,))
        row = await cur.fetchone()
        return _row_to_job(row) if row else None
    finally:
        await db.close()


async def set_published(url_id: str, published: bool) -> bool:
    """Toggle the is_published flag. Returns True if a row was updated."""
    db = await get_db()
    try:
        if published:
            cur = await db.execute(
                "UPDATE jobs SET is_published = 1, published_at = ? WHERE url_id = ?",
                (_iso(datetime.now(timezone.utc)), url_id),
            )
        else:
            cur = await db.execute(
                "UPDATE jobs SET is_published = 0, published_at = NULL WHERE url_id = ?",
                (url_id,),
            )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def delete_job(url_id: str) -> bool:
    """Hard-delete a single job row by url_id. Returns True if a row was removed.

    The jobs table is self-contained (findings live in the row's JSON
    ``results`` column), so a single DELETE fully removes the scan; the public
    feed excludes it immediately since list_feed only returns existing rows.
    Also drops the scan's full-text page index.
    """
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM jobs WHERE url_id = ?", (url_id,))
        await db.execute("DELETE FROM scan_pages_fts WHERE url_id = ?", (url_id,))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


# Cap the text stored per page and the number of pages indexed per scan so the
# full-text index stays bounded (visible text, not raw HTML). ~40 KB x 400 pages
# is a few MB per scan, purged on the 7-day retention.
_FTS_MAX_CHARS_PER_PAGE = 40_000
_FTS_MAX_PAGES = 600


async def index_scan_pages(url_id: str, rows: list[tuple[str, str, str]]) -> int:
    """Index (timestamp, url, text) rows for a scan into the full-text table.

    Best-effort: callers wrap it so a failure never breaks the scan. Replaces any
    existing rows for this url_id (idempotent on re-scan of the same url_id).
    """
    if not rows:
        return 0
    db = await get_db()
    try:
        await db.execute("DELETE FROM scan_pages_fts WHERE url_id = ?", (url_id,))
        count = 0
        for ts, url, text in rows[:_FTS_MAX_PAGES]:
            if not text:
                continue
            await db.execute(
                "INSERT INTO scan_pages_fts (text, url_id, timestamp, url) VALUES (?, ?, ?, ?)",
                (text[:_FTS_MAX_CHARS_PER_PAGE], url_id, ts or "", url or ""),
            )
            count += 1
        await db.commit()
        return count
    finally:
        await db.close()


async def search_scan_pages(url_id: str, query: str, limit: int = 50) -> list[dict]:
    """Full-text search within a scan's pages. Returns url/timestamp + a
    highlighted snippet, ranked by relevance (bm25)."""
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(limit, 200))
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT url, timestamp,
                      snippet(scan_pages_fts, 0, '<mark>', '</mark>', ' … ', 12) AS snippet
                 FROM scan_pages_fts
                WHERE url_id = ? AND scan_pages_fts MATCH ?
                ORDER BY bm25(scan_pages_fts)
                LIMIT ?""",
            (url_id, q, limit),
        )
        rows = await cur.fetchall()
        return [
            {"url": r["url"], "timestamp": r["timestamp"], "snippet": r["snippet"]}
            for r in rows
        ]
    finally:
        await db.close()


async def list_feed(limit: int = 20, offset: int = 0) -> list[dict]:
    """Return published, non-expired jobs sorted by published_at DESC."""
    db = await get_db()
    try:
        now = _iso(datetime.now(timezone.utc))
        cur = await db.execute(
            """SELECT url_id, domain, completed_at, published_at, expires_at, meta, results
               FROM jobs
               WHERE is_published = 1 AND expires_at > ?
               ORDER BY published_at DESC
               LIMIT ? OFFSET ?""",
            (now, limit, offset),
        )
        rows = await cur.fetchall()
        items = []
        for row in rows:
            meta = _json.loads(row["meta"]) if row["meta"] else {}
            results = _json.loads(row["results"]) if row["results"] else {}
            top = sorted(
                (
                    (cat, len(v))
                    for cat, v in results.items()
                    if isinstance(v, list) and v and cat != "highlights"
                ),
                key=lambda x: -x[1],
            )[:3]
            items.append({
                "url_id": row["url_id"],
                "domain": row["domain"],
                "completed_at": row["completed_at"],
                "published_at": row["published_at"],
                "expires_at": row["expires_at"],
                "summary": {
                    "date_first_seen": meta.get("date_first_seen"),
                    "snapshots_analyzed": meta.get("snapshots_analyzed"),
                    "top_categories": [{"name": n, "count": c} for n, c in top],
                },
            })
        return items
    finally:
        await db.close()


async def expire_job_now(url_id: str) -> bool:
    """Force-expire a single job by setting expires_at to now()."""
    db = await get_db()
    try:
        now = _iso(datetime.now(timezone.utc))
        cur = await db.execute(
            "UPDATE jobs SET expires_at = ? WHERE url_id = ?",
            (now, url_id),
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def delete_expired_jobs() -> int:
    """Hard-delete jobs whose expires_at has been reached. Returns count deleted."""
    db = await get_db()
    try:
        now = _iso(datetime.now(timezone.utc))
        cur = await db.execute("DELETE FROM jobs WHERE expires_at <= ?", (now,))
        # Prune the full-text index of any scan that no longer exists (expired
        # or deleted), so it follows the same 7-day retention as the scans.
        await db.execute(
            "DELETE FROM scan_pages_fts WHERE url_id NOT IN (SELECT url_id FROM jobs)"
        )
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


