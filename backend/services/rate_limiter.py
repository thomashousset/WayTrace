# backend/services/rate_limiter.py
"""Auto-adaptive rate limiter for Wayback Machine requests."""
from __future__ import annotations

import asyncio
from random import uniform


class RateLimiter:
    """Adapts request delay based on server responses.

    Speeds up after sustained success, backs off on 429/errors.
    State can be persisted to/from a dict for resume support.
    """

    def __init__(
        self,
        initial_delay: float = 1.0,
        min_delay: float = 0.3,
        max_delay: float = 120.0,
        speedup_factor: float = 0.9,
        speedup_streak: int = 10,
        backoff_factor: float = 2.0,
        pause_429: float = 30.0,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.speedup_factor = speedup_factor
        self.speedup_streak = speedup_streak
        self.backoff_factor = backoff_factor
        self.pause_429 = pause_429

        self.delay = initial_delay
        self.success_streak = 0
        self.backoff_level = 0

    async def wait(self) -> None:
        """Sleep for the current delay plus jitter."""
        jitter = uniform(0, self.delay * 0.3)
        await asyncio.sleep(self.delay + jitter)

    async def wait_429(self) -> None:
        """Additional pause after a 429 response."""
        level = max(1, self.backoff_level)
        await asyncio.sleep(self.pause_429 * level)

    def on_success(self) -> None:
        """Call after a successful (2xx) response."""
        self.success_streak += 1
        if self.success_streak >= self.speedup_streak and self.delay > self.min_delay:
            self.delay = self.delay * self.speedup_factor
            self.success_streak = 0
            self.backoff_level = max(0, self.backoff_level - 1)

    def on_429(self) -> None:
        """Call after a 429 Too Many Requests response."""
        self.backoff_level += 1
        self.delay = min(self.delay * self.backoff_factor, self.max_delay)
        self.success_streak = 0

    def on_error(self) -> None:
        """Call after a 5xx or timeout error."""
        self.delay = min(self.delay * 1.5, self.max_delay)
        self.success_streak = 0

    def get_state(self) -> dict:
        """Export state for persistence."""
        return {
            "delay": self.delay,
            "success_streak": self.success_streak,
            "backoff_level": self.backoff_level,
        }

    def restore_state(self, state: dict) -> None:
        """Restore state from a persisted dict."""
        self.delay = state.get("delay", self.delay)
        self.success_streak = state.get("success_streak", 0)
        self.backoff_level = state.get("backoff_level", 0)
