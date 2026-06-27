"""Background asyncio tasks: queue worker + expired jobs cleanup.

Wired up in main.py's lifespan. Each task is its own coroutine and
keeps running until cancelled at shutdown.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from config import settings
from db import delete_expired_jobs


async def queue_worker_tick(store, run_scan: Callable[[str], Awaitable[None]]) -> str | None:
    """Promote one waiting job to active if a slot is free.

    Returns the promoted job_id, or None if nothing to do.
    Side effect: schedules an asyncio task running run_scan(job_id).
    """
    job_id = await store.take_next()
    if job_id is None:
        return None
    asyncio.create_task(run_scan(job_id))
    return job_id


async def queue_worker_loop(
    store, run_scan: Callable[[str], Awaitable[None]], tick_seconds: float = 0.2
) -> None:
    """Long-running coroutine: poll the queue and dispatch waiting jobs."""
    logger.info("Queue worker started (tick={}s)", tick_seconds)
    while True:
        try:
            await queue_worker_tick(store, run_scan)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Queue worker tick failed")
        await asyncio.sleep(tick_seconds)


async def cleanup_loop() -> None:
    """Long-running coroutine: hourly deletion of expired jobs."""
    logger.info("Cleanup loop started (interval={}s)", settings.cleanup_interval_seconds)
    while True:
        try:
            n = await delete_expired_jobs()
            if n:
                logger.info("Cleanup removed {} expired jobs", n)
            from db import maintain
            await maintain()  # PRAGMA optimize + WAL checkpoint
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cleanup loop failed")
        await asyncio.sleep(settings.cleanup_interval_seconds)
