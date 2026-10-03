# Changelog

## v1.9.1

- **A rejected address no longer disappears into silence.** When a mailbox refuses our mail, the provider stops delivering to it and says nothing. Five addresses had been in that state for months, one of them a real account created with a mistyped address that was told, every time, that a confirmation email was on its way. Bounces and complaints now come back into WayTrace: the sign-in page says the address rejected our messages instead of promising a link, the signup flow stops claiming a confirmation was sent, and the account still works so nobody is locked out by a typo they can fix.
- **You can reply to a WayTrace email.** Messages carry a reply address, which they never did. Answering one now reaches a person.
- **Admin: accounts stop looking inactive when they are not.** The scan count was read from the table that gets purged after the retention window, so any account that had scanned more than two weeks earlier showed zero. It now reads the usage record, which is kept, and shows the date of the last scan next to the count.
- **Admin: a new Delivery view** lists every address that can no longer be reached, why, and since when, including addresses with no account. A permanent bounce and a spam complaint are shown as what they are, because one means the address does not exist and the other means somebody asked to be left alone.
- **Admin: the ban button is no longer offered on the proxy gateway address.** Roughly a third of scans are recorded under it, so banning it would have blocked a large share of real traffic in one click.

## v1.9.0

- **Your scans stay yours.** A finished scan was reused across accounts: submitting a domain someone else had scanned in the last 14 days returned their report, and the interface presented it as a fast result. For people investigating live targets that disclosed both the findings and the fact that the domain was being looked at. Deduplication now only ever matches your own scans, and the same applies to attaching to a scan already running. A self-hosted instance, which has no accounts, is unaffected.
- **Reports say how much they cover.** A scan that ran out of its download budget looked exactly like a complete one, because the "snapshots analysed" figure counted the captures selected, not the captures actually fetched. Roughly four scans in ten were affected. Reports now show the captures processed against the captures selected, with one line explaining when the budget ran out first, and the scan record carries the truncation as a fact you can query.
- **Failures say why.** A failed scan used to record nothing at all, so the cause lived only in container logs that a rebuild erased. Each failure now names its reason, and the message tells you which one it was: archive.org not returning the snapshot index in time (what happens on very large domains), returning an unusable index, or rate-limiting the server.
- **A deploy no longer costs the diagnostics.** The application log and the archive.org index cache lived inside the container image, so every rebuild destroyed the log history and threw away a cache that exists to keep load off archive.org. Both now sit with the database, in the directory a deployment keeps. Log retention follows scan retention, because those lines name the domains that were scanned.
- **Fewer copies of your scan links.** The scan address is a capability: whoever has it can read the report. It was written in full into the web server's access log and into the application's own request log. The proxy now redacts it and the duplicate request log is off, while the traffic data needed to understand a spike is untouched, and kept far longer than the previous 24 hours.
- **Knowing what is deployed.** `/api/health` reports the exact commit and build time of the running image.
- **Five new extraction categories, 43 to 48.** Live-chat and helpdesk widgets, marketing and CRM embeds, payment and commerce stacks, linked mobile apps, and media/content CDN accounts. Each one exposes the third-party account identifier, which is scoped to the operator rather than to the site: the same identifier on another domain means the same owner behind both.

## v1.8.2

- **First-run setup for self-hosted instances.** On first launch a skippable wizard configures the instance name, default theme, operator identity, and default extraction categories. Everything stays editable afterwards in Settings, and a "Run setup again" button reopens it.
- **Honest per-instance identity.** Each instance generates a stable random id folded into the archive.org User-Agent, with an optional operator contact, so instances are attributed individually instead of all sharing one project-wide identity. This is attribution, not evasion: the identity never rotates and still names WayTrace.
- **Default extraction categories.** Pick which of the 43 categories an instance runs by default (empty runs all), turning an instance into a focused scanner, with a per-scan override in the advanced step to narrow or restore categories for a single scan.
- **Instance branding.** An instance name shows in the header and page title, and a default theme is applied before first paint (no flash) when the browser has no saved choice.
- All of the above is self-host only; the hosted service is unchanged.

