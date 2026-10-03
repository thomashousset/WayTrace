"""Read-only scan namespace under /api/s/{url_id}.

The url_id is a 24-char random token (~144 bits) generated server-side
at submission time; knowing the url_id is the only capability needed to
view a scan.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from loguru import logger
from config import settings
from db import (
    delete_job,
    find_public_demo_scan,
    get_job_by_url_id,
    search_scan_pages,
    list_recent_scans,
)
from services.html_export import build_standalone_html
from store import store


router = APIRouter(prefix="/api", tags=["public"])



# What a scan hands to whoever holds its link. An allowlist, not a list of
# things to strip: the jobs row also carries the scanner's IP, their account
# id, a notify address and the raw scan config, and the previous code removed
# user_id by name while client_ip sat in the same dict. A column added later
# must not leak by default.
_PUBLIC_FIELDS = (
    "url_id", "domain", "status",
    "created_at", "expires_at", "completed_at",
    "is_published", "published_at",
    "meta", "results",
)


def _public_view(job: dict) -> dict:
    return {k: job.get(k) for k in _PUBLIC_FIELDS if k in job}


def _is_expired(iso_str: str | None) -> bool:
    """True when this scan is past its retention date.

    A date that cannot be read counts as expired. Retention is a promise, and
    the old parser understood exactly one format and answered "still valid" to
    everything else, so a timestamp written any other way would have kept a
    scan readable forever.
    """
    if not iso_str:
        return False                      # never given an expiry
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
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
        # Live per-category counts pushed during the extraction phase so findings
        # appear on the loading page as they're extracted (no refresh).
        "live_counts": live.get("live_counts"),
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
    payload = _public_view(persisted)
    payload["owned"] = persisted.get("user_id") is not None
    payload["can_publish"] = _owner_ok(persisted, user)
    return payload


def _owner_ok(job: dict | None, user: dict | None) -> bool:
    """Anonymous scans (user_id NULL) keep the url_id-capability model; scans
    owned by an account can only be changed by that account."""
    owner = job.get("user_id") if job else None
    if owner is None:
        return True
    return bool(user) and user.get("id") == owner


@router.delete("/s/{url_id}")
async def delete_scan(url_id: str, request: Request):
    """Permanently delete a scan: cancel it if still running, hard-delete the
    persisted row so it disappears from the scan history."""
    user = None
    live = await store.get_job_by_url_id(url_id)
    persisted = await get_job_by_url_id(url_id)
    if live is None and persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if not _owner_ok(live or persisted, user):
        raise HTTPException(status_code=403, detail="This scan belongs to another account.")
    if live is not None:
        # Marks the live job cancelled; _persist_and_finish then skips re-saving
        # it, so a running scan cannot resurrect itself after deletion.
        await store.cancel_job(live["id"])
    await delete_job(url_id)
    return {"url_id": url_id, "deleted": True}


@router.get("/s/{url_id}/search")
async def search_scan(url_id: str, q: str = "", limit: int = 50):
    """Full-text search within a scan's archived page content.

    Returns matching snapshots (url + timestamp) with a highlighted excerpt,
    ranked by relevance. The url_id is the capability token, same as viewing.
    """
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    results = await search_scan_pages(url_id, q, limit=limit)
    return {"query": q, "count": len(results), "results": results}


@router.get("/example-scan")
async def get_example_scan():
    """url_id of the permanently-kept demo scan (settings.example_scan_domain),
    so the homepage can open a real report before the visitor runs anything."""
    domain = settings.example_scan_domain
    if not domain:
        raise HTTPException(status_code=404, detail="No example scan configured")
    # The one lookup that deliberately ignores ownership: the operator named
    # this domain in EXAMPLE_SCAN_DOMAIN precisely so its report is public.
    row = await find_public_demo_scan(domain)
    if row is None:
        raise HTTPException(status_code=404, detail="Example scan not ready yet")
    return {"url_id": row["url_id"], "domain": row["domain"]}


@router.get("/local-scans")
async def local_scans(limit: int = 50):
    """SOLO / self-hosted 'My scans': every scan this instance has run.
    Disabled on the hosted build, which scopes scans per account, so it can
    never expose other users' private scans."""
    return {"scans": await list_recent_scans(limit=limit)}


@router.get("/s/{url_id}/export.html")
async def export_scan_html(url_id: str):
    """Standalone HTML snapshot of a scan, downloadable for offline viewing."""
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    # The export is a file people hand over, so it gets the same view as the
    # share link and not the whole row.
    html = build_standalone_html(_public_view(persisted))
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


# Excel, LibreOffice and Sheets all treat a cell opening with one of these as
# a formula. Everything WayTrace exports was written by someone else and
# archived years ago, so a page crafted with =HYPERLINK("…"&A1) in it would
# exfiltrate the rest of the sheet the moment an analyst opened the download.
# Prefixing with an apostrophe is what spreadsheets read as "this is text":
# the value stays legible and strips back to the original in one character.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in _FORMULA_LEAD else text


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
    """Flat CSV of every finding: category, value, dates, occurrences, source.

    Provenance-first (matches the neutral report): each row carries the archived
    source page rather than a severity verdict."""
    # Imported here to avoid a circular import at module load.
    from services.extractor.item_values import item_value as _item_value
    persisted = await get_job_by_url_id(url_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if _is_expired(persisted.get("expires_at")):
        raise HTTPException(status_code=410, detail="Scan expired")
    results = persisted.get("results") or {}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["category", "value", "first_seen", "last_seen", "occurrences", "source"])
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
            writer.writerow([_csv_safe(c) for c in (
                cat, value,
                item.get("first_seen", ""), item.get("last_seen", ""),
                item.get("occurrences", 1),
                item.get("source_url", ""),
            )])
    return Response(
        content=output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_export_name(persisted, "csv")}"'},
    )
