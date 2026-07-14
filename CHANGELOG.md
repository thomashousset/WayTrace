# Changelog

## v1.6.0

- **Redesigned report, two views.** The results page is rebuilt around how an investigation actually reads:
  - **Categories (default):** a rail lists all 43 categories (found first with counts, empty ones collapsed but present for scope transparency). You open one at a time; it shows its full findings *and* its own activity: when each value appeared and disappeared, plus a dated change feed. "Show all" flattens every found category.
  - **Activity:** tick categories *and* individual pivots (a subdomain, tracker, favicon, person) to compose a shared timeline; each becomes a lane, and the axis always spans exactly what's shown. Pivots derive from the ticked categories and are searchable. Includes the favicon-evolution gallery and a global change feed.
- **Neutral, provenance-first findings.** No more "importance" editorialising (the severity stats bar, filter, per-row dot, and the radial Pivots graph are gone). Every finding shows *when* it was first/last seen, *how often*, and the *archived source page*, so you judge. Values are fully shown and copyable (per-value with a confirmation, or whole column); CSV export carries the source, not a severity verdict.
- **Live scan.** Extraction now overlaps downloading (pages are extracted as they arrive) and runs off the event loop, so the server stays responsive and findings fill in on the loading page while the scan runs. The loading page shows four honest phases and real progress.
- **Don't re-scan a domain you already have.** A completed scan is reused for **14 days** instead of re-scanning (which would re-hammer archive.org); "Scan more" forces a fresh one. Anti-block hardened further: a hard IP-refusal now bails out immediately instead of draining hundreds of doomed requests.
- **Read the result at a glance, then narrow it.** A summary strip states the shape of the scan up front (findings, categories touched of 43, pages, date range) and lets you filter every view to what is **still present** today versus what has **disappeared** from the live site - so the "gone" findings, often the most interesting, are one click away. Any finding also expands, on request, to show the other findings captured on the *same* archived page (co-occurrence), without changing that a click still copies the value.
- **Better search & a11y.** Full-text page search no longer breaks on punctuation (emails, URLs, hyphens). Keyboard focus is visible on the search box and Scan button, and every control in the report (category rail, pivots, view toggles, filters) is reachable and operable by keyboard; the recent-scans feed distinguishes an error from an empty result; the sign-in dialog traps focus and closes on Escape.
- **Wayback Machine credited** with its official logo on the homepage and loading view.

## v1.5.0

- **Rate ceiling pinned below the refusal point.** The adaptive governor's ceiling drops from 150 to **80 req/min** (starting at 75): after a dense scan measured archive.org refusing TCP connections once the self-tuned rate crept to ~105/min, the governor can no longer climb into that zone. The floor/burst behaviour is unchanged.
- **Escalating hard-block cooldown.** A connection refusal used to pause scanning for a flat 30 minutes - far too long for what is usually a temporary, rate-based reject that clears in seconds. The pause is now **2 minutes on a first/isolated refusal** and only doubles (capped at 30 min) when refusals recur back-to-back within 15 minutes, i.e. the signature of a real block. Refusals are now logged at WARNING (were invisible at the default log level).
- **One scan at a time.** The public queue runs a **single scan at a time** with a **15-deep** waiting queue and **one in-flight scan per client**, so aggregate archive.org load stays minimal and no single user can stack scans.
- **UX.** The scan-progress spinner no longer stutters (its animation was restarting on every status poll). The alarming red "blocked" banner is gone - the count of pages archive.org rate-limited is folded into the neutral scan-summary line instead.

## v1.3.0

- **Self-governing archive.org request rate.** A process-wide governor bounds every archive.org call (page scrape, CDX enumeration, favicon) to both a shared request rate and a shared concurrency limit, so no number of parallel scans or users can burst past archive.org's tolerance. The rate is not a fixed guess: it **adapts** (AIMD, like TCP congestion control) - it starts conservative, creeps up while responses stay clean, and halves the instant archive.org refuses a connection, staying within a safe floor/ceiling. This keeps the server IP from being throttled or blocked.
- **Connection-refusal handling.** A hard IP block (TCP connection refused) is detected distinctly from ordinary throttling: the breaker trips fast, holds a long cooldown, does not retry (retrying only deepens a block), and a scan already running aborts gracefully instead of grinding. Intermittent throttling (some connections dropped, others served) is now caught too. A scan curtailed this way is shown honestly rather than being miscounted as archive gaps.
- **Leaner codebase.** Removed a large tranche of dead front-end code (the retired collect/v1 UI: comparison view, old history table, legacy pollers) and its orphaned CSS, and dropped the unused v1 database tables from the schema (with a migration that removes them from existing installs).
- **UX.** Loading skeletons on the scan view (no blank flash on a deep link), a bilingual archive.org status banner, and a handful of filled-in translation gaps.

