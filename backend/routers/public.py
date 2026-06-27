"""Public read-only namespace under /api/s/{url_id} + /api/feed.

These endpoints are exposed without authentication. The url_id is a
24-char random token (~144 bits) generated server-side at submission
time; knowing the url_id is the only "credential" needed to view or
publish a scan.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field

from db import (
    expire_job_now,
    get_job_by_url_id,
    list_feed,
    set_published,
)
from services.html_export import build_standalone_html
from store import store


router = APIRouter(prefix="/api", tags=["public"])


class PublishRequest(BaseModel):
    published: bool = True


class FeedResponse(BaseModel):
    items: list[dict] = Field(default_factory=list)
    count: int = 0


def _is_expired(iso_str: str | None) -> bool:
    if not iso_str:
        return False
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return dt <= datetime.now(timezone.utc)


def _live_to_payload(live: dict) -> dict:
    """Serialize an in-memory live job to the same shape as a persisted one."""
    return {
        "url_id": live["url_id"],
        "domain": live["domain"],
        "status": live["status"],
        "progress": live.get("progress", 0),
        "step": live.get("step", ""),
        "created_at": live["created_at"].strftime("%Y-%m-%dT%H:%M:%SZ")
        if hasattr(live["created_at"], "strftime") else live["created_at"],
        "expires_at": None,
        "completed_at": None,
        "is_published": 0,
        "published_at": None,
        "publish_on_complete": bool(live.get("publish_on_complete")),
        "meta": live.get("meta"),
        "results": live.get("results"),
        "position": store.get_position(live["id"]),
        "eta_seconds": store.get_eta_seconds(live["id"]),
        "total_in_queue": len(store.waiting),
    }


@router.get("/s/{url_id}")
async def get_scan_public(url_id: str, request: Request):
    """Lookup a scan by its public url_id.

    Prefers the in-memory store (so queued/running progress shows up live),
    falls back to the persisted jobs table for completed/older scans. The
    url_id is the only capability needed.
    """
    user = None
    live = await store.get_job_by_url_id(url_id)
    if live is not None:
        payload = _live_to_payload(live)
        payload["owned"] = live.get("user_id") is not None
        payload["can_publish"] = _owner_ok(live, user)
        return payload
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    owned = persisted.get("user_id") is not None
    can_publish = _owner_ok(persisted, user)
    payload = {k: v for k, v in persisted.items() if k != "user_id"}
    payload["owned"] = owned
    payload["can_publish"] = can_publish
    return payload


def _owner_ok(job: dict | None, user: dict | None) -> bool:
    """Anonymous scans (user_id NULL) keep the url_id-capability model; scans
    owned by an account can only be changed by that account."""
    owner = job.get("user_id") if job else None
    if owner is None:
        return True
    return bool(user) and user.get("id") == owner


@router.post("/s/{url_id}/publish")
async def publish_scan(url_id: str, body: PublishRequest, request: Request):
    """Toggle the scan's appearance in the public feed. The url_id is the
    capability token."""
    user = None
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    if not _owner_ok(persisted, user):
        raise HTTPException(status_code=403, detail="This scan belongs to another account.")
    ok = await set_published(url_id, bool(body.published))
    if not ok:
        raise HTTPException(status_code=404, detail="Scan not found")
    logger.info("Scan {} publish={}", url_id, body.published)
    return {"url_id": url_id, "published": bool(body.published)}


@router.delete("/s/{url_id}")
async def delete_scan(url_id: str, request: Request):
    """Cancel a queued/running scan and/or force-expire it in the DB."""
    user = None
    live = await store.get_job_by_url_id(url_id)
    persisted = await get_job_by_url_id(url_id)
    if live is None and persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if not _owner_ok(live or persisted, user):
        raise HTTPException(status_code=403, detail="This scan belongs to another account.")
    if live is not None:
        await store.cancel_job(live["id"])
    if persisted is not None:
        await expire_job_now(url_id)
    return {"url_id": url_id, "deleted": True}


@router.get("/feed", response_model=FeedResponse)
async def get_feed(limit: int = 20, offset: int = 0):
    """Return the N most recently published, non-expired scans."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    items = await list_feed(limit=limit, offset=offset)
    return FeedResponse(items=items, count=len(items))


@router.get("/s/{url_id}/export.html")
async def export_scan_html(url_id: str):
    """Standalone HTML snapshot of a scan, downloadable for offline viewing."""
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    html = build_standalone_html(persisted)
    safe_domain = "".join(
        c if c.isalnum() or c in "-_." else "_"
        for c in (persisted.get("domain") or "scan")
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"waytrace-{safe_domain}-{today}.html"
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_name(persisted: dict, ext: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_"
                   for c in (persisted.get("domain") or "scan"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"waytrace-{safe}-{today}.{ext}"


@router.get("/s/{url_id}/export.json")
async def export_scan_json(url_id: str):
    """Machine-readable export: the full findings tree as JSON."""
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    payload = {
        "tool": "WayTrace",
        "domain": persisted.get("domain"),
        "url_id": url_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": persisted.get("completed_at"),
        "meta": persisted.get("meta"),
        "results": persisted.get("results") or {},
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        content=body, media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_export_name(persisted, "json")}"'},
    )


@router.get("/s/{url_id}/export.csv")
async def export_scan_csv(url_id: str):
    """Flat CSV of every finding: category, value, dates, occurrences, severity."""
    # Imported here to avoid a circular import at module load.
    from routers.analyze import _item_value, _item_severity
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    results = persisted.get("results") or {}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["category", "value", "first_seen", "last_seen", "occurrences", "severity"])
    for cat in sorted(results):
        items = results[cat]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            value = _item_value(cat, item)
            if value is None:
                continue
            writer.writerow([
                cat, value,
                item.get("first_seen", ""), item.get("last_seen", ""),
                item.get("occurrences", 1),
                _item_severity(cat, item) or item.get("severity", ""),
            ])
    return Response(
        content=output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_export_name(persisted, "csv")}"'},
    )
