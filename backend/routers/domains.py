"""Domains and findings API routes ; list, detail, filter, export."""
from __future__ import annotations

import csv
import io
import json
import zlib
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from config import settings
from db import get_db

router = APIRouter(prefix="/api", tags=["domains"])


# ---------------------------------------------------------------------------
# GET /api/domains
# ---------------------------------------------------------------------------

@router.get("/domains")
async def list_domains():
    """List all domains with their crawl status."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute(
            """SELECT d.id, d.name, d.created_at,
                      cs.status, cs.phase, cs.progress,
                      cs.pages_downloaded, cs.pages_failed, cs.updated_at
               FROM domains d
               LEFT JOIN crawl_state cs ON cs.domain_id = d.id
               ORDER BY d.id"""
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    result = []
    for row in rows:
        result.append({
            "id": row[0],
            "name": row[1],
            "created_at": row[2],
            "status": row[3],
            "phase": row[4],
            "progress": row[5],
            "pages_downloaded": row[6],
            "pages_failed": row[7],
            "updated_at": row[8],
        })
    return result


# ---------------------------------------------------------------------------
# GET /api/domains/{domain_id}
# ---------------------------------------------------------------------------

@router.get("/domains/{domain_id}")
async def get_domain(domain_id: int):
    """Domain details with finding counts per category."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT id, name, created_at FROM domains WHERE id = ?", (domain_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Domain not found")

        domain_data = {"id": row[0], "name": row[1], "created_at": row[2]}

        # Crawl state + coverage record.
        cursor = await db.execute(
            """SELECT status, phase, progress, total_snapshots, snapshots_indexed,
                      pages_downloaded, pages_failed, started_at, updated_at,
                      auto_depth, total_estimate, sampled_snapshots,
                      truncated, truncation_reason
               FROM crawl_state WHERE domain_id = ?""",
            (domain_id,),
        )
        cs_row = await cursor.fetchone()
        if cs_row:
            domain_data["crawl"] = {
                "status": cs_row[0],
                "phase": cs_row[1],
                "progress": cs_row[2],
                "total_snapshots": cs_row[3],
                "snapshots_indexed": cs_row[4],
                "pages_downloaded": cs_row[5],
                "pages_failed": cs_row[6],
                "started_at": cs_row[7],
                "updated_at": cs_row[8],
            }
            # Coverage block. Older rows predate the columns; surface
            # them as null/0 so the response shape stays stable.
            total_estimate = cs_row[10] or 0
            sampled = cs_row[11] or 0
            truncated_flag = bool(cs_row[12])
            coverage_pct = (
                round(sampled / total_estimate, 4)
                if total_estimate > 0 else None
            )
            domain_data["coverage"] = {
                "auto_depth": cs_row[9],
                "total_estimate": total_estimate or None,
                "sampled_snapshots": sampled or None,
                "truncated": truncated_flag,
                "truncation_reason": cs_row[13],
                "coverage_pct": coverage_pct,
            }

        # Finding counts by category
        cursor = await db.execute(
            """SELECT category, COUNT(*) as cnt
               FROM findings WHERE domain_id = ?
               GROUP BY category ORDER BY category""",
            (domain_id,),
        )
        cat_rows = await cursor.fetchall()
        domain_data["findings_summary"] = {r[0]: r[1] for r in cat_rows}
        domain_data["total_findings"] = sum(r[1] for r in cat_rows)
    finally:
        await db.close()

    return domain_data


# ---------------------------------------------------------------------------
# GET /api/domains/{domain_id}/findings
# ---------------------------------------------------------------------------

@router.get("/domains/{domain_id}/findings")
async def get_findings(
    domain_id: int,
    category: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Return findings for a domain, optionally filtered by category and/or severity."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT id FROM domains WHERE id = ?", (domain_id,)
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Domain not found")

        query = (
            "SELECT id, category, value, metadata, first_seen, last_seen, "
            "occurrences, severity FROM findings WHERE domain_id = ?"
        )
        params: list = [domain_id]

        if category:
            query += " AND category = ?"
            params.append(category)
        if severity:
            query += " AND severity = ?"
            params.append(severity)

        query += " ORDER BY occurrences DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    finally:
        await db.close()

    result = []
    for row in rows:
        finding = {
            "id": row[0],
            "category": row[1],
            "value": row[2],
            "first_seen": row[4],
            "last_seen": row[5],
            "occurrences": row[6],
            "severity": row[7],
        }
        if row[3]:
            try:
                finding["metadata"] = json.loads(row[3])
            except (json.JSONDecodeError, TypeError):
                finding["metadata"] = {}
        result.append(finding)
    return result


# ---------------------------------------------------------------------------
# GET /api/domains/{domain_id}/export
# ---------------------------------------------------------------------------

@router.get("/domains/{domain_id}/export")
async def export_findings(
    domain_id: int,
    format: Annotated[str, Query()] = "json",
):
    """Export findings grouped by category. format=json|csv."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT name FROM domains WHERE id = ?", (domain_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Domain not found")
        domain_name = row[0]

        cursor = await db.execute(
            """SELECT category, value, metadata, first_seen, last_seen,
                      occurrences, severity
               FROM findings WHERE domain_id = ?
               ORDER BY category, occurrences DESC""",
            (domain_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    # Group by category
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        cat = row[0]
        finding = {
            "value": row[1],
            "first_seen": row[3],
            "last_seen": row[4],
            "occurrences": row[5],
            "severity": row[6],
        }
        if row[2]:
            try:
                finding["metadata"] = json.loads(row[2])
            except (json.JSONDecodeError, TypeError):
                pass
        grouped.setdefault(cat, []).append(finding)

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["category", "value", "first_seen", "last_seen", "occurrences", "severity"])
        for cat, items in sorted(grouped.items()):
            for item in items:
                writer.writerow([
                    cat,
                    item["value"],
                    item.get("first_seen", ""),
                    item.get("last_seen", ""),
                    item.get("occurrences", 1),
                    item.get("severity", ""),
                ])
        output.seek(0)
        filename = f"{domain_name}_findings.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return JSONResponse(content=grouped)


# ---------------------------------------------------------------------------
# GET /api/pages/{page_id}/view
# ---------------------------------------------------------------------------

@router.get("/pages/{page_id}/view")
async def view_page(page_id: int):
    """Serve an archived HTML page from the database."""
    db_path = settings.database_url
    db = await get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT p.html FROM pages p WHERE p.id = ? AND p.status = 'done'",
            (page_id,),
        )
        row = await cursor.fetchone()
    finally:
        await db.close()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Page not found")

    try:
        html = zlib.decompress(row[0]).decode("utf-8", errors="replace")
    except (zlib.error, TypeError):
        raise HTTPException(status_code=500, detail="Failed to decompress page")

    # Serve the archived page with the tightest sandbox we can: no script
    # execution, no forms, no same-origin access. CSP 'sandbox' puts the
    # response in an ephemeral origin, so any stored <script> in the page
    # cannot reach WayTrace APIs, cookies, or localStorage. X-Frame-Options
    # is set in case an operator embeds the iframe from another origin.
    # CSP default-src 'none' with inline-style allowance keeps the visual
    # rendering usable while blocking every external fetch.
    security_headers = {
        "Content-Security-Policy": (
            "sandbox; "
            "default-src 'none'; "
            "img-src 'self' data: https:; "
            "style-src 'unsafe-inline' 'self' https:; "
            "font-src 'self' data: https:; "
            "frame-ancestors 'self'"
        ),
        "X-Frame-Options": "SAMEORIGIN",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    return HTMLResponse(content=html, headers=security_headers)
