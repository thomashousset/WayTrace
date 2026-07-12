from __future__ import annotations

import time

from fastapi import APIRouter

from config import APP_VERSION
from models import HealthResponse, StatsResponse
from services import archive_health, archive_rate
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


@router.get("/stats", response_model=StatsResponse)
async def stats():
    active = await store.active_count()
    return StatsResponse(
        total_scans_run=store.total_scans_run,
        active_jobs=active,
    )