## v1.2.0

- **Full-text search over scanned page content.** Search any word across a scan's archived pages (not only the extracted pivots), with highlighted excerpts and links to the Wayback capture. Accent-insensitive; the index is kept per-scan and purged on the 7-day retention.
- **Single scan pipeline.** Removed a dead, divergent second pipeline (collect/analyze) that duplicated CDX/scraping/extraction; the public scan flow is now the only path. This also removed unauthenticated legacy endpoints (IDOR).
- **Security hardening.** Fixed a catastrophic ReDoS in the S3 bucket regex; made client-IP detection spoof-resistant (trust the reverse-proxy header, not client-forgeable ones); reject selected snapshots that aren't on the scanned domain; refuse to boot in production with the default secret.
- **Reliability.** Reworked the scraper to back off on archive.org connection-level throttling (not only HTTP 429) and report a per-outcome breakdown, so large scans no longer fail silently.
- **Accessibility & UI.** WCAG-AA text contrast, keyboard-operable favicon tiles, and the Google favicon fallback removed (it leaked the investigated domain to Google - the tool now contacts only archive.org).
- **Codebase.** The single-file frontend is split into cacheable `index.html` + `styles.css` + `app.js`.

## v1.1.0

Public release folding in the RETEX round 2 work (shipped to the hosted beta first).

### Behaviour
- Scans are now private by default. The "Publish to the public feed" box is unchecked; a scan stays private unless the user explicitly ticks it.
- Scans can be deleted. Each row in My scans has a delete button that removes the scan permanently (cancels it if still running, hard-deletes the persisted row, drops it from the public feed). A running scan that is deleted can no longer resurrect itself when it finishes.
- "Scan more" button on the results page reopens the scope tuner for the same domain so a light scan can be extended to a denser one, reusing the recent CDX enumeration (cached ~6h) instead of restarting from zero.

### Classification
- Facebook is recognised on both `facebook.com` and the `fb.com` shortener, and a Facebook URL can no longer land in Named persons (URL-shaped values are rejected there).
- Social links found among Outgoing links (Facebook, Pinterest, YouTube, ...) now also appear under Social profiles and are de-duplicated: a social profile is listed once, under Social profiles, not repeated in Outgoing links.

### Favicons
- Each favicon now carries an MD5 and SHA-256 of its bytes (fetched best-effort from archive.org, capped and breaker-gated). The hashes are shown on hover in the favicon gallery and are copyable, for pivoting identical icons across hosts via Shodan/Censys.

### Advertising & tracker IDs
- Ad and tracker identifiers keep their exact prefix and show a platform chip in the findings table. Recognized patterns:
  - Google AdSense publisher, `ca-pub-` + 10-16 digits (Ad IDs)
  - Google AdMob app publisher, `ca-app-pub-` + 10-16 digits (Ad IDs)
  - AdSense ad slot, `data-ad-slot="<digits>"` (Ad IDs)
  - Universal Analytics, `UA-XXXXXXX-N` (Analytics & trackers)
  - Google Analytics 4, `G-XXXXXXXXXX` (Analytics & trackers)
  - Google Tag Manager, `GTM-XXXXXXX` (Analytics & trackers)
  - Google Ads / AdWords, `AW-XXXXXXXXX` (Analytics & trackers)
  - Meta / Facebook Pixel, `fbq('init', '<id>')` (Analytics & trackers)
  - Hotjar, Mixpanel, Yandex Metrica (Analytics & trackers)
  - GA4, UA, GTM, Hotjar, Matomo, Mixpanel, Segment, Yandex Metrica, Plausible, Fathom also carry a dedicated pivot URL (Analytics IDs)

### Fixes
- Launch scan button no longer becomes inert after a first successful scan. It was left disabled after navigating to the result and never re-enabled when returning to the scope view; it is now reset on entry and via a try/finally on every exit path.
- CDX pagination no longer stops early on large domains. The per-page success path reset the wrong counter, so non-consecutive transient errors accumulated and cut pagination short, silently dropping snapshots.
- Both snapshot-dedup pipelines now use the same normalized path key, so results are reproducible regardless of which pipeline runs.
- `/robots.txt` returns a real robots response instead of a binary icon when the static file is missing.
- Version is exposed at `/api/health` and in the site footer, with a short note explaining the public GitHub repo, the hosted test build, and the beta.

### Housekeeping
- Removed dead code (`JobResponse`, `startPublicScan`, unused settings, orphan CSS) and two hardcoded/untranslated UI strings.
