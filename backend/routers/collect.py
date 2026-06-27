"""Collection API routes ; start/pause/resume/status for DB-backed crawls."""
from __future__ import annotations

import asyncio

import aiohttp
import aiosqlite
from fastapi import APIRouter, HTTPException
from loguru import logger

from config import settings
from db import get_db
from models import CollectRequest, CollectResponse, CollectStatus
from services.cdx import cdx_size_probe
from services.collector import crawl_cdx, download_pages, discover_backup_files
from services.filters import auto_depth, select_snapshots_in_db
from services.rate_limiter import RateLimiter

# Avoid an import cycle between collect and analyze routers. both share
# the per-domain analysis lock so concurrent /api/analyze and
# auto-analyze-after-collect calls don't race on findings DELETE/INSERT.
from routers.analyze import _get_analysis_lock as _analyze_lock_for
from routers.analyze import run_analysis as _run_analysis

router = APIRouter(prefix="/api", tags=["collect"])

# domain_id -> running asyncio.Task. Access is guarded by _active_tasks_lock
# so two concurrent POST /api/collect calls for the same domain can't both
# pass the existence check and spawn parallel crawlers.
_active_tasks: dict[int, asyncio.Task] = {}
_active_tasks_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Background pipeline
# ---------------------------------------------------------------------------

