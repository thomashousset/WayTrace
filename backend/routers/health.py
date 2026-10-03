from __future__ import annotations

import time

from fastapi import APIRouter

from config import APP_VERSION, settings
from models import HealthResponse, StatsResponse
from services import archive_health, archive_rate, maintenance
from store import store

router = APIRouter(prefix="/api", tags=["health"])

_start_time: float = 0.0


def set_start_time() -> None:
    global _start_time
    _start_time = time.monotonic()


@router.get("/health", response_model=HealthResponse)
async def health():
    active = await store.active_count()
    return HealthResponse(
        status="ok",
        active_jobs=active,
        uptime_seconds=round(time.monotonic() - _start_time, 1),
        version=APP_VERSION,
        commit=settings.waytrace_commit,
        built_at=settings.waytrace_built_at,
    )


@router.get("/archive-status")
async def archive_status():
    """Public archive.org health (ok / slow / paused) so the UI can warn users.
    Includes the live adaptive request rate so it can be watched auto-tuning."""
    return {**archive_health.status(), "rate_per_minute": archive_rate.current_rate_per_minute()}


# The banner threshold: this many WAITING scans reads as "high traffic".
BUSY_WAITING_THRESHOLD = 3

# 7-day scan count for the homepage status strip. /service-status is polled by
# every open tab, so the DB is only re-asked once a minute.
_SCANS_7D_TTL = 60.0
_scans_7d_cache: dict = {"value": 0, "ts": 0.0}


async def _scans_last_7_days() -> int:
    now = time.monotonic()
    if now - _scans_7d_cache["ts"] > _SCANS_7D_TTL:
        from db import count_jobs_last_days
        _scans_7d_cache["value"] = await count_jobs_last_days(7)
        _scans_7d_cache["ts"] = now
    return _scans_7d_cache["value"]


@router.get("/service-status")
async def service_status():
    """One-call status for the frontend banner: archive.org health plus
    WayTrace's own load and the operator maintenance flag. Never 500s; each
    sub-payload degrades independently."""
    try:
        archive = {**archive_health.status(),
                   "rate_per_minute": archive_rate.current_rate_per_minute()}
    except Exception:
        archive = {"state": "ok", "cooldown_remaining": 0, "message": ""}
    try:
        active, waiting = len(store.active), len(store.waiting)
    except Exception:
        active, waiting = 0, 0
    try:
        scans_7d = await _scans_last_7_days()
    except Exception:
        scans_7d = 0
    if maintenance.is_enabled():
        state = "maintenance"
    elif waiting >= BUSY_WAITING_THRESHOLD:
        state = "busy"
    else:
        state = "ok"
    # First-run gate for the self-host wizard. True (never route to setup) when
    # the panel is off (hosted) so the wizard is a self-host-only concern.
    if settings.config_panel_enabled:
        try:
            from routers.selfhost_config import setup_completed as _setup_done
            setup_ok = await _setup_done()
        except Exception:
            setup_ok = True
    else:
        setup_ok = True
    return {
        "archive": archive,
        "service": {
            "state": state,
            "active": active,
            "waiting": waiting,
            "max_active": settings.max_active_total,
            "max_queue": settings.max_queue_total,
            "maintenance": maintenance.is_enabled(),
            "maintenance_message": maintenance.message() or None,
            "notice": maintenance.notice() or None,
            "scans_7d": scans_7d,
            "retention_days": settings.scan_retention_days,
            "config_panel": settings.config_panel_enabled,
            "setup_completed": setup_ok,
        },
    }


@router.get("/stats", response_model=StatsResponse)
async def stats():
    active = await store.active_count()
    return StatsResponse(
        total_scans_run=store.total_scans_run,
        active_jobs=active,
    )
