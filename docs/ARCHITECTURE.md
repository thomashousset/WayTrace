# WayTrace Architecture

## Overview

```
Client (Browser / curl)
    │
    ▼
┌─────────────────────────────────────┐
│  FastAPI (main.py)                  │
│  CORS + Swagger UI on /docs         │
├─────────────────────────────────────┤
│  Routers                            │
│  ├── scan.py                        │
│  │   POST /api/scan/preflight       │
│  │   POST /api/scan                 │
│  │   GET  /api/jobs/{id}            │
│  └── health.py                      │
│      GET  /api/health               │
│      GET  /api/stats                │
├─────────────────────────────────────┤
│  Job Store (store.py)               │
│  In-memory, asyncio.Lock            │
│  TTL cleanup every 10 min           │
├─────────────────────────────────────┤
│  Scan Pipeline (run_scan)           │
│  ┌───────────────────────────┐      │
│  │ 1. CDX API (cdx.py)       │      │
│  │    HTML-only, paginated    │      │
│  │    ↓                       │      │
│  │ 2. Smart Filter            │      │
│  │    (filters.py)            │      │
│  │    Priority scoring + cap  │      │
│  │    ↓                       │      │
│  │ 3. Concurrent Scrape       │      │
│  │    (scraper.py)            │      │
│  │    Semaphore + delays      │      │
│  │    ↓                       │      │
│  │ 4. OSINT Extract           │      │
│  │    (extractor.py)          │      │
│  │    18 extraction categories│      │
│  └───────────────────────────┘      │
└─────────────────────────────────────┘
    │
    ▼
archive.org (CDX API + Wayback Machine)
```

## Scan Pipeline

1. **CDX Query**: Fetch all archived HTML URLs from archive.org CDX API (server-side filtered to `mimetype:text/html`, paginated via resumeKey for large domains)
2. **Smart Filter**: Score each path by OSINT value, apply depth preset and date range, select diverse snapshots within cap budget
3. **Concurrent Scrape**: Download HTML from Wayback Machine with semaphore-controlled concurrency + random delays
4. **OSINT Extract**: Parse each page for 18 categories of intelligence data using regex + DOM parsing via selectolax

## Preflight Flow

Before a full scan, clients can call `/api/scan/preflight` which runs only step 1 (CDX query) and returns domain statistics + a suggested scan config. This allows users to see how large a domain is and adjust depth/date range/cap before committing to the full pipeline.

## Smart Filtering Strategy

Snapshots are selected based on path priority scoring:

| Score | Path type | Sampling |
|-------|-----------|----------|
| 3 (high) | OSINT paths: contact, about, team, careers, login, admin, blog... | First + last + 1/semester |
| 2 (medium) | Homepage `/` | 1 per month |
| 1 (low) | Everything else | First + last (+ 1/year if 3+ snapshots) |

Cap is computed adaptively based on unique paths and available HTML count:
- Small sites (≤30 paths): scan almost everything, up to 500
- Medium (31-200 paths): ~5 snapshots/path, up to 600
- Large (201-1000 paths): ~2 snapshots/path, up to 800
- Very large (1000+): hard cap at 800

Depth presets multiply the cap: quick (×0.3), standard (×1.0), full (×1.5).

## Data Flow

- All state is in-memory (no database)
- Jobs expire after 2 hours (configurable via `JOB_TTL_SECONDS`)
- Jobs running > 25 minutes are auto-failed
- Duplicate scans for the same domain return the existing job ID

## Key Design Decisions

- **No database**: Keeps deployment to a single container. Trade-off: data lost on restart
- **Async everything**: FastAPI + aiohttp for non-blocking I/O throughout
- **HTML-only CDX filter**: Server-side `mimetype:text/html` filter avoids downloading tens of thousands of asset entries
- **Rate limiting archive.org**: Semaphore + random delays between requests to avoid 429s
- **selectolax**: C-based HTML parser, significantly faster than BeautifulSoup
- **Preflight step**: Cheap CDX-only call lets users make informed decisions before expensive scraping