async def _run_collection(domain_id: int, domain: str, db_path: str, config=None) -> None:
    """Full collection pipeline: CDX index -> HTML download -> backup discovery."""
    rl = RateLimiter(
        initial_delay=settings.rate_limit_initial_delay,
        min_delay=settings.rate_limit_min_delay,
        max_delay=settings.rate_limit_max_delay,
        speedup_factor=settings.rate_limit_speedup_factor,
        speedup_streak=settings.rate_limit_speedup_streak,
        backoff_factor=settings.rate_limit_backoff_factor,
        pause_429=settings.rate_limit_429_pause,
    )

    async def _update_state(**kwargs):
        db = await get_db(db_path)
        try:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [domain_id]
            await db.execute(
                f"UPDATE crawl_state SET {sets}, updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
                f"WHERE domain_id = ?",
                vals,
            )
            await db.commit()
        finally:
            await db.close()

    # Ensure crawl_state row exists
    db = await get_db(db_path)
    try:
        await db.execute(
            """INSERT OR IGNORE INTO crawl_state
               (domain_id, phase, status, progress, started_at, updated_at)
               VALUES (?, 'cdx', 'running', 0,
                       strftime('%Y-%m-%dT%H:%M:%S','now'),
                       strftime('%Y-%m-%dT%H:%M:%S','now'))""",
            (domain_id,),
        )
        await db.execute(
            "UPDATE crawl_state SET status = 'running', phase = 'cdx', progress = 0, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE domain_id = ?",
            (domain_id,),
        )
        await db.commit()
    finally:
        await db.close()

    try:
        from services.collector import USER_AGENT

        # Archive session: rate-limited, with User-Agent
        archive_timeout = aiohttp.ClientTimeout(total=settings.archive_request_timeout)
        archive_headers = {"User-Agent": USER_AGENT}

        # Probe the CDX to learn the domain's rough size and let
        # auto_depth() pick a depth and cap. ``config.depth`` is now
        # only honoured as a force_thorough flag (``"max"`` lifts the
        # auto pick one tier); the UI itself no longer exposes a depth
        # selector.
        force_thorough = bool(config and getattr(config, "depth", None) == "max")
        date_from = (config.date_from if config else None)
        date_to = (config.date_to if config else None)
        smart_dedup = (config.smart_dedup if config else True)

        probe = await cdx_size_probe(domain)
        estimated_records = probe.get("estimated_records", 0) if probe.get("ok") else 0
        depth, _picked_cap = auto_depth(
            estimated_records, force_thorough=force_thorough,
        )
        logger.info(
            "Auto-depth picked '{}' for {} (est. {} CDX records, force_thorough={})",
            depth, domain, estimated_records, force_thorough,
        )
        await _update_state(
            auto_depth=depth,
            total_estimate=estimated_records,
        )

        async with aiohttp.ClientSession(timeout=archive_timeout, headers=archive_headers) as session:
            # Phase 1: Archive.org CDX (progress 5-40%)
            await _update_state(phase="cdx", status="running", progress=5)
            cdx_result = await crawl_cdx(
                session, domain, db_path, rate_limiter=rl, depth=depth,
            )
            total_snaps = cdx_result.get("snapshots_indexed", 0)
            stop_reason = cdx_result.get("stop_reason", "exhausted")
            # Truncation = the crawl stopped because of a budget cap, not
            # because archive.org ran out of pages. We still surface the
            # estimate when the probe failed (estimated_records=0) so old
            # rows degrade gracefully.
            truncated_flag = 1 if stop_reason.startswith(("deadline", "snapshot_cap")) else 0
            await _update_state(
                phase="cdx", status="running", progress=40,
                total_snapshots=total_snaps, snapshots_indexed=total_snaps,
                sampled_snapshots=total_snaps,
                truncated=truncated_flag,
                truncation_reason=stop_reason if truncated_flag else None,
            )
            # Archive.org CDX was empty or rate-limited us: fail loudly rather
            # than silently transitioning to 'done' with zero findings. The
            # frontend shows the error message so users know to retry later.
            if total_snaps == 0:
                raise RuntimeError(
                    "No snapshots returned by archive.org for this domain. "
                    "The CDX endpoint may be temporarily unavailable or the "
                    "domain is not archived. Try again in a minute."
                )

            # Phase 2: Archive.org download (progress 40-85%)
            await _update_state(phase="download", status="running", progress=40)
            await select_snapshots_in_db(
                domain_id, db_path,
                depth=depth,
                date_from=date_from,
                date_to=date_to,
                smart_dedup=smart_dedup,
            )

            async def _on_dl_progress(downloaded, failed, total):
                pct = 40 + int(45 * downloaded / max(total, 1))
                await _update_state(
                    pages_downloaded=downloaded,
                    pages_failed=failed, progress=pct,
                )

            dl_result = await download_pages(
                session, domain_id, db_path, rate_limiter=rl,
                on_progress=_on_dl_progress,
            )
            archive_pages = dl_result.get("pages_downloaded", 0)
            archive_failed = dl_result.get("pages_failed", 0)
            await _update_state(
                phase="download", status="running", progress=85,
                pages_downloaded=archive_pages, pages_failed=archive_failed,
            )

            # Phase 5: Backup discovery + done (progress 85-100%)
            await _update_state(phase="backup_scan", status="running", progress=90)
            await discover_backup_files(domain_id, db_path)

        # Phase 6: auto-analyze. Skipped when this run downloaded 0 new
        # pages AND there are already findings for the domain. Without
        # this guard a scan-refresh that brings no new content still
        # spends several minutes re-analyzing identical pages.
        skip_analyze = False
        if archive_pages == 0:
            db = await get_db(db_path)
            try:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM findings WHERE domain_id = ?",
                    (domain_id,),
                )
                row = await cursor.fetchone()
                existing = row[0] if row else 0
                if existing > 0:
                    skip_analyze = True
                    logger.info(
                        "Skipping auto-analyze for domain_id={}: {} existing "
                        "findings + 0 new pages downloaded",
                        domain_id, existing,
                    )
            finally:
                await db.close()

        if not skip_analyze:
            await _update_state(phase="analyze", status="running", progress=92)
            try:
                lock = await _analyze_lock_for(domain_id)
                async with lock:
                    results = await _run_analysis(domain_id, db_path)
                total = sum(len(v) for v in results.values()) if isinstance(results, dict) else 0
                logger.info(
                    "Auto-analyze complete for domain_id={}: {} findings",
                    domain_id, total,
                )
            except Exception as exc:
                # Don't fail the whole pipeline. collection succeeded and
                # the user can retry analyze via POST /api/analyze/{id}.
                logger.warning(
                    "Auto-analyze failed for domain_id={}, leaving for manual retry: {!r}",
                    domain_id, exc,
                )

        await _update_state(phase="done", status="done", progress=100)
        logger.info("Collection complete for domain_id={}", domain_id)

    except asyncio.CancelledError:
        await _update_state(phase="download", status="paused")
        logger.info("Collection paused for domain_id={}", domain_id)
        raise
    except Exception as exc:
        logger.error("Collection failed for domain_id={}: {}", domain_id, exc)
        try:
            await _update_state(phase="error", status="failed", progress=0)
        except Exception as state_exc:
            logger.warning(
                "Failed to persist error state for domain_id={}: {}",
                domain_id, state_exc,
            )
        raise
    finally:
        # Identity check: only pop if we're still the live task. A restart
        # triggered by /resume while our finally was pending would otherwise
        # evict the new live task reference, orphaning it from /pause.
        current = asyncio.current_task()
        async with _active_tasks_lock:
            if _active_tasks.get(domain_id) is current:
                _active_tasks.pop(domain_id, None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/collect", response_model=CollectResponse)
async def start_collection(body: CollectRequest):
    """Validate domain, create/resume a collection task."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        await db.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (body.domain,))
        await db.commit()
        cursor = await db.execute("SELECT id FROM domains WHERE name = ?", (body.domain,))
        row = await cursor.fetchone()
        domain_id: int = row[0]
    finally:
        await db.close()

    async with _active_tasks_lock:
        existing = _active_tasks.get(domain_id)
        if existing is not None and not existing.done():
            return CollectResponse(domain_id=domain_id, status="running")

        task = asyncio.create_task(
            _run_collection(domain_id, body.domain, db_path, config=body.config)
        )
        _active_tasks[domain_id] = task
    return CollectResponse(domain_id=domain_id, status="started")


@router.post("/collect/{domain_id}/pause")
async def pause_collection(domain_id: int):
    """Cancel the running asyncio task and mark state as paused."""
    task = _active_tasks.get(domain_id)
    if task is None or task.done():
        raise HTTPException(status_code=404, detail="No active collection for this domain")
    task.cancel()
    return {"domain_id": domain_id, "status": "paused"}


@router.post("/collect/{domain_id}/resume")
async def resume_collection(domain_id: int):
    """Resume collection from DB state."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT d.name FROM domains d WHERE d.id = ?", (domain_id,)
        )
        row = await cursor.fetchone()
    finally:
        await db.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Domain not found")

    domain = row[0]

    async with _active_tasks_lock:
        existing = _active_tasks.get(domain_id)
        if existing is not None and not existing.done():
            return {"domain_id": domain_id, "status": "already_running"}

        task = asyncio.create_task(
            _run_collection(domain_id, domain, db_path, config=None)
        )
        _active_tasks[domain_id] = task
    return {"domain_id": domain_id, "status": "resumed"}


