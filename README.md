# WayTrace

**English** · [Français](README.fr.md)

WayTrace reconstructs the public history of a domain from the Wayback Machine (archive.org). You give it a domain; it reads the HTML that archive.org already saved over the years, picks the most revealing snapshots across time, and pulls out **48 categories** of intelligence, from emails and subdomains to exposed secrets, tech stacks and people. Every finding is stamped with when it `first_seen` and `last_seen` in the archive, so you get a timeline of what appeared, changed, and disappeared, not just a snapshot of today.

**It never touches the target.** No port scan, no brute force, no DNS enumeration, no traffic of any kind to the domain itself. Every byte comes from archive.org's public archive. The target never sees you.

[![Live at waytrace.org](https://img.shields.io/badge/live-waytrace.org-6f5bd6)](https://waytrace.org)
[![tests](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml)
![MIT License](https://img.shields.io/badge/license-MIT-blue)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)

## Try it

- **Hosted:** [**waytrace.org**](https://waytrace.org) runs a scan in your browser, nothing to install.
- **Self-hosted:** clone and `docker compose up` (see [Quick start](#quick-start)). The self-hosted build has no accounts, no per-scan snapshot ceiling, and a Settings page that exposes every scan and archive.org option, so you can scan a domain in full and tune the tool to your machine.

The interface is fully bilingual (English and French), switchable from the navbar.

## Contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [The guided scan](#the-guided-scan)
- [What it extracts](#what-it-extracts)
- [Findings and provenance](#findings-and-provenance)
- [The report](#the-report)
- [Settings (self-hosted)](#settings-self-hosted)
- [Configuration](#configuration)
- [API](#api)
- [Architecture](#architecture)
- [Tests](#tests)
- [Legal and ethics](#legal-and-ethics)
- [License](#license)

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/thomashousset/WayTrace.git
cd WayTrace
cp .env.example .env
docker compose up -d
```

Open **http://localhost:8000**. The default compose file binds to `127.0.0.1` only; put a reverse proxy in front to expose it.

### Docker (development, hot reload)

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up
```

### Without Docker

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn main:app --reload
```

Open **http://localhost:8000**. The database defaults to `waytrace.db` at the project root; nothing else to configure.

## How it works

A scan is a four-phase pipeline. Only phases 3 and 4 touch archive.org for content; phase 1 is a single index query and phase 2 is pure local computation.

```
  domain
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  1 · Index (CDX)                                                     │
│  Ask archive.org's CDX API for every archived HTML URL of the domain│
│  Keep text/html + status 200, paginated; gzip-cached in data/cdx/   │
│  → up to tens of thousands of snapshot records (timestamp + digest) │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  2 · Select                                                         │
│  Score each URL path by OSINT value (high / medium / low)           │
│  Drop identical captures by content digest, keep the earliest       │
│  Spread picks year-proportionally so no era dominates               │
│  Cap the count adaptively to the domain's size                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  3 · Fetch                                                          │
│  Download the selected captures from the Wayback Machine            │
│  Adaptive rate + shared concurrency limit, back off on refusal      │
│  Time budget: keep what is downloaded, never hang on stragglers     │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  4 · Extract                                                        │
│  Parse with selectolax (C-based), run 48 category extractors        │
│  (regex + DOM + JSON-LD), aggregate first_seen / last_seen /        │
│  occurrences, and stamp each finding with its source capture        │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
              Structured results with a full timeline
```

## The guided scan

Nothing is downloaded blindly. Every scan starts with a lightweight scope step.

**Preflight** runs phase 1 only: a CDX query, no scraping. It returns the total snapshot count, the unique paths, the archived date range, and a per-path snapshot browser.

**Scope** lets you shape the run before it fetches anything:

- a **snapshot histogram** over time; click two years to bound a range;
- a **month-precision calendar** for an exact `from → to` window (month precision matches Wayback's granularity);
- **density**: Fast (few per year), Dense (default), or Max (as much as the cap allows);
- a **subdomain picker**: every subdomain seen in the archive, each selectable;
- **exclude by keyword**: chips with presets (blog, tag, category, author, feed…);
- a **live estimate** of pages and time that updates as you adjust.

When you launch, the exact snapshots you chose are sent straight to phase 3, skipping a second index round-trip.

## What it extracts

48 categories. Every finding tracks `first_seen`, `last_seen`, and `occurrences`, and records the archived page it came from.

**People and contact**
`emails` · `phones` · `persons` · `organizations` · `addresses` · `social_profiles`

**Secrets and exposure**
`api_keys` · `connection_strings` · `cloud_buckets` · `jwt_tokens` · `crypto_addresses` · `internal_ips` · `hidden_fields` · `directory_listings` · `pgp_keys`

**Infrastructure and hosting**
`subdomains` · `hosting` · `http_headers` · `favicons` · `technologies` · `status_pages`

**Tracking and identifiers**
`analytics_trackers` · `analytics_ids` · `adsense_ids` · `verification_tags` · `cookie_consent` · `captcha_providers` · `auth_providers` · `github_repos` · `bug_bounty_programs` · `job_boards` · `french_business_ids`

**Business and SaaS integrations**
`support_chat` · `email_marketing` · `payment_processors` · `cdn_accounts` · `mobile_apps`

**Structure and content**
`endpoints` · `js_urls` · `iframe_sources` · `outgoing_links` · `linked_documents` · `assets` · `sitemaps_and_robots` · `rss_feeds` · `html_comments` · `meta_info` · `html_titles`

A few worth calling out:

- **api_keys** covers AWS, Google, Stripe, SendGrid, Slack webhooks, GitHub tokens, and modern low-false-positive patterns (Supabase, DigitalOcean, Shopify, Linear, npm). Always treated as a leak.
- **support_chat / email_marketing / payment_processors / cdn_accounts** pull the tenant or account ID out of third-party SaaS embeds (Intercom app_id, HubSpot portal, Shopify store, Cloudinary cloud, Algolia app...). The same ID on another domain ties both sites to one operator.
- **mobile_apps** links the domain to its iOS / Android apps via app-banner and App Links meta tags and store links, pivoting to the store listing (developer, reviews, version history).
- **cloud_buckets** finds S3, GCS, Azure Blob and DigitalOcean Spaces URLs, the usual home of misconfigured public storage.
- **connection_strings** matches MySQL, Postgres, Mongo, Redis, AMQP, MSSQL and more; credentials are masked in the output.
- **subdomains** surfaces dev / staging / api / internal hosts still referenced by old pages long after they went dark.
- **favicons** hashes each icon (MD5 and SHA-256), a cross-domain correlation vector.
- **analytics_trackers** catches GA/GA4, GTM, Meta Pixel, Hotjar, Mixpanel and others; a shared tracking ID across domains ties them to one owner.

Because every finding records its **source capture**, entities that co-occur on the same archived page (an email and a phone number, a person and an address) can be pivoted together.

### Severity

Findings are sorted into four tiers so the signal rises to the top, but nothing is hidden:

| Tier | Meaning |
|------|---------|
| **LEAK** | A real sensitive exposure the owner did not mean to publish. |
| **PIVOT** | A lead worth chasing; it points to linked entities. |
| **CONTEXT** | Useful background for understanding the target. |
| **BACKGROUND** | Listed for completeness, never highlighted. |

## Findings and provenance

WayTrace does not tell you what is "important" and does not score findings by hype. It shows you the evidence and lets you judge. Every finding carries:

| Field | What it tells you |
|-------|-------------------|
| **first seen / last seen** | when the value appeared in the archive, and when it was last present (so you see what is live versus gone) |
| **occurrences** | how many archived pages it showed up on |
| **source page** | the exact Wayback capture it came from, one click to verify |

Categories that found something are surfaced first. The empty ones stay visible too, so a clean result reads as "we looked and found nothing", not "we didn't look".

## The report

The result is a single page with two views you switch between.

**Categories** (default) is a rail of all 48 categories, the ones with findings first (with counts), the empty ones present but collapsed. You open one category at a time; its panel shows the findings (value, occurrences, first/last-seen, link to the source capture) and, below, its own activity: a lane per value showing when it appeared and disappeared, plus a dated change feed. "Show all" flattens every found category at once.

**Activity** lets you tick categories and individual pivots (a specific subdomain, tracker, favicon or person) to compose a shared timeline on one year axis, so overlaps and disappearances read at a glance. It includes the favicon-evolution gallery and a global change feed.

Two searches sit at the top, deliberately distinct: **filter the extracted findings** (instant, client-side) and **full-text search the archived page content** (any word inside the scraped HTML, with highlighted excerpts and a link to the exact capture). Every value is copyable, and you can **export** the whole scan to JSON, CSV, or a standalone HTML report.

A finished scan is addressed by a 24-character `url_id`, a capability token: knowing the link is enough to view or export it. Scans are private; there is no public feed and no accounts. On a self-hosted install, **My scans** lists every scan the instance has run, with its exact start time and how long it took, so you keep your whole history locally.

## Settings (self-hosted)

The self-hosted build ships a **Settings** page (in the navbar) that turns every scan and archive.org option into a form. It is the panel you would otherwise edit in `.env`, made live:

- every setting grouped by pipeline stage (archive.org politeness, snapshot selection, scans and queue, advanced), each with its description, unit, and recommended value;
- changes apply only when you click the per-setting **Save**, so nothing is committed by accident;
- risk zones instead of hard caps: a value can be pushed anywhere technically valid, but the orange zone is flagged aggressive and the red zone warns of a real chance archive.org blocks your IP;
- the few settings that need a restart offer a one-click **Restart now**;
- scan retention accepts an infinite (∞) option, so a self-hosted install can keep every scan forever.

On the hosted service these limits stay locked; the panel is a self-hosting feature.

## Configuration

Settings live in `.env` (copy `.env.example`), and unknown variables are ignored, so a leftover from an old release never blocks a boot. Every value below can also be changed live from the Settings page. The defaults are deliberately polite to archive.org; raising concurrency or lowering the delays is what gets an IP rate-limited.

| Variable | Default | Description |
|----------|---------|-------------|
| `ARCHIVE_RATE_PER_MINUTE` | `75` | **Starting** archive.org request rate (req/min); the governor adapts it live |
| `ARCHIVE_RATE_MIN` / `ARCHIVE_RATE_MAX` | `60` / `80` | Floor and ceiling the adaptive rate stays within |
| `ARCHIVE_GLOBAL_CONCURRENCY` | `3` | Max simultaneous archive.org connections across all scans |
| `MAX_CONCURRENT_SCRAPES` | `4` | Per-scan parallel requests (1 to 50) |
| `SCRAPE_DELAY_MIN` / `SCRAPE_DELAY_MAX` | `0.5` / `1.2` | Per-request jitter (s) |
| `MAX_ACTIVE_TOTAL` | `1` | Scans running at once; the rest queue |
| `MAX_QUEUE_TOTAL` | `100` | Cap on running plus waiting scans |
| `MAX_ACTIVE_PER_IP` | `2` | In-flight scans per client IP |
| `ARCHIVE_REQUEST_TIMEOUT` | `60` | Per-request timeout (s) |
| `HOSTED_SNAPSHOT_CEILING` | `3000` | Per-scan snapshot cap; `0` removes it, for full self-hosted scans |
| `SNAPSHOT_CAP_MULTIPLIER` | `1.0` | Scales the adaptive snapshot cap before the depth preset |
| `SCAN_RETENTION_DAYS` | `14` | How long a stored scan is kept and reused; `0` keeps them forever |
| `DATABASE_URL` | `<repo>/waytrace.db` | SQLite path; Docker images set `/data/waytrace.db` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `EXPOSE_API_DOCS` | `0` | `1` serves the interactive API docs at `/api/docs` |

**The rate governor.** archive.org publishes no scraping limit and its tolerance shifts, so WayTrace does not guess a fixed number. One shared token bucket starts conservative, nudges the rate up while responses stay clean, and halves it the instant archive.org refuses a connection (AIMD, like TCP congestion control). Combined with the shared concurrency cap and a circuit breaker that tells a hard IP block from ordinary throttling, this keeps the server IP off archive.org's block list no matter how many scans or users run at once. See `.env.example` for the full set.

## API

The HTTP API is the same one the frontend uses. Interactive docs are served at `/api/docs` when `EXPOSE_API_DOCS=1`. Full endpoint reference: [docs/API.md](docs/API.md).

**Scanning**

- `POST /api/scan/preflight`: CDX query only; returns domain stats without scraping.
- `POST /api/scan`: start a scan; returns a `job_id` immediately. Accepts a `config` (depth, date range, categories, exclude keywords) or an explicit `selected_snapshots` list from preflight.
- `GET /api/jobs/{job_id}`: poll status and, on completion, the results.
- `GET /api/jobs/{job_id}/stream`: Server-Sent Events for live progress (`progress`, `complete`, `error`).

**Scans**

- `GET /api/s/{url_id}`: view a stored scan; `DELETE` to remove it.
- `GET /api/s/{url_id}/search?q=…`: full-text search the scan's archived page content.
- `GET /api/s/{url_id}/export.{json,csv,html}`: download the scan.
- `GET /api/local-scans`: every scan this instance has run (self-hosted "My scans").

**Service**

- `GET /api/health`: `{ "status": "ok", "version": "1.8.0", "uptime_seconds": …, "active_jobs": … }`
- `GET /api/service-status`: queue depth, archive.org health, rolling scan count.
- `GET /api/config`, `PUT /api/config`, `POST /api/config/reset`, `POST /api/config/restart`: the Settings panel (self-hosted only).

Example:

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
  "date_range": { "first": "2003-08", "last": "2026-01" }
}
```

## Architecture

```
backend/
  main.py             FastAPI app, middleware, lifespan (queue restore, cleanup)
  config.py           Pydantic settings from .env (unknown vars ignored)
  models.py           Request/response schemas (Pydantic v2)
  db.py               SQLite (aiosqlite): scans + FTS5 page-content index
  store.py            In-memory job index + restart-proof fair queue
  routers/
    scan.py           /scan, /scan/preflight, /jobs/{id}, SSE stream
    public.py         stored scans (/s/{url_id}), search, exports, my scans
    health.py         /health, /service-status, /stats
    selfhost_config.py  the Settings panel API (/config)
  services/
    cdx.py            CDX client, HTML-only, paginated, gzip cache
    filters.py        snapshot selection: path scoring, dedup, density
    scraper.py        concurrent Wayback downloader, time budget, backoff
    archive_rate.py   shared adaptive (AIMD) rate + concurrency governor
    archive_health.py circuit breaker: throttle vs hard IP-block detection
    runtime_config.py live-tunable registry behind the Settings panel
    extractor/        one module per category (48) + finalize + highlights

frontend/             index.html + styles.css + app.js: vanilla JS, no build
                      step, dark/light themes, bilingual EN/FR, two-view report
tests/                ~1250 tests across ~80 files: extraction, selection,
                      API, anti-block, regressions
```

**Stack:** Python 3.12+, FastAPI, aiohttp, selectolax, Pydantic v2, aiosqlite, loguru.

A longer walkthrough of the pipeline and the design decisions lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Design choices worth knowing:

- **selectolax** over BeautifulSoup: a C-based parser, roughly ten times faster for high-volume HTML.
- **Async throughout**: aiohttp for all network I/O, no blocking calls.
- **CDX filtering server-side**: request only `text/html` and `status:200`, never thousands of asset rows.
- **One IP-safe rate governor**: a single shared, self-tuning token bucket across every archive.org call, plus a shared concurrency cap and a circuit breaker, so no amount of load pushes the IP onto the block list.
- **A scrape time budget**: a slow archive.org never hangs a scan; downloaded pages are kept and analysed even if stragglers are dropped.
- **A restart-proof queue**: queued and running scans survive a restart and re-enqueue under the same link.
- **Per-finding provenance**: every entity is stamped with its source capture for co-occurrence pivots.

## Tests

```bash
cd backend
python -m pytest tests/ -q                    # full suite
python -m pytest tests/test_extractor.py -q   # extraction patterns
python -m pytest tests/test_filters.py -q     # snapshot selection
python -m pytest tests/test_api.py -q         # API endpoints
```

Every extraction category ships dedicated positive and false-positive tests (at least five of each), alongside API validation, job-lifecycle, selection-algorithm, and end-to-end tests. CI runs the full suite on Python 3.12.

## Legal and ethics

WayTrace reads **only public archives** from the Wayback Machine. It performs no active scanning, port scanning, brute-forcing, or DNS enumeration, and sends nothing to the target.

- It is built for legitimate security research, OSINT investigations, journalism, and academic or historical research.
- All source data comes from the Internet Archive. By using WayTrace you also agree to the [Internet Archive's Terms of Use](https://archive.org/about/terms.php); do not flood requests or try to bypass rate limits.
- Archived pages can contain personal data; there is no general exemption for public personal data under the GDPR. Handle what you find responsibly, and report risks to the people who own the data, never against them.
- You are solely responsible for how you use WayTrace and its results. The software is provided "as is", without warranty, and the author disclaims liability to the maximum extent permitted by law.

Abuse reports and removal requests: [housset.thomas@pm.me](mailto:housset.thomas@pm.me).

See [CONTRIBUTING.md](CONTRIBUTING.md) to build a new extraction category, and [CHANGELOG.md](CHANGELOG.md) for the release history.

## License

[MIT](LICENSE)