## v1.8.1

- **Settings panel trimmed for self-hosting.** Two settings that only make sense for a public multi-user service behind a proxy have been removed from the panel: the per-client-IP scan limit (an abuse net) and the "trust Cloudflare headers" toggle. A single-user self-hosted install has no use for either.
- **Legal page rewritten.** Clearer plain-language terms with an explicit acceptance clause (using WayTrace means accepting these terms and the Internet Archive's Terms of Use, which govern the source data), a stronger "as is" disclaimer and limitation of liability, and sole-responsibility wording. The permitted-use list now names journalism, investigation and academic research. Contact for abuse and removal updated. Fixed the "Back to WayTrace" button, whose label was accent-on-accent (invisible) and vertically off-centre.

## v1.8.0

- **Rewritten documentation.** The README (English and French) has been rebuilt from scratch to match the software as it is today: 43 extraction categories, the real API surface, the self-hosted Settings panel, no accounts, and corrected defaults throughout. This release marks the 1.8 line, gathering everything since 1.7.2 into one coherent version.
- **Fully bilingual interface.** Around 55 interface strings that still showed in English regardless of the chosen language, notifications, confirmations, the 404 page, the export drawer and the guided-scan intro, are now translated in both English and French.

## v1.7.9

- **Upgrades don't break on an old `.env`.** WayTrace now ignores unknown settings in your `.env` instead of refusing to start, so a variable left over from a previous release never blocks a boot.
- **Fewer dead knobs.** Removed nine settings that were defined but never did anything (an old delay-based rate limiter, superseded by the adaptive archive.org governor), so the configuration is only settings that actually matter.

## v1.7.8

- **The Settings panel is fully bilingual and safer to use.** Every label, description, unit and control is translated in both languages. Nothing is applied until you click a **Save** button that appears next to a setting when you change it, so a value is never committed by accident.
- **One-click restart.** The few settings that need a restart now offer a **Restart now** button: the server restarts itself, the page waits for it to come back, and reloads. Works the same however you run WayTrace.
- **Keep scans forever.** Scan retention accepts an **infinite (∞)** option, so a self-hosted install can keep every scan indefinitely.
- **Steadier navbar.** Switching between English and French no longer nudges the top menu around.

## v1.7.7

- **Self-hosting is simpler.** The self-hosted build has no accounts and needs none: it now carries a first-class **Settings** entry in the navbar (the panel that tunes every scan and archive.org option), instead of a discreet footer link. Copy that assumed a hosted account or service has been reworked so nothing on a local install talks about signing in, a hosted snapshot ceiling, or a feed that no longer exists.

## v1.7.6

- **A scan can't be deleted by accident anymore.** In the scan history, the delete cross now turns into an explicit "Delete / Keep" choice in place, so it takes a deliberate second click, not one stray tap, to remove a scan.
- **Precise timing on the history page.** Each scan now shows exactly when it started, to the second, and how long it took to run.

## v1.7.5

- **The public feed is gone.** Almost every scan was kept private, so the feed sat empty and was already dropped from the homepage. The last of it is now removed: no more "publish to the feed" option anywhere, no publish endpoint. Your scans are yours.
- **Sharing by link stays on the hosted service**, where handing someone a scan URL is the point. On a self-hosted install the copy-link button is gone, since every scan is already in your local history; opening a scan by its link still works everywhere.
- **A real selector for categories.** On a finished scan, the selected category in the left rail is now outlined all the way around instead of wearing a single thick orange edge.

## v1.7.4

- **A configuration panel for self-hosters.** New Configuration page (linked from the homepage footer): every scan and archive.org setting of your install, editable from the UI and applied live, no restart, no `.env` editing. Settings are grouped by pipeline stage (archive.org politeness, snapshot selection, scans and queue, advanced), each with its description, unit and recommended value. Changes persist in the local database and survive restarts; every setting has a reset, plus a global "back to safe values" button. Nothing is capped beyond technical validity, but each sensitive setting shows a risk zone: orange is aggressive, red carries a real chance archive.org blocks your IP. Your machine, your rules. The hosted service keeps its limits locked.
- **A new quantity lever.** `SNAPSHOT_CAP_MULTIPLIER` scales how many snapshots a scan selects, on top of the depth presets.
- **The hero button now opens your scan history** instead of the example report.

## v1.7.3

- **The homepage shows the service breathing.** The latest-scans feed in the hero gives way to a live status panel: service state, scans running right now, queue depth, and how many scans ran over the last seven days.
- **Paste anything.** The scan input now accepts full URLs: scheme, path, query, port and credentials are stripped away and only the domain is kept, instead of rejecting the paste.
- **Self-hosting works out of the box.** The manual quick start (venv + uvicorn) no longer crashes on first run: the `.env` at the project root is loaded wherever you start uvicorn from, and the database now defaults to `waytrace.db` at the project root instead of Docker's `/data` path. Docker setups are unchanged.
- **Polish.** Focus rings follow each field's own corner radius, with the hero pill and navbar search sharing the same accent treatment; the recent-scans feed keeps a single card per domain, so re-scanning never shows a duplicate; the service can surface a short informational notice when something is worth announcing.

## v1.7.2

- **Make WayTrace yours.** New Appearance page (in the profile menu, or via the themes link in the footer): 20 hand-tuned themes, each with a dark and a light face, from classics like Truffe, Encre or Brume to full-mood palettes inspired by beloved editor colorschemes (Retro, Fjord, Vampire, Tokyo, Pastel, Estampe, Solaire...). A custom palette lets you pick a background and an accent per mode; the rest of the interface is derived automatically. Signed in, the theme is saved to your account and follows you across devices; otherwise it sticks to the browser.
- **Cleaner right edge.** The export and detail drawers no longer bleed their shadow into the page while parked off-screen, and the scrollbar now blends into the page and follows the active theme.
- **Fewer throwaway sign-ups.** The disposable-email blocklist grew by ~50 domains: the temp-mail.org rotating pool (lovadio.com and friends) and the current generation of temp-mail services.

## v1.7.1

- **New look.** The dark theme trades its olive-tinted gray for a deep truffle brown, and the light theme becomes a cool porcelain white; two deliberately contrasting moods instead of one warm scheme stretched both ways.

## v1.7.0

- **The scan queue survives a restart.** Queued and running scans are now persisted, so a redeploy or a crash no longer drops them: on restart they are re-queued under the same link, and a scan that was mid-run simply starts over. Finished scans were already saved; this closes the gap for the ones still in flight.
- **Same domain, one scan.** When a domain is already being scanned, a second request for it joins that run instead of launching a duplicate, so simultaneous interest in one domain doesn't double the load on archive.org. "Scan more" still forces a fresh, denser scan.
- **A single status line for how the service is doing.** One banner now reports the state from a single endpoint: a high-traffic notice when scans are queuing up, the archive.org health (slow / paused) as before, and a maintenance notice if the service becomes unreachable. A brief hiccup no longer reads as an outage.
- **Lighter homepage.** The recent-scans feed shows the six most recent published scans instead of twenty.
- **Reliability.** The schema migration is now idempotent, so a deploy interrupted mid-migration can't wedge the app on its next boot; the SQLite worker shuts down cleanly (no hung `docker stop`); and a scan cancelled at the instant it starts is honoured instead of running to completion anyway.

## v1.6.2

- **Fresh UI after every deploy.** `index.html`, `styles.css` and `app.js` are now served with `Cache-Control: no-cache`: browsers revalidate on each load (cheap 304 when unchanged) instead of heuristically keeping a stale copy for days after a release.

## v1.6.1

- **Quieter notes.** The colored left-border callout style is retired everywhere (legal note, scan-setup intro, error banner, admin alerts): notes are now uniform quiet cards, and the scan-setup intro reads as plain page copy so the setup cards stand out.
- **Copy.** WayTrace now describes itself as an OSINT recon tool through the Wayback Machine (page title, social metadata, PWA manifest, legal page in both languages).

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
