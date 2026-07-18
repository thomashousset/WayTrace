"""Admin-controlled maintenance flag.

When enabled, /api/service-status reports state "maintenance" and the frontend
shows a maintenance banner ahead of every other status. Persisted in the
app_state KV table so it survives restarts; cached in memory so reading it
costs nothing on the hot path.
"""
from __future__ import annotations

import json

from loguru import logger

_KEY = "maintenance"
_state: dict = {"enabled": False, "message": ""}


def is_enabled() -> bool:
    return bool(_state["enabled"])


def message() -> str:
    return _state["message"] or ""


async def load_from_db() -> None:
    """Boot-time hydration; missing/corrupt state means 'off'."""
    try:
        from db import get_app_state
        raw = await get_app_state(_KEY)
        if raw:
            data = json.loads(raw)
            _state["enabled"] = bool(data.get("enabled"))
            _state["message"] = str(data.get("message") or "")
    except Exception as exc:
        logger.debug("maintenance flag load skipped: {}", exc)


async def set_state(enabled: bool, message_text: str = "") -> None:
    _state["enabled"] = bool(enabled)
    _state["message"] = message_text or ""
    try:
        from db import set_app_state
        await set_app_state(_KEY, json.dumps(_state))
    except Exception as exc:
        logger.warning("maintenance flag not persisted: {}", exc)
