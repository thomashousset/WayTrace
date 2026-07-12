# Changelog

## v1.2.0

- **Full-text search over scanned page content.** Search any word across a scan's archived pages (not only the extracted pivots), with highlighted excerpts and links to the Wayback capture. Accent-insensitive; the index is kept per-scan and purged on the 7-day retention.
- **Single scan pipeline.** Removed a dead, divergent second pipeline (collect/analyze) that duplicated CDX/scraping/extraction; the public scan flow is now the only path. This also removed unauthenticated legacy endpoints (IDOR).
- **Security hardening.** Fixed a catastrophic ReDoS in the S3 bucket regex; made client-IP detection spoof-resistant (trust the reverse-proxy header, not client-forgeable ones); reject selected snapshots that aren't on the scanned domain; refuse to boot in production with the default secret.
- **Reliability.** Reworked the scraper to back off on archive.org connection-level throttling (not only HTTP 429) and report a per-outcome breakdown, so large scans no longer fail silently.
- **Accessibility & UI.** WCAG-AA text contrast, keyboard-operable favicon tiles, and the Google favicon fallback removed (it leaked the investigated domain to Google — the tool now contacts only archive.org).
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
