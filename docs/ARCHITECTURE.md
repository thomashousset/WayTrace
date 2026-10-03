# WayTrace Architecture

## Overview

```
Client (Browser / curl)
    │
    ▼
┌──────────────────────────────────────────────┐
│  FastAPI (main.py)                           │
│  Serves the single-file frontend + the API   │
│  CORS, CSP, body-size cap, cache headers     │
├──────────────────────────────────────────────┤
│  Routers                                     │
│  ├── scan.py       preflight, scan, jobs     │
│  ├── health.py     health, stats, status     │
│  ├── public.py     /s/{url_id}, search,      │
│  │                 exports, local scans      │
│  └── selfhost_config.py   /api/config        │
├──────────────────────────────────────────────┤
│  State                                       │
│  ├── store.py   in-memory job index + queue  │
│  └── db.py      SQLite (jobs, findings,      │
│                 page FTS, app_state KV)      │
├──────────────────────────────────────────────┤
│  Scan Pipeline (run_scan)                    │
│  ┌────────────────────────────────┐          │
│  │ 1. CDX index      (cdx.py)     │          │
│  │ 2. Smart filter   (filters.py) │          │
│  │ 3. Scrape         (scraper.py) │          │
│  │ 4. Extract        (extractor/) │          │
│  └────────────────────────────────┘          │
└──────────────────────────────────────────────┘
    │
    ▼
archive.org (CDX API + Wayback Machine)
```

## Scan Pipeline

1. **CDX query** (`services/cdx.py`): fetch the archived HTML URLs for the
   domain from the archive.org CDX API (server-side `mimetype:text/html`
   filter, paginated via resumeKey for large domains, cached on disk).
2. **Smart filter** (`services/filters.py`): score each path by OSINT value,
   apply the depth preset and date range, and select a diverse,
   year-proportional set of snapshots within the cap budget.
3. **Concurrent scrape** (`services/scraper.py`): download the selected pages
   from the Wayback Machine under a process-wide concurrency cap, with jitter
   between requests. Extraction overlaps with the download, so findings start
   appearing while pages are still coming in.
4. **OSINT extraction** (`services/extractor/`): a package with one module per
   category, 48 categories total, orchestrated by `extract.py` and
   `finalize.py`. Parsing uses regex + selectolax DOM. Every extracted entity
   carries `first_seen`, `last_seen` and `occurrences`; `highlights.py` ranks
   the notable findings.

## archive.org Politeness

The request rate is driven by an adaptive AIMD governor
(`services/archive_rate.py`): it starts low, creeps up while responses stay
clean and halves the moment archive.org refuses a connection, with an
escalating cooldown on repeated refusals (`services/archive_health.py` acts as
a circuit breaker). Every instance sends a stable, identifiable User-Agent
(`services/identity.py`) with a link to this project and an optional operator
contact.

## State and Persistence

- `store.py` keeps an in-memory index of live jobs for cheap progress polling,
  plus the scan queue (one scan runs at a time by default; the rest wait).
- `db.py` persists everything in SQLite: finished scans and their findings,
  full-text search over the scraped pages, and an `app_state` key/value table
  (instance settings, runtime config overrides).
- The queue is restart-proof: queued and running jobs persist their submission
  and are re-enqueued on startup under the same `job_id` / `url_id`.
- A finished scan is addressed by its `url_id`, a 24-char random token
  generated server-side.
- Scans expire after `SCAN_RETENTION_DAYS` (14 by default, 0 keeps them
  forever). Within that window, re-submitting the same domain returns the
  existing scan unless `force` is set.

## Frontend

A single-page vanilla JS app (`frontend/index.html`, `app.js`, `styles.css`)
served by the backend itself: no build step, no framework. Progress is polled
every 2 seconds; the report offers filtering, full-text search and JSON / CSV /
standalone-HTML export. The UI is bilingual (English / French) and themeable.

## Key Design Decisions

- **SQLite over a database server**: one container, one volume, no extra
  dependency. WAL journal, accessed via `aiosqlite`.
- **Async everything**: FastAPI + aiohttp, no blocking calls in the event loop.
- **HTML-only CDX filter**: the server-side `mimetype:text/html` filter avoids
  paging through tens of thousands of asset entries.
- **Adaptive rate control**: an AIMD governor instead of fixed delays, so the
  scan is as fast as archive.org tolerates that day, and no faster.
- **selectolax**: C-based HTML parser, significantly faster than
  BeautifulSoup.
- **Preflight step**: a cheap CDX-only call (`/api/scan/preflight`) lets the
  user see how large a domain is and tune depth / dates / cap before the real
  scan.
