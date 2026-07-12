# WayTrace

**English** . [Français](README.fr.md)

> **The internet never forgets.**

Reconstruct the complete digital history of any domain from the Wayback Machine (archive.org). Enter a domain: WayTrace pulls archived HTML across decades, selects the most revealing snapshots, and extracts **43 categories** of intelligence — emails, subdomains, exposed secrets, tech stacks, people — each stamped with `first_seen` / `last_seen`, so you get a full timeline of what appeared, changed, and disappeared. You can even full-text search the archived page content itself.

**No active scanning. No brute-forcing. No traffic to the target. Only public data from archive.org.**

[![Live at waytrace.org](https://img.shields.io/badge/live-waytrace.org-6f5bd6)](https://waytrace.org)
[![tests](https://github.com/HXLLO/WayTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/HXLLO/WayTrace/actions/workflows/ci.yml)
![MIT License](https://img.shields.io/badge/license-MIT-blue)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)

---

## Try it

- **Hosted:** [**waytrace.org**](https://waytrace.org) - run a scan in the browser, nothing to install.
- **Self-hosted:** clone and `docker compose up` (see [Quick start](#quick-start)). Running it yourself removes the hosted per-scan snapshot ceiling, so you can scan a domain in full.

The interface is fully bilingual (English / French), switchable from the navbar.

---

## What's new in v1.3.0

- **Self-governing, IP-safe archive.org access.** Every request goes through a shared, *adaptive* rate governor (AIMD, like TCP congestion control: it creeps up while responses stay clean and halves on the first connection-refusal) plus a shared concurrency limit — so no number of parallel scans or users can push the server IP past archive.org's tolerance. A hard IP block is detected and backed off from immediately.
- **Full-text search over page content** (from v1.2.0): search any word across a scan's archived pages, not just the extracted pivots, with highlighted excerpts and links to the Wayback capture.
- **Leaner codebase & UX polish:** loading progress now tracks real pages scraped with a measured ETA, a bilingual archive.org status banner, self-describing result categories, and a lot of dead code removed.

See [CHANGELOG.md](CHANGELOG.md) for the full history (v1.0 → v1.3).

---

## Contents

- [How it works](#how-it-works)
- [The guided scan](#the-guided-scan)
- [Smart snapshot selection](#smart-snapshot-selection)
- [Extraction categories](#extraction-categories)
- [Findings & severity](#findings--severity)
- [Results interface](#results-interface)
- [Sharing & the public feed](#sharing--the-public-feed)
- [Quick start](#quick-start)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Tests](#tests)
- [Legal & ethics](#legal--ethics)

---

## How it works

```
  domain input
       |
       v
+---------------------------------------------------------------------+
|  Phase 1 - CDX query                                                 |
|  -------------------------------------------------------------------+
|  Hit the archive.org CDX API -> every archived HTML URL for domain   |
|  Filter: text/html, status 200, paginated (resumeKey)               |
|  Local gzip cache in data/cdx/ to avoid redundant network calls      |
|  Result: up to 50 000+ snapshot records with timestamps + digests    |
+--------------------------------+------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  Phase 2 - Smart snapshot selection                                  |
|  -------------------------------------------------------------------+
|  Score every URL path by OSINT value (HIGH / MEDIUM / LOW)           |
|  Deduplicate by CDX digest (drop identical content, keep earliest)   |
|  Spread picks year-proportionally so no era dominates                |
|  Enforce an adaptive cap based on domain size                        |
+--------------------------------+------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  Phase 3 - Scraping                                                  |
|  -------------------------------------------------------------------+
|  Fetch HTML from the Wayback Machine for each selected snapshot      |
|  Concurrent requests (semaphore) + adaptive delay, backoff on 429    |
|  Wall-clock budget: keep what is downloaded, never hang on stragglers|
|  Strip the Wayback toolbar/injected scripts before parsing           |
+--------------------------------+------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  Phase 4 - Extraction & aggregation                                  |
|  -------------------------------------------------------------------+
|  Parse with selectolax (C-based, ~10x faster than BeautifulSoup)     |
|  Run 43 extraction categories (regex + DOM + JSON-LD)               |
|  Aggregate first_seen / last_seen / occurrences, stamp source page   |
|  Rank findings by severity (LEAK > PIVOT > CONTEXT > BACKGROUND)     |
+--------------------------------+------------------------------------+
                                 |
                                 v
                    Structured OSINT results
                     with temporal metadata
```

---

## The guided scan

Every scan goes through a short, interactive scope step before any page is downloaded; no blind launches.

**Preflight (Phase 1 only).** A lightweight CDX query, no scraping. It returns the total snapshot count, unique paths, the archived date range, and a per-path snapshot browser.

**Scope page.** From the preflight you tune the run:

- **Snapshot histogram** over time; click two years to bound a range.
- **Month-precision calendar** for an exact `from -> to` window (month precision matches the granularity of Wayback data).
- **Density** - Fast (3/yr), Dense (12/yr, default), or Max (newest up to the ceiling).
- **Subdomain picker** - every subdomain found in the archive, individually selectable.
- **Exclude URLs** - keyword chips with presets (blog, tag, category, author, feed, ...).
- A **live estimate** of pages and time updates as you adjust.

When you launch, the selected snapshots are sent directly, bypassing a second CDX round-trip.

---

## Smart snapshot selection

Not all archived pages are worth the same. WayTrace scores each URL path:

| Score | Paths | Why |
|-------|-------|-----|
| **HIGH (3)** | `/contact`, `/about`, `/team`, `/staff`, `/people`, `/careers`, `/login`, `/admin`, `/press`, `/investors`, `/security`, `/partners`, `/privacy`, `/terms`, `/legal`, `/imprint`, `/blog` | Where emails, names, phones, and internal endpoints typically surface |
| **MEDIUM (2)** | Homepage `/` | Tracks branding, stack, and ownership changes over time |
| **LOW (1)** | Everything else | General content |

**Content deduplication.** CDX ships a SHA-1 digest per snapshot; snapshots with the same `path + digest` collapse to the earliest occurrence, so identical pages aren't scraped twice.

**Year-proportional spread.** Picks are distributed across the archived years rather than clustering on whichever period has the most captures, so a domain's whole history is represented.

**Adaptive cap.** The maximum page count scales with domain size. On the hosted service a per-scan ceiling (`HOSTED_SNAPSHOT_CEILING`, default 5000) keeps runs bounded; set it to `0` on a self-hosted install to scan in full.

---

## Extraction categories

43 categories, each finding tracked with `first_seen`, `last_seen`, and `occurrences`.

**People & contact**
`emails` . `phones` . `persons` . `social_profiles` . `pgp_keys`

**Secrets & exposures**
`api_keys` . `connection_strings` . `cloud_buckets` . `jwt_tokens` . `internal_ips` . `hidden_fields` . `directory_listings`

**Infrastructure & hosting**
`subdomains` . `hosting` . `http_headers` . `status_pages` . `favicons` . `sitemaps_and_robots`

**Tech & tracking**
`technologies` . `analytics_trackers` . `analytics_ids` . `adsense_ids` . `verification_tags` . `captcha_providers` . `cookie_consent` . `auth_providers`

**Identifiers & correlation**
`crypto_addresses` . `french_business_ids` . `github_repos` . `organizations` . `bug_bounty_programs` . `job_boards`

**Structure & content**
`endpoints` . `js_urls` . `iframe_sources` . `outgoing_links` . `linked_documents` . `rss_feeds` . `assets` . `html_comments` . `meta_info` . `html_titles` . `addresses`

A few worth calling out:

- **emails** - raw and obfuscated forms, `mailto:` links; noise like `noreply`, `example`, asset filenames, and JS module specifiers filtered out.
- **api_keys** - AWS, Google, Stripe, SendGrid, Slack webhooks, GitHub tokens, plus modern low-FP patterns (Supabase, DigitalOcean, Shopify, Linear, npm). Always treated as a leak.
- **cloud_buckets** - S3, GCS, Azure Blob, DigitalOcean Spaces URLs, often misconfigured public storage.
- **connection_strings** - MySQL, Postgres, Mongo, Redis, AMQP, MSSQL, and more; credentials masked in output.
- **subdomains** - dev / staging / api / internal hosts still referenced from old pages long after they go dark.
- **favicons** - per-snapshot icon with MD5/SHA-256 hashes, a cross-domain correlation vector.
- **analytics_trackers** - GA/GA4, GTM, Meta Pixel, Hotjar, Mixpanel and more; a shared ID across domains links them to one owner.

Every finding also records the **source page** it came from, so co-occurring entities (an email and a phone on the same archived page) can be pivoted together.

---

## Findings & severity

WayTrace ranks every result into four tiers and surfaces the important ones automatically:

| Tier | Meaning | Examples |
|------|---------|----------|
| **LEAK** | Sensitive exposure the owner didn't mean to publish | live API keys, exposed cloud buckets, connection strings with credentials, JWTs, internal IPs, directory listings |
| **PIVOT** | A lead worth chasing | named mailboxes, subdomains, admin/auth endpoints, persons, GitHub repos, business IDs, analytics & tracker IDs, favicon hashes |
| **CONTEXT** | Useful background | tech stack, hosting/CDN, HTTP headers, page titles & meta tags, organisations, linked documents |
| **BACKGROUND** | Listed for completeness, never highlighted | outgoing links, social profiles, asset files, HTML comments |

LEAK and PIVOT are promoted to the top of the results; CONTEXT and BACKGROUND stay one click away.

---

## Results interface

Results open as a single page with a tabbed intelligence block:

- **Activity** - one timeline lane per category on a shared year axis; click a lane to expand a per-value gantt and see when each entity was live.
- **Pivots** - a radial graph linking the domain to its emails, subdomains, persons, orgs, social, GitHub, trackers, favicons, and hosting.
- **Subdomains** - ranked by occurrences with their active period.
- **Tech & infra** - stack, hosting/CDN, and HTTP headers with first/last seen.

Across every tab: a **global search** (filters all tabs at once), **sortable columns**, **one-click column copy** (e.g. every email), and **export** to JSON, CSV (current tab), or all categories at once. There's also a **full-text search over the archived page content itself** — find any word inside the scraped pages, with highlighted excerpts and a link to the exact Wayback capture. The whole UI is bilingual (EN / FR).

Every result category is shown — including the ones with **zero findings** — each with a one-line description of what it detects, so you always see the full scope of what was searched, not just what was found.

---

## Sharing & the public feed

A finished scan is addressed by a 24-character `url_id` (a capability token). Scans are **private by default**; publishing to the public feed is an explicit opt-in, and a published scan can be unpublished or **deleted** at any time (which also removes it from the feed). Shared scans are viewable by anyone with the link and exportable as JSON, CSV, or a standalone HTML report. A self-hosted install runs fully open (no accounts).

---

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/HXLLO/WayTrace.git
cd WayTrace
cp .env.example .env
docker compose up -d
```

Open **http://localhost:8000**.

### Docker (development, hot reload)

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up
```

### Manual

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn main:app --reload
```

Open **http://localhost:8000**.

---

## API reference

Interactive Swagger docs at **http://localhost:8000/docs**.

### POST /api/scan/preflight

Lightweight CDX query; returns domain stats without scraping.

```bash
curl -X POST http://localhost:8000/api/scan/preflight \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.com"}'
```

```json
{
  "domain": "example.com",
  "total_snapshots": 47404,
  "html_snapshots": 12861,
  "unique_paths": 971,
  "date_range": { "first": "2003-08", "last": "2026-01" },
  "path_groups": [
    { "path": "/", "score": 2, "count": 412, "snapshots": [ ... ] },
    { "path": "/contact", "score": 3, "count": 89, "snapshots": [ ... ] }
  ]
}
```

### POST /api/scan

Create a scan. Returns immediately with a `job_id`; poll or stream for results. `config` is optional.

```bash
curl -X POST http://localhost:8000/api/scan \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "config": {
      "depth": "standard",
      "date_from": "2018-01",
      "date_to": null,
      "categories": ["emails", "subdomains", "api_keys", "phones"],
      "exclude_keywords": ["tag", "category"]
    }
  }'
```

Pass `selected_snapshots` (from preflight `path_groups`) to scrape exactly the pages you choose:

```json
{
  "domain": "example.com",
  "selected_snapshots": [
    { "timestamp": "20210615120000", "url": "https://example.com/contact" }
  ]
}
```

### GET /api/jobs/{job_id}

Poll status and retrieve results on completion.

```json
{
  "id": "3f8a2c1d-...",
  "status": "completed",
  "progress": 100,
  "meta": {
    "domain": "example.com",
    "total_snapshots_found": 12861,
    "snapshots_analyzed": 312,
    "pages_scraped": 298,
    "date_first_seen": "2003-08",
    "date_last_seen": "2026-01"
  },
  "results": {
    "highlights": [ { "severity": "LEAK", "category": "api_keys", "...": "..." } ],
    "emails": [ { "value": "ceo@example.com", "first_seen": "2009-03", "last_seen": "2021-11", "occurrences": 14 } ],
    "subdomains": [ "..." ]
  }
}
```

Status progression: `queued` -> `running` -> `completed` | `failed`.

### GET /api/jobs/{job_id}/stream

Server-Sent Events for real-time progress (preferred over polling). Events: `progress`, `complete`, `error`, `expired`; heartbeat every 15s.

### Shared scans & storage

Every scan is stored under a stable `url_id` and stays available for the retention window (7 days on the hosted build; configurable when self-hosted):

- `GET /api/s/{url_id}` — view a scan; `DELETE` to remove it; `POST /api/s/{url_id}/publish` to toggle public.
- `GET /api/s/{url_id}/search?q=…` — full-text search the scan's archived page content.
- `GET /api/s/{url_id}/export.{json,csv,html}` — download.
- `GET /api/feed` — recently published scans.
- `GET /api/local-scans` — **self-hosted only**: lists every scan this instance has run (published or private), so a solo user keeps and re-accesses all their scans from "My scans". Disabled on the hosted build, which scopes scans to accounts.

### GET /api/health

```json
{ "status": "ok", "uptime_seconds": 3842, "active_jobs": 1 }
```

---

## Configuration

All settings live in `.env` (copy from `.env.example`). Defaults are polite toward archive.org; raising concurrency or lowering the delays will get you rate-limited.

| Variable | Default | Description |
|----------|---------|-------------|
| `ARCHIVE_RATE_PER_MINUTE` | `90` | **Starting** archive.org request rate (req/min). The governor adapts it live. |
| `ARCHIVE_RATE_MIN` / `ARCHIVE_RATE_MAX` | `60` / `150` | Floor and ceiling the adaptive rate stays within (1 → 2.5 req/s) |
| `ARCHIVE_GLOBAL_CONCURRENCY` | `3` | Max simultaneous archive.org connections across all scans |
| `MAX_CONCURRENT_SCRAPES` | `4` | Per-scan parallel requests (1-50) |
| `SCRAPE_DELAY_MIN` / `SCRAPE_DELAY_MAX` | `0.5` / `1.2` | Per-request jitter (s) |
| `MAX_ACTIVE_TOTAL` | `2` | Scans running at once; the rest queue |
| `ARCHIVE_REQUEST_TIMEOUT` | `60` | Per-request timeout (s) |
| `HOSTED_SNAPSHOT_CEILING` | `5000` | Per-scan snapshot ceiling; `0` disables it for self-hosted **full** scans |
| `SCAN_RETENTION_DAYS` | `7` | How long a stored scan stays retrievable |
| `IS_PRODUCTION` | `0` | `1` in prod: refuses to boot with the default `SECRET_KEY` |
| `DATABASE_URL` | `/data/waytrace.db` | SQLite path (override outside Docker) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

**About the rate governor.** archive.org publishes no scraping limit and its tolerance is dynamic, so WayTrace doesn't guess a fixed number: it starts conservative, nudges the rate up while responses stay clean, and *halves it the instant archive.org refuses a connection* (AIMD, like TCP congestion control). This keeps the server IP off archive.org's block list no matter how many scans or users run at once. Raising the ceilings speeds scans up at your own risk. See `.env.example` for the full set.

---

## Architecture

```
backend/
  main.py                 FastAPI app, middleware, lifespan (TTL cleanup)
  config.py               Pydantic settings from .env
  models.py               Request/response schemas (Pydantic v2)
  db.py                   SQLite (aiosqlite) - scans + FTS5 page-content index
  store.py                In-memory job index + fair queue for live progress
  routers/
    scan.py               POST /scan, POST /scan/preflight, GET /jobs/{id}, SSE
    public.py             Shared scans (/api/s/{url_id}), search, exports, feed
    health.py             GET /health, GET /archive-status, GET /stats
  services/
    cdx.py                CDX client, HTML-only, paginated, gzip cache
    filters.py            Snapshot selection, path scoring, dedup, density
    scraper.py            Concurrent Wayback downloader, budget, backoff
    archive_rate.py       Shared adaptive (AIMD) rate + concurrency governor
    archive_health.py     Circuit breaker: throttle + hard IP-block detection
    extractor/            One module per category (43 total) + finalize/highlights

frontend/                 index.html + styles.css + app.js - vanilla JS, no
                          build step, dark/light, bilingual EN/FR, tabbed results
tests/                    1200+ tests: extraction, selection, API, anti-block, regressions
```

**Stack:** Python 3.12+, FastAPI, aiohttp, selectolax, Pydantic v2, aiosqlite, loguru.

**Design notes:**

- **selectolax** over BeautifulSoup - C-based, ~10x faster for high-volume parsing.
- **Async throughout** - aiohttp for all network I/O, no blocking calls.
- **CDX server-side filtering** - request only `text/html` + `status:200`, never thousands of asset entries.
- **Adaptive, IP-safe rate governor** - one shared token bucket whose rate self-tunes (AIMD) across every archive.org call, plus a shared concurrency cap and a circuit breaker that tells a hard IP block from ordinary throttling. Keeps the server IP off the block list under any load.
- **Scrape time budget** - a slow archive.org never hangs a scan; downloaded pages are kept and analysed even if stragglers are dropped.
- **Per-finding provenance** - every entity is stamped with the source page for co-occurrence pivots.

---

## Tests

```bash
cd backend
python -m pytest tests/ -q                      # full suite
python -m pytest tests/test_extractor.py -q     # core extraction patterns
python -m pytest tests/test_filters.py -q       # snapshot selection
python -m pytest tests/test_api.py -q           # API endpoints
```

Each extraction category ships dedicated positive and false-positive tests (minimum five of each), alongside API validation, job-lifecycle, selection-algorithm, and end-to-end integration tests.

---

## Legal & ethics

WayTrace queries **only public archives** from the Wayback Machine (archive.org). It performs no active scanning, port scanning, brute-forcing, DNS enumeration, or any intrusive action against target systems.

- Intended for legitimate security research, OSINT investigations, due diligence, and competitive intelligence.
- Do not use it for harassment, stalking, or any illegal activity.
- You are solely responsible for how you use the extracted data.
- Respect [archive.org's terms](https://archive.org/about/terms.php); do not flood requests or attempt to bypass rate limits.

Abuse reports and removal requests: [legal@waytrace.org](mailto:legal@waytrace.org).

---

## License

[MIT](LICENSE)
