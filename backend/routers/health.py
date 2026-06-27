from __future__ import annotations

import time

from fastapi import APIRouter

from models import HealthResponse, StatsResponse
from services import archive_health
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
    )


@router.get("/archive-status")
async def archive_status():
    """Public archive.org health (ok / slow / paused) so the UI can warn users."""
    return archive_health.status()


@router.get("/stats", response_model=StatsResponse)
async def stats():
    active = await store.active_count()
    return StatsResponse(
        total_scans_run=store.total_scans_run,
        active_jobs=active,
    )
