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
import uuid
from collections import deque
from datetime import datetime, timezone

from config import settings
from services.ids import generate_url_id


class PerIpLimitError(Exception):
    """Raised when a single client IP has too many in-flight jobs."""


class QueueFullError(Exception):
    """Raised when the global active+waiting queue is at its hard cap."""


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
            return {
                "job_id": job_id,
                "url_id": url_id,
                "status": "queued",
                "position": position,
                "eta_seconds": int(self.avg_scan_seconds * max(position - 1, 0)),
            }

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
        """User-initiated cancellation. Returns True if cancelled, False otherwise."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job["status"] not in ("queued", "running"):
                return False
            try:
                self.waiting.remove(job_id)
            except ValueError:
                pass
            if job_id in self.active:
                self.active.remove(job_id)
            ip = job.get("client_ip")
            if ip and self.per_ip_count.get(ip, 0) > 0:
                self.per_ip_count[ip] -= 1
                if self.per_ip_count[ip] == 0:
                    self.per_ip_count.pop(ip, None)
            job["status"] = "cancelled"
            job["step"] = "Cancelled"
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
