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
    )


@router.get("/archive-status")
async def archive_status():
    """Public archive.org health (ok / slow / paused) so the UI can warn users.
    Includes the live adaptive request rate so it can be watched auto-tuning."""
    return {**archive_health.status(), "rate_per_minute": archive_rate.current_rate_per_minute()}


# The banner threshold: this many WAITING scans reads as "high traffic".
BUSY_WAITING_THRESHOLD = 3


@router.get("/service-status")
async def service_status():
    """One-call status for the frontend banner: archive.org health plus
    WayTrace's own load and the admin maintenance flag. Never 500s; each
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
    if maintenance.is_enabled():
        state = "maintenance"
    elif waiting >= BUSY_WAITING_THRESHOLD:
        state = "busy"
    else:
        state = "ok"
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
            "retention_days": settings.scan_retention_days,
        },
    }


@router.get("/stats", response_model=StatsResponse)
async def stats():
    active = await store.active_count()
    return StatsResponse(
        total_scans_run=store.total_scans_run,
        active_jobs=active,
    )
