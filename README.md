# WayTrace

**English** . [Français](README.fr.md)

> **The archive never forgets.**

Passive OSINT reconnaissance that reconstructs the complete digital history of any domain from the Wayback Machine (archive.org). Enter a domain. WayTrace pulls archived HTML across decades, selects the most revealing snapshots, and extracts **43 categories** of intelligence. Every finding carries `first_seen` / `last_seen` timestamps, so you get a full timeline of what appeared, changed, and disappeared.

**No active scanning. No brute-forcing. No traffic to the target. Only public data from archive.org.**

[![Live at waytrace.org](https://img.shields.io/badge/live-waytrace.org-6f5bd6)](https://waytrace.org)
![MIT License](https://img.shields.io/badge/license-MIT-blue)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)

---

## Try it

- **Hosted:** [**waytrace.org**](https://waytrace.org) - run a scan in the browser, nothing to install.
- **Self-hosted:** clone and `docker compose up` (see [Quick start](#quick-start)). Running it yourself removes the hosted per-scan snapshot ceiling, so you can scan a domain in full.

The interface is fully bilingual (English / French), switchable from the navbar.

---

## What's new in v1.2.0

- **Full-text search over page content.** Search any word across a scan's archived pages (not only the extracted pivots), with highlighted excerpts and links to the Wayback capture. Accent-insensitive.
- **Single, simpler scan pipeline** and a round of **security hardening** (ReDoS fix, spoof-resistant client-IP, domain-scoped snapshots, production secret guard) and **reliability** (backs off on archive.org connection throttling, not only HTTP 429).
- **Accessibility & privacy.** WCAG-AA contrast, keyboard-operable favicon tiles, and the Google favicon fallback removed (it leaked the investigated domain — only archive.org is contacted).

### Earlier, in v1.1.0

- **Private by default.** New scans are private; publishing to the public feed is an explicit opt-in.
- **Delete a scan.** Remove a scan entirely, from your list and from the public feed.
- **Favicon hashes for pivoting.** Every favicon now carries an **MD5**, a **SHA-256**, and the **Shodan `http.favicon.hash`** value (MurmurHash3 of the base64-encoded icon), so identical icons can be pivoted across hosts on Shodan and Censys.
- **Sharper classification.** `fb.com` and social-profile URLs are routed to Social profiles (never mistaken for named persons); social links found among outgoing links surface under Social profiles and are de-duplicated. A round of extractor QA removed many false positives (look-alike domains, documentation-placeholder tracker IDs, prose brand mentions, template-literal URLs, date-format strings) while closing recall gaps.
- **Precise advertising / tracker ids.** Findings keep their exact prefix and show a platform chip: AdSense (`ca-pub-`), AdMob (`ca-app-pub-`), Google Analytics (`UA-`/`G-`), GTM, Meta Pixel, and more.
- **More reliable scans.** The scraper now recognises archive.org's connection-level throttling (not only HTTP 429), backs off, and reports a per-outcome breakdown, so large scans no longer fail silently.

See [CHANGELOG.md](CHANGELOG.md) for the full list.

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
| **PIVOT** | A lead worth chasing | named mailboxes, subdomains, admin/auth endpoints, public API keys, persons, GitHub repos, business IDs |
| **CONTEXT** | Useful background | tech stack, analytics trackers, hosting/CDN, organisations, HTTP headers |
| **BACKGROUND** | Listed for completeness, never highlighted | meta tags, titles, assets, outgoing links, comments |

LEAK and PIVOT are promoted to the top of the results; CONTEXT and BACKGROUND stay one click away.

---

## Results interface

Results open as a single page with a tabbed intelligence block:

- **Activity** - one timeline lane per category on a shared year axis; click a lane to expand a per-value gantt and see when each entity was live.
- **Pivots** - a radial graph linking the domain to its emails, subdomains, persons, orgs, social, GitHub, trackers, favicons, and hosting.
- **Subdomains** - ranked by occurrences with their active period.
- **Tech & infra** - stack, hosting/CDN, and HTTP headers with first/last seen.

Across every tab: a **global search** (filters all tabs at once), **sortable columns**, **one-click column copy** (e.g. every email), and **export** to JSON, CSV (current tab), or all categories at once. The whole UI is bilingual (EN / FR).

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

### Shared scans

`GET /api/s/{url_id}` (view), `POST /api/s/{url_id}/publish` (toggle public), and `GET /api/s/{url_id}/export.{json,csv,html}` (download). `GET /api/feed` lists published scans.

### GET /api/health

```json
{ "status": "ok", "uptime_seconds": 3842, "active_jobs": 1 }
```

---

## Configuration

All settings live in `.env` (copy from `.env.example`). Defaults are polite toward archive.org; raising concurrency or lowering the delays will get you rate-limited.

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_CONCURRENT_SCRAPES` | `8` | Parallel Wayback requests (1-50) |
| `ARCHIVE_REQUEST_TIMEOUT` | `60` | Per-request timeout (s) |
| `ARCHIVE_RETRY_COUNT` | `3` | Retries on CDX/Wayback transient errors |
| `SCRAPE_DELAY_MIN` | `0.25` | Min delay between requests (s) |
| `SCRAPE_DELAY_MAX` | `0.75` | Max delay between requests (s) |
| `SCRAPE_MAX_RETRIES` | `3` | Retries per page scrape |
| `JOB_TTL_SECONDS` | `7200` | Job expiry (2 hours) |
| `MAX_ACTIVE_JOBS` | `10` | Max concurrent scans |
| `SCAN_TIMEOUT_SECONDS` | `3600` | Hard timeout per scan (60 min) |
| `HOSTED_SNAPSHOT_CEILING` | `5000` | Per-scan snapshot ceiling; `0` disables it for self-hosted full scans |
| `CORS_ORIGINS` | `localhost:5173,3000` | Comma-separated allowed origins |
| `DATABASE_URL` | `/data/waytrace.db` | SQLite path (override outside Docker) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

An adaptive rate limiter (`RATE_LIMIT_*`) auto-increases the delay on 429s and recovers on a success streak; see `.env.example` for the full set.

---

## Architecture

```
backend/
  main.py                 FastAPI app, middleware, lifespan (TTL cleanup)
  config.py               Pydantic settings from .env
  models.py               Request/response schemas (Pydantic v2)
  db.py                   SQLite (aiosqlite) - crawl state, jobs, findings
  store.py                In-memory job index + fair queue for live progress
  routers/
    scan.py               POST /scan, POST /scan/preflight, GET /jobs/{id}, SSE
    public.py             Shared scans (/api/s/{url_id}), publish, exports, feed
    health.py             GET /health, GET /stats
  services/
    cdx.py                CDX client, HTML-only, paginated, gzip cache
    filters.py            Snapshot selection, path scoring, dedup, density
    scraper.py            Concurrent Wayback downloader, semaphore, backoff, budget
    extractor/            One module per category (43 total) + finalize/highlights

frontend/
  index.html              Single file, vanilla JS, dark theme, no build step,
                          bilingual EN/FR, tabbed results, search, export
tests/                    ~1200 tests: extraction, selection, API, regressions
```

**Stack:** Python 3.12+, FastAPI, aiohttp, selectolax, Pydantic v2, aiosqlite, loguru.

**Design notes:**

- **selectolax** over BeautifulSoup - C-based, ~10x faster for high-volume parsing.
- **Async throughout** - aiohttp for all network I/O, no blocking calls.
- **CDX server-side filtering** - request only `text/html` + `status:200`, never thousands of asset entries.
- **Adaptive rate limiting** - `asyncio.Semaphore` + jittered delay; backs off on 429, recovers on success.
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
