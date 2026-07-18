"""In-memory job queue with per-IP and global caps.

Active = currently running scans (max settings.max_active_total).
Waiting = queued, will be promoted to active by the queue worker
(see services/background_tasks.py).

On crash/restart this in-memory state is lost. Completed jobs persist
to SQLite (see db.save_job) so /s/{url_id} lookups survive across
restarts. Running scans at restart time die and need to be re-submitted.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from loguru import logger

from config import settings
from services.ids import generate_url_id


class PerIpLimitError(Exception):
    """Raised when a single client IP has too many in-flight jobs."""


class QueueFullError(Exception):
    """Raised when the global active+waiting queue is at its hard cap."""


class PerUserLimitError(Exception):
    """Raised when an account already has its max scans in flight."""


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._url_id_to_job: dict[str, str] = {}
        self.active: list[str] = []
        self.waiting: deque[str] = deque()
        self.per_ip_count: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.total_scans_run: int = 0
        # Moving average of completed scan duration. Used to estimate ETA.
        # Initial guess: 3 minutes per scan (will adapt after first 10 scans).
        self.avg_scan_seconds: float = 180.0

    # ------------------------------------------------------------------
    # Job creation + queue placement
    # ------------------------------------------------------------------

    async def create_job(
        self,
        domain: str,
        client_ip: str = "0.0.0.0",
        config: object = None,
        selected_snapshots: list | None = None,
        publish_on_complete: bool = False,
        user_id: int | None = None,
        notify_email: str | None = None,
    ) -> dict:
        async with self._lock:
            if self.per_ip_count.get(client_ip, 0) >= settings.max_active_per_ip:
                raise PerIpLimitError(client_ip)
            if len(self.active) + len(self.waiting) >= settings.max_queue_total:
                raise QueueFullError()

            job_id = str(uuid.uuid4())
            url_id = generate_url_id()
            now = datetime.now(timezone.utc)
            self._jobs[job_id] = {
                "id": job_id,
                "url_id": url_id,
                "domain": domain,
                "client_ip": client_ip,
                "status": "queued",
                "progress": 0,
                "step": "Queued",
                "created_at": now,
                "updated_at": now,
                "meta": None,
                "results": None,
                "config": config,
                "selected_snapshots": selected_snapshots,
                "publish_on_complete": publish_on_complete,
                "user_id": user_id,
                "notify_email": notify_email,
            }
            self._url_id_to_job[url_id] = job_id
            self.per_ip_count[client_ip] = self.per_ip_count.get(client_ip, 0) + 1
            self.total_scans_run += 1

            if len(self.active) < settings.max_active_total:
                self.active.append(job_id)
                position = 0
            else:
                self.waiting.append(job_id)
                position = len(self.waiting)
            res = {
                "job_id": job_id,
                "url_id": url_id,
                "status": "queued",
                "position": position,
                "eta_seconds": int(self.avg_scan_seconds * max(position - 1, 0)),
            }

        # Best-effort persistence so a restart re-enqueues this job instead of
        # losing it. Outside the lock, and never blocks or fails the submission
        # (tests and the public build may run without an initialized DB).
        try:
            from db import save_queued_job
            await save_queued_job(
                url_id=url_id, job_id=job_id, domain=domain,
                client_ip=client_ip, created_at=now,
                expires_at=now + timedelta(days=settings.scan_retention_days),
                user_id=user_id,
                config_json=config.model_dump_json()
                if config is not None and hasattr(config, "model_dump_json") else None,
                selected_snapshots_json=json.dumps(selected_snapshots)
                if selected_snapshots else None,
                publish_on_complete=bool(publish_on_complete),
                notify_email=notify_email,
            )
        except Exception as exc:
            logger.debug("queue persistence skipped for {}: {}", job_id, exc)
        return res

    async def restore_pending_jobs(self) -> int:
        """Rebuild queued/running jobs persisted before a restart.

        Every restored job re-enters the WAITING queue (a job that was mid-run
        restarts from zero; its url_id and job_id are preserved so links and
        pollers keep working). Returns the number restored."""
        try:
            from db import load_resumable_jobs
            rows = await load_resumable_jobs()
        except Exception as exc:
            logger.debug("queue restore skipped: {}", exc)
            return 0
        restored = 0
        for row in rows:
            try:
                if await self._restore_one(row):
                    restored += 1
            except Exception:
                logger.exception("Could not restore job {}", row.get("url_id"))
                # Don't leave a phantom 'queued'/'running' row that can never
                # run (e.g. a config_json the current schema can't parse): mark
                # it failed so it stops showing as in-flight and isn't retried
                # on every subsequent restart.
                url_id = row.get("url_id")
                if url_id:
                    try:
                        from db import update_job_queue_status
                        await update_job_queue_status(url_id, "failed")
                    except Exception:
                        logger.debug("could not mark unrestorable job {} failed", url_id)
        return restored

    async def _restore_one(self, row: dict) -> bool:
        config = None
        if row.get("config_json"):
            from models import ScanConfig
            config = ScanConfig.model_validate_json(row["config_json"])
        selected = (
            json.loads(row["selected_snapshots_json"])
            if row.get("selected_snapshots_json") else None
        )
        try:
            created = datetime.strptime(
                row["created_at"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            created = datetime.now(timezone.utc)
        job_id = row["job_id"]
        async with self._lock:
            if job_id in self._jobs:
                return False
            ip = row.get("client_ip") or "0.0.0.0"
            self._jobs[job_id] = {
                "id": job_id,
                "url_id": row["url_id"],
                "domain": row["domain"],
                "client_ip": ip,
                "status": "queued",
                "progress": 0,
                "step": "Queued",
                "created_at": created,
                "updated_at": datetime.now(timezone.utc),
                "meta": None,
                "results": None,
                "config": config,
                "selected_snapshots": selected,
                "publish_on_complete": bool(row.get("publish_on_complete")),
                "user_id": row.get("user_id"),
                "notify_email": row.get("notify_email"),
            }
            self._url_id_to_job[row["url_id"]] = job_id
            self.per_ip_count[ip] = self.per_ip_count.get(ip, 0) + 1
            self.waiting.append(job_id)
            return True

    @staticmethod
    def _owner_key(job: dict | None) -> object:
        """Fairness key: the account if signed in, else the client IP."""
        if not job:
            return None
        uid = job.get("user_id")
        return ("u", uid) if uid is not None else ("ip", job.get("client_ip"))

    async def take_next(self) -> str | None:
        """Promote one waiting job to active if a slot is free.

        Fair scheduling: among waiting jobs, pick the one whose owner currently
        has the fewest active scans (tie broken by queue arrival order). This
        keeps one user's burst from monopolising the active slots while another
        user's first scan waits behind it. Returns the promoted job_id, or None.
        """
        async with self._lock:
            if len(self.active) >= settings.max_active_total:
                return None
            if not self.waiting:
                return None
            active_by_owner: dict[object, int] = {}
            for jid in self.active:
                key = self._owner_key(self._jobs.get(jid))
                active_by_owner[key] = active_by_owner.get(key, 0) + 1
            best_jid = None
            best_load = None
            for jid in self.waiting:  # deque preserves arrival order
                load = active_by_owner.get(self._owner_key(self._jobs.get(jid)), 0)
                if best_load is None or load < best_load:
                    best_jid, best_load = jid, load
                    if load == 0:
                        break  # cannot do better than an idle owner
            self.waiting.remove(best_jid)
            self.active.append(best_jid)
            return best_jid

    # ------------------------------------------------------------------
    # Lookups + updates
    # ------------------------------------------------------------------

    async def find_live_job_for_domain(self, domain: str) -> dict | None:
        """Oldest queued/running job for this domain, or None.

        Launch-day guardrail: when several people submit the same domain at
        once, later submissions attach to the scan already in flight instead
        of doubling the archive.org load."""
        async with self._lock:
            candidates = [
                j for j in self._jobs.values()
                if j.get("domain") == domain
                and j.get("status") in ("queued", "running")
            ]
            if not candidates:
                return None
            return min(candidates, key=lambda j: j["created_at"])

    async def get_job(self, job_id: str) -> dict | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def get_job_by_url_id(self, url_id: str) -> dict | None:
        async with self._lock:
            jid = self._url_id_to_job.get(url_id)
            return self._jobs.get(jid) if jid else None

    async def update_job(self, job_id: str, **fields) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                if key in job or key == "completed_at":
                    job[key] = value
            job["updated_at"] = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Completion / cancellation
    # ------------------------------------------------------------------

    async def finish_job(self, job_id: str, duration_seconds: float | None = None) -> None:
        """Remove from active/waiting + drop the live record, free per-IP slot, update avg duration.

        After this returns, lookups by job_id/url_id miss the in-memory store and
        fall back to the persisted jobs table (which holds the final state with
        expires_at, is_published, etc.).
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job_id in self.active:
                self.active.remove(job_id)
            try:
                self.waiting.remove(job_id)
            except ValueError:
                pass
            ip = job.get("client_ip")
            if ip and self.per_ip_count.get(ip, 0) > 0:
                self.per_ip_count[ip] -= 1
                if self.per_ip_count[ip] == 0:
                    self.per_ip_count.pop(ip, None)
            if duration_seconds is not None and duration_seconds > 0:
                # Exponential moving average (~10-sample window)
                self.avg_scan_seconds = 0.9 * self.avg_scan_seconds + 0.1 * duration_seconds
            # Drop the in-memory record; DB is now authoritative
            url_id = job.get("url_id")
            if url_id:
                self._url_id_to_job.pop(url_id, None)
            self._jobs.pop(job_id, None)

    async def cancel_job(self, job_id: str) -> bool:
        """User-initiated cancellation. Returns True if cancelled, False otherwise.

        A job that never started (still WAITING) is fully removed here: it will
        never reach finish_job, so leaving it in _jobs would leak memory and keep
        serving a phantom 'cancelled' page after delete_scan hard-deleted the DB
        row. A job that is ALREADY RUNNING is only flagged cancelled; its running
        task's finally -> _persist_and_finish -> finish_job does the cleanup and
        the per-IP release (so the slot is freed exactly once)."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job["status"] not in ("queued", "running"):
                return False
            job["status"] = "cancelled"
            job["step"] = "Cancelled"
            if job_id in self.active:
                # Running (or promoted-but-not-yet-started): let finish_job clean
                # up so active/per-IP are released exactly once.
                return True
            # Waiting and will never start: remove it now, releasing the slot.
            try:
                self.waiting.remove(job_id)
            except ValueError:
                pass
            ip = job.get("client_ip")
            if ip and self.per_ip_count.get(ip, 0) > 0:
                self.per_ip_count[ip] -= 1
                if self.per_ip_count[ip] == 0:
                    self.per_ip_count.pop(ip, None)
            url_id = job.get("url_id")
            if url_id:
                self._url_id_to_job.pop(url_id, None)
            self._jobs.pop(job_id, None)
            return True

    # ------------------------------------------------------------------
    # Queue position helpers (sync, no lock; safe enough, used for UX only)
    # ------------------------------------------------------------------

    def get_position(self, job_id: str) -> int | None:
        """1-based position in the waiting queue, or None if not waiting."""
        try:
            return list(self.waiting).index(job_id) + 1
        except ValueError:
            return None

    def get_eta_seconds(self, job_id: str) -> int:
        pos = self.get_position(job_id)
        if pos is None:
            return 0
        # Slots ahead of us = max(0, pos - free_slots). For simplicity
        # we assume all active slots are full when we're waiting.
        return int(self.avg_scan_seconds * pos)

    async def active_count(self) -> int:
        """Total in-flight count (active + waiting). Kept for /api/health."""
        async with self._lock:
            return len(self.active) + len(self.waiting)

    async def _reset_for_tests(self) -> None:
        """Wipe all in-memory state. Test-only helper."""
        async with self._lock:
            self._jobs.clear()
            self._url_id_to_job.clear()
            self.active.clear()
            self.waiting.clear()
            self.per_ip_count.clear()


store = JobStore()
