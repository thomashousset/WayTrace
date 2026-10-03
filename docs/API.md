# WayTrace API Reference

Base URL: `http://localhost:8000`

Interactive docs: set `EXPOSE_API_DOCS=1` in `.env`, then open
`http://localhost:8000/api/docs` (Swagger UI). Hidden by default.

---

## Scanning

### POST /api/scan/preflight

Lightweight domain analysis: fetches the CDX index only (a few seconds) and
returns stats plus a suggested configuration. No page is downloaded.

**Request body:**
```json
{ "domain": "example.com" }
```

**Success response (200):**
```json
{
  "domain": "example.com",
  "total_snapshots": 47404,
  "html_snapshots": 47404,
  "unique_paths": 971,
  "unique_content": 8120,
  "date_range": { "first": "2003-08", "last": "2026-03" },
  "suggested_config": { "depth": "standard", "cap": 800 },
  "path_groups": [ ... ],
  "subdomain_groups": [ ... ]
}
```

### POST /api/scan

Submit a full scan. Returns immediately with a job id and the public `url_id`
of the future report; the scan itself runs through the queue.

**Request body:**
```json
{
  "domain": "example.com",
  "config": {
    "depth": "standard",
    "cap": 500,
    "date_from": "2015-01",
    "date_to": "2026-01",
    "exclude_keywords": ["blog"],
    "categories": ["emails", "subdomains"]
  }
}
```
Everything except `domain` is optional. `depth` is one of `quick`,
`standard`, `full`, `max`.

**Success response (200):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "url_id": "a1b2c3d4e5f6a7b8c9d0e1f2",
  "url": "/s/a1b2c3d4e5f6a7b8c9d0e1f2",
  "status": "queued",
  "position": 0,
  "eta_seconds": 0,
  "reused": false,
  "live": false,
  "retention_days": 14
}
```

`reused: true` means a recent scan of the same domain already existed and was
returned instead of re-scanning (send `"force": true` to bypass). If the
domain is already being scanned right now, you are attached to that live scan
(`reused` + `live` both true).

### GET /api/jobs/{job_id}

Poll a scan's progress. Returns status (`queued` / `running` / `completed` /
`failed` / `cancelled`), `progress` (0-100), the current `step`, queue
`position` / `eta_seconds` while waiting, and the full `results` object once
completed.

### GET /api/jobs/{job_id}/stream

Same information as Server-Sent Events, for clients that prefer push over
polling.

## Reports (by url_id)

A finished scan is addressed by its 24-char `url_id`; knowing it is the only
capability needed.

| Method and path | Description |
|---|---|
| `GET /api/s/{url_id}` | Full report payload (findings, meta, timeline). |
| `GET /api/s/{url_id}/search?q=...` | Full-text search inside the scan's archived pages. |
| `GET /api/s/{url_id}/export.html` | Standalone HTML report (offline viewing). |
| `GET /api/s/{url_id}/export.json` | Raw findings as JSON. |
| `GET /api/s/{url_id}/export.csv` | Findings as CSV. A value starting with `=`, `+`, `-` or `@` is prefixed with an apostrophe so a spreadsheet reads it as text rather than as a formula. That includes international phone numbers and `@handles`. Strip one leading character to get the archived value back, or use `export.json`, which is untouched. |
| `DELETE /api/s/{url_id}` | Cancel (if running) and permanently delete the scan. |
| `GET /api/local-scans` | Every scan this instance has run (self-hosted history). |
| `GET /api/example-scan` | `url_id` of the configured demo scan, if any. |

## Status

| Method and path | Description |
|---|---|
| `GET /api/health` | `{ status, active_jobs, uptime_seconds, version }`. |
| `GET /api/stats` | Total scans run + active jobs. |
| `GET /api/service-status` | One-call status for the frontend banner: archive.org health, own load, maintenance flag. |
| `GET /api/archive-status` | archive.org reachability details. |

## Self-host configuration

The Settings panel in the UI is backed by:

| Method and path | Description |
|---|---|
| `GET /api/config` | Current values, descriptions, bounds and risk zones for every editable setting. |
| `PUT /api/config` | Apply new values (persisted in SQLite, applied live). |
| `POST /api/config/reset` | Back to safe defaults. |
| `POST /api/config/restart` | Restart the server process. |
| `POST /api/config/complete-setup` | Mark the first-run wizard as done. |

## Errors

Errors follow FastAPI conventions: `{ "detail": ... }` with 404 (unknown job
or scan), 410 (scan expired), 422 (validation), 429 (queue or per-IP limit)
and 503 (maintenance / archive.org down).