@router.get("/collect/{domain_id}/status", response_model=CollectStatus)
async def get_collect_status(domain_id: int):
    """Return CollectStatus from crawl_state table."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute(
            """SELECT cs.domain_id, d.name, cs.phase, cs.status, cs.progress,
                      cs.total_snapshots, cs.snapshots_indexed,
                      cs.pages_downloaded, cs.pages_failed,
                      cs.started_at, cs.updated_at
               FROM crawl_state cs
               JOIN domains d ON d.id = cs.domain_id
               WHERE cs.domain_id = ?""",
            (domain_id,),
        )
        row = await cursor.fetchone()

        # Compute coverage: pages downloaded / pages selected
        pages_downloaded = row[7] or 0 if row else 0
        pages_selected = 0
        coverage_pct = 0.0
        if row is not None:
            try:
                cursor2 = await db.execute(
                    "SELECT COUNT(*) FROM pages p JOIN snapshots s ON s.id = p.snapshot_id "
                    "WHERE s.domain_id = ? AND s.selected = 1",
                    (domain_id,),
                )
                count_row = await cursor2.fetchone()
                pages_selected = count_row[0] if count_row else 0
                if pages_selected > 0:
                    coverage_pct = round(100.0 * pages_downloaded / pages_selected, 1)
            except aiosqlite.Error as exc:
                logger.debug("Coverage query failed for domain_id={}: {}", domain_id, exc)
    finally:
        await db.close()

    if row is None:
        raise HTTPException(status_code=404, detail="No collection state for this domain")

    return CollectStatus(
        domain_id=row[0],
        domain=row[1],
        phase=row[2],
        status=row[3],
        progress=row[4],
        total_snapshots=row[5],
        snapshots_indexed=row[6],
        pages_downloaded=row[7],
        pages_failed=row[8],
        started_at=row[9],
        updated_at=row[10],
        pages_selected=pages_selected,
        coverage_pct=coverage_pct,
    )
