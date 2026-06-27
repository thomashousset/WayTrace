# Frontend Integration Guide

This document describes everything the frontend developer needs to integrate with the WayTrace backend API.

## Getting Started

### Start the backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

The API runs on `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### CORS
Already configured for:
- `http://localhost:5173` (Vite default)
- `http://localhost:3000` (CRA / Next.js default)

No additional configuration needed for local development.

---

## API Endpoints

### 1. Start a scan

```
POST /api/scan
Content-Type: application/json

{ "domain": "example.com" }
```

**Response:**
```json
{ "job_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Error codes:**
- `422`: Invalid domain
- `429`: Too many active jobs (max 10)

### 2. Poll job status

```
GET /api/jobs/{job_id}
```

**Polling strategy:**
- Poll every **2 seconds**
- Stop polling when `status` is `completed` or `failed`
- Show `step` field as a progress message to the user
- Use `progress` (0-100) for a progress bar

### 3. Health check

```
GET /api/health
```

---

## Job Lifecycle

```
queued → running → completed
                 → failed
```

| Status | Meaning |
|---|---|
| `queued` | Job accepted, waiting to start |
| `running` | Scan in progress, check `progress` and `step` |
| `completed` | Done. `meta` and `results` fields are populated |
| `failed` | Error occurred. `step` contains error message |

Jobs expire after **2 hours** from last update.

---

## Results Structure

When `status === "completed"`, the response includes:

### `meta` object
```json
{
  "domain": "example.com",
  "total_snapshots_found": 58420,
  "snapshots_analyzed": 147,
  "date_first_seen": "2003-08",
  "date_last_seen": "2024-11",
  "scan_duration_seconds": 847.2
}
```

### `results` object

All arrays share a common entity format:
```json
{
  "first_seen": "YYYY-MM",
  "last_seen": "YYYY-MM",
  "occurrences": 47
}
```

**Date format:** `first_seen` and `last_seen` are always `YYYY-MM` strings.

**Result arrays:**

| Key | Extra fields | Description |
|---|---|---|
| `endpoints` | `path` | Internal URL paths found |
| `outgoing_links` | `domain` | External domains linked to |
| `emails` | `value` | Email addresses found |
| `phones` | `raw`, `normalized` | Phone numbers (original + digits only) |
| `subdomains` | `value` | Subdomains discovered |
| `analytics_trackers` | `type`, `id` | GA, GTM, Meta Pixel, etc. |
| `social_profiles` | `platform`, `handle`, `url` | Social media accounts |
| `persons` | `name`, `context` | People names (from meta, byline, JSON-LD) |
| `technologies` | `technology`, `version` | CMS / framework detected |
| `built_with` | `value` | "Built with X" / "Powered by X" mentions |

All arrays are **sorted by `occurrences` descending** (most frequent first).

---

## Example: Minimal Frontend Flow

```javascript
// 1. Start scan
const { job_id } = await fetch('/api/scan', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ domain: 'example.com' })
}).then(r => r.json());

// 2. Poll until done
const poll = setInterval(async () => {
  const job = await fetch(`/api/jobs/${job_id}`).then(r => r.json());

  updateProgressBar(job.progress);
  updateStatusText(job.step);

  if (job.status === 'completed' || job.status === 'failed') {
    clearInterval(poll);
    if (job.status === 'completed') {
      renderResults(job.meta, job.results);
    } else {
      showError(job.step);
    }
  }
}, 2000);
```

---

## Notes

- The backend is stateless between restarts (in-memory store)
- Duplicate scans for the same domain return the existing job (no wasted requests)
- Tracker IDs (`analytics_trackers`) can be used for cross-domain pivoting (same GA ID = same owner)
- The `context` field on `persons` indicates where the name was found: `meta:author`, `html:class`, or `json-ld`
