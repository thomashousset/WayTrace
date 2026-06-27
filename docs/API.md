# WayTrace API Reference

Base URL: `http://localhost:8000`

Interactive docs: `http://localhost:8000/docs` (Swagger UI)

---

## POST /api/scan/preflight

Lightweight domain analysis, fetches CDX index only (~2-5s) and returns stats + suggested configuration.

**Request body:**
```json
{
  "domain": "example.com"
}
```

**Success response (200):**
```json
{
  "domain": "example.com",
  "total_snapshots": 47404,
  "html_snapshots": 47404,
  "unique_paths": 971,
  "date_range": {
    "first": "2003-08",
    "last": "2026-03"
  },
  "suggested_config": {
    "cap": 800,
    "date_from": null,
    "date_to": null,
    "depth": "standard"
  }
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/api/scan/preflight \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.com"}'
```

---

## POST /api/scan

Start a new domain scan. Optionally provide a config to override defaults.

**Request body:**
```json
{
  "domain": "example.com",
  "config": {
    "cap": 500,
    "date_from": "2020-01",
    "date_to": null,
    "depth": "standard"
  }
}
```

`config` is entirely optional. If omitted, smart defaults are used.

**Config fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cap` | int \| null | auto-computed | Max snapshots to scrape |
| `date_from` | string \| null | null | Start date filter (`"YYYY-MM"`) |
| `date_to` | string \| null | null | End date filter (`"YYYY-MM"`) |
| `depth` | string | `"standard"` | Preset: `"quick"`, `"standard"`, or `"full"` |

**Depth presets:**

| Preset | Cap multiplier | Sampling strategy |
|--------|---------------|-------------------|
| `quick` | ×0.3 (min 20) | First + last per path only |
| `standard` | ×1.0 | Monthly homepage, semester for high-priority, yearly for medium |
| `full` | ×1.5 (max 1200) | Monthly for homepage + high-priority, semester for medium |

**Validation rules:**
- Must be a valid domain (no IPs, no `http://` prefix)
- `www.` prefix and trailing slashes are stripped automatically

**Success response (200):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Error responses:**
- `422`: Invalid domain format
- `429`: Too many active jobs

**Deduplication:** If a job for the same domain is already `queued` or `running`, the existing `job_id` is returned.

**Example:**
```bash
curl -X POST http://localhost:8000/api/scan \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.com", "config": {"depth": "full", "date_from": "2020-01"}}'
```

---

## GET /api/jobs/{job_id}

Get the current status and results of a scan job.

**Path parameters:**
- `job_id` (string, required), UUID returned by POST /api/scan

**Success response (200):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "domain": "example.com",
  "status": "completed",
  "progress": 100,
  "step": "Scan complete",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:05:00Z",
  "meta": {
    "domain": "example.com",
    "total_snapshots_found": 47404,
    "snapshots_analyzed": 800,
    "pages_scraped": 782,
    "pages_failed": 18,
    "date_first_seen": "2003-08",
    "date_last_seen": "2026-03",
    "scan_duration_seconds": 312.5
  },
  "results": {
    "endpoints": [],
    "emails": [],
    "phones": [],
    "subdomains": [],
    "analytics_trackers": [],
    "social_profiles": [],
    "persons": [],
    "technologies": [],
    "outgoing_links": [],
    "built_with": [],
    "html_comments": [],
    "cloud_buckets": [],
    "api_keys": [],
    "script_sources": [],
    "form_actions": [],
    "crypto_wallets": [],
    "iframe_sources": [],
    "meta_tags": [],
    "highlights": []
  }
}
```

**Job statuses:** `queued` → `running` → `completed` | `failed`

**Error responses:**
- `404`: Job not found or expired

---

## GET /api/health

Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "active_jobs": 2,
  "uptime_seconds": 3600.5
}
```

---

## GET /api/stats

Server statistics.

**Response:**
```json
{
  "total_scans_run": 42,
  "active_jobs": 1
}
```
