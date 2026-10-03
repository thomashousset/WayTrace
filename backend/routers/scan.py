from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from config import settings
from db import save_job as _save_job_to_db
from db import index_scan_pages
from selectolax.parser import HTMLParser
from models import (
    DateRange,
    JobCreate,
    JobStatus,
    PathGroup,
    PreflightResponse,
    ScanConfig,
    ScanCreateResponse,
    SnapshotDetail,
    SubdomainGroup,
)
from services.cdx import CDX_DILUTION_FACTOR, fetch_cdx_snapshots
from services import archive_health
from services.extractor import (
    ALL_CATEGORIES, compute_highlights,
    new_accum, mine_subdomains, process_page, finalize_accum,
)
from services.extractor.finalize import ALL_CATEGORIES, merge_analytics_ids
from services.favicon_hash import hash_favicons
from services.filters import (
    filter_snapshots, _compute_cap, _normalize_path, _score_path,
    _allocate_budget_by_year,
)
from services.ip_utils import get_client_ip
from services.scraper import scrape_snapshots
from store import store, PerIpLimitError, QueueFullError

router = APIRouter(prefix="/api", tags=["scan"])


def _visible_text(html: str | None) -> str:
    """Extract the human-visible text of a page for full-text indexing.

    Drops script/style/template noise so a search for a word matches page copy,
    not inlined JS. Best-effort: any parse error yields an empty string.
    """
    if not html:
        return ""
    try:
        tree = HTMLParser(html)
        for node in tree.css("script, style, noscript, template"):
            node.decompose()
        root = tree.body or tree.root
        return root.text(separator=" ", strip=True) if root is not None else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# SSE frame formatting
# ---------------------------------------------------------------------------

SSE_EVENT_TYPES: tuple[str, ...] = (
    "progress", "complete", "error", "expired",
    "phase", "source_started", "source_completed", "source_failed", "source_skipped",
)


def format_sse_event(event_id: int, event_type: str, data: dict) -> str:
    """Format a single SSE frame.

    Returns the full ``id:..\\nevent:..\\ndata:..\\n\\n`` block. Unknown
    event types are not rejected (the SSE protocol passes them through),
    but a debug log is emitted so future regressions surface in dev.
    """
    if event_type not in SSE_EVENT_TYPES:
        logger.debug("SSE: unknown event type {!r}", event_type)
    return f"id: {event_id}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Scan pipeline (CDX -> filter -> scrape, with extraction overlapping the scrape)
# ---------------------------------------------------------------------------

# Failure taxonomy. Every scan that failed in production carried an empty
# meta, so "why did these 23 scans fail" could only be answered from container
# logs that a rebuild destroys. The cause is now part of the record.
#
# The codes are deliberately coarse and stable: they name what the operator can
# act on, not the exception class. `detail` keeps the raw text, bounded, for the
# cases the taxonomy has not met yet.
_FAILURE_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    # (code, substring to look for, message shown to the person who ran the scan)
    #
    # Order matters: the timeout signature is checked before the generic
    # unreachable one, because the RuntimeError text carries both.
    ("archive_paused", "rate-limiting us",
     "Scanning is paused so this server does not get blocked by archive.org. "
     "Try again in a few minutes."),
    ("archive_paused", "cooling down",
     "Scanning is paused so this server does not get blocked by archive.org. "
     "Try again in a few minutes."),
    ("cdx_malformed", "malformed json",
     "Archive.org returned a snapshot index for this domain that we could not "
     "read."),
    # We stopped waiting. Not a size problem: measured 2026-09-06, the same
    # query took 8.7 s on one domain and over 50 s on a smaller one, and the
    # speed of that index varies through the day. Saying "archive.org is slow"
    # here would blame a third party for our own deadline.
    ("cdx_timeout", "timeouterror",
     "Archive.org's snapshot index for this domain did not answer within the "
     "time we allow. This is not about the size of the site, that index varies "
     "in speed from one hour to the next. Try again a little later."),
    # Archive.org answered, with an error. Here it really is their side.
    ("cdx_error", "cdx api unreachable",
     "Archive.org refused the snapshot index request. That is temporary on "
     "their side, try again in a few minutes."),
)


def classify_failure(exc: BaseException) -> tuple[str, str]:
    """Map an exception to (code, user-facing message).

    The codes name what the person can act on. `cdx_timeout` and `cdx_error`
    used to share one code, `cdx_unreachable`, which forced a single sentence
    onto two opposite situations: a deadline we set ourselves, and an error
    archive.org returned. Only the second is archive.org's fault, and the
    product should not attribute the first to them.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return "scan_timeout", (
            f"The scan passed {settings.scan_timeout_seconds // 60} minutes and was "
            "stopped."
        )
    text = str(exc).lower()
    for code, needle, message in _FAILURE_SIGNATURES:
        if needle in text:
            return code, message
    return "unexpected", (
        "The scan failed for a reason we have not identified yet. The details "
        "are recorded on our side."
    )


def _failure_meta(domain: str, code: str, exc: BaseException, start: float) -> dict:
    """Minimal, durable record of why a scan failed. No results, no page data:
    just enough to answer 'why did this fail' months later without logs."""
    return {
        "domain": domain,
        "error": code,
        # Bounded and type-prefixed: a RuntimeError with an empty str() is
        # exactly what made the CDX timeouts unreadable in the logs.
        "error_detail": f"{type(exc).__name__}: {exc}"[:300],
        "failed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan_duration_seconds": round(time.time() - start, 1),
    }


async def run_scan(
    job_id: str,
    config: ScanConfig | None = None,
    selected_snapshots: list[dict] | None = None,
) -> None:
    """Main scan pipeline with timeout protection.

    When called by the queue worker (no positional args beyond job_id), config and
    selected_snapshots are pulled from the job state set at submission time.
    """
    job = await store.get_job(job_id)
    if job is None:
        return
    # The user may have cancelled/deleted this scan in the window between the
    # queue worker promoting it and this task starting. Honour that: don't flip
    # cancelled -> running (which would run the scan anyway and, worse, let
    # _persist_and_finish re-insert the row delete_scan just hard-deleted).
    if job.get("status") == "cancelled":
        logger.info("Job {} was cancelled before start; skipping", job_id)
        await store.finish_job(job_id)
        return
    if config is None:
        config = job.get("config")
    if selected_snapshots is None:
        selected_snapshots = job.get("selected_snapshots")

    await store.update_job(job_id, status="running", step="Starting scan...")
    # Mirror the transition to the persisted row (restart-proof queue): a
    # restart re-enqueues 'running' rows from zero. Best-effort only.
    try:
        from db import update_job_queue_status
        await update_job_queue_status(job["url_id"], "running")
    except Exception:
        pass
    logger.info("Scan started for job {}", job_id)

    domain = job["domain"]
    start = time.time()

    try:
        await asyncio.wait_for(
            _scan_pipeline(job_id, domain, start, config, selected_snapshots),
            timeout=settings.scan_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        logger.warning("Job {} timed out after {}s", job_id, settings.scan_timeout_seconds)
        code, message = classify_failure(exc)
        await store.update_job(
            job_id, status="failed", step=message,
            meta=_failure_meta(domain, code, exc, start),
        )
    except Exception as exc:
        code, message = classify_failure(exc)
        # A CDX budget exhaustion on a mega-domain is an expected outcome, not a
        # defect: log it at warning without the traceback so real bugs stay
        # visible in the log stream.
        if code == "unexpected":
            logger.exception("Scan failed for job {}: {!r}", job_id, exc)
        else:
            logger.warning("Scan failed for job {} ({}): {}", job_id, code, exc)
        await store.update_job(
            job_id, status="failed", step=message,
            meta=_failure_meta(domain, code, exc, start),
        )
    finally:
        await _persist_and_finish(job_id, start)


async def _persist_and_finish(job_id: str, start: float) -> None:
    """Persist the final job state to the jobs table and free the queue slot."""
    live = await store.get_job(job_id)
    if live is None:
        return
    if live.get("status") == "cancelled":
        # The user deleted this scan while it was still running. Persisting it
        # now would re-insert the row that delete_scan just hard-deleted (and
        # revive it with a fresh expiry). Also drop the queued row written at
        # submission time (restart-proof queue), so a cancelled scan can never
        # be resurrected by the startup restore. Free the slot and stop.
        try:
            from db import delete_job as _delete_job
            await _delete_job(live["url_id"])
        except Exception:
            logger.exception("Could not drop cancelled job row {}", job_id)
        await store.finish_job(job_id, duration_seconds=time.time() - start)
        return
    now = datetime.now(timezone.utc)
    from store import retention_timedelta
    retention = retention_timedelta()   # 0 retention days = keep forever
    # The example-domain scan is the permanent product demo: it must survive
    # the retention purge so the homepage "See an example" button always has
    # a report to open. Effectively never expires.
    if (
        settings.example_scan_domain
        and live["domain"] == settings.example_scan_domain
        and live.get("status") == "completed"
    ):
        retention = timedelta(days=365 * 100)
    try:
        await _save_job_to_db(
            url_id=live["url_id"],
            domain=live["domain"],
            client_ip=live.get("client_ip", "0.0.0.0"),
            created_at=live["created_at"],
            expires_at=now + retention,
            completed_at=now if live.get("status") in ("completed", "failed") else None,
            status=live.get("status", "failed"),
            meta=live.get("meta"),
            results=live.get("results"),
        )
    except Exception:
        logger.exception("Failed to persist job {} to DB", job_id)


    duration = time.time() - start
    await store.finish_job(job_id, duration_seconds=duration)


def resolve_categories(config: ScanConfig | None) -> list[str] | None:
    """Which extraction categories a scan runs. A non-empty explicit per-scan
    list wins; otherwise fall back to the instance default
    (`settings.default_categories`); if that is also empty, return None meaning
    "run all categories". An explicit empty list is normalised to None (all),
    never "extract nothing", so no code path can produce an all-empty report."""
    if config is not None and config.categories:
        return config.categories
    if settings.default_categories:
        return list(settings.default_categories)
    return None


async def _scan_pipeline(
    job_id: str, domain: str, start: float,
    config: ScanConfig | None = None,
    selected_snapshots: list[dict] | None = None,
) -> None:
    empty_results = {cat: [] for cat in ALL_CATEGORIES}
    categories = resolve_categories(config)

    try:
        pages_deduped = 0
        # Largeur de l'echantillon. En mode avance l'utilisateur a choisi ses
        # captures lui-meme, on ne connait pas le vivier dont elles sortent,
        # donc on ne remonte pas de chiffre plutot que d'en inventer un.
        unique_paths = None
        narrow_sample = False
        if selected_snapshots:
            # Advanced mode: user-selected snapshots ; skip CDX + filter
            await store.update_job(
                job_id, step="Using selected snapshots...", progress=10
            )
            snap_list = [{"timestamp": s["timestamp"], "url": s["url"]} for s in selected_snapshots]
            total_found = len(snap_list)
            selected = snap_list
            date_first = f"{snap_list[0]['timestamp'][:4]}-{snap_list[0]['timestamp'][4:6]}" if snap_list else None
            date_last = f"{snap_list[-1]['timestamp'][:4]}-{snap_list[-1]['timestamp'][4:6]}" if snap_list else None
        else:
            # Phase 1: CDX fetch. Bounded the same way as the preflight so a
            # mega-domain (lemonde.fr) returns a diverse sample fast instead of
            # hanging on an unbounded full-index collapse (the cause of scans
            # stuck at "Fetching snapshots from CDX API... 5%"). The filter caps
            # to the scan depth afterwards, so a 25k diverse pull is plenty.
            await store.update_job(
                job_id, step="Fetching snapshots from CDX API...", progress=5
            )
            try:
                cdx_result = await fetch_cdx_snapshots(
                    domain, max_snapshots=15_000,
                    server_limit=int(15_000 * CDX_DILUTION_FACTOR),
                    deadline_seconds=55, request_timeout=45, retries=2,
                    collapse="timestamp:6",
                )
            except RuntimeError:
                cdx_result = await fetch_cdx_snapshots(
                    domain, max_snapshots=12_000,
                    server_limit=int(12_000 * CDX_DILUTION_FACTOR),
                    deadline_seconds=35, request_timeout=30, retries=1,
                    collapse="urlkey",
                )

            # Phase 2: Filtering
            await store.update_job(
                job_id, step="Selecting diverse snapshots...", progress=10
            )
            filtered = filter_snapshots(cdx_result["snapshots"], config)

            if not filtered["selected"]:
                await store.update_job(
                    job_id,
                    status="completed",
                    progress=100,
                    step="No HTML snapshots found",
                    meta={
                        "domain": domain,
                        "total_snapshots_found": cdx_result["total_found"],
                        "snapshots_analyzed": 0,
                        "pages_scraped": 0,
                        "pages_failed": 0,
                        "pages_deduped": 0,
                        "pages_attempted": 0,
                        "truncated": False,
                        "truncation_reason": None,
                        "date_first_seen": None,
                        "date_last_seen": None,
                        "scan_duration_seconds": round(time.time() - start, 1),
                    },
                    results=empty_results,
                )
                return

            total_found = cdx_result["total_found"]
            selected = filtered["selected"]
            date_first = filtered["date_first_seen"]
            date_last = filtered["date_last_seen"]
            pages_deduped = filtered.get("pages_deduped", 0)
            unique_paths = filtered.get("unique_paths")
            narrow_sample = bool(filtered.get("narrow_sample"))

        # Phase 3: Scraping
        await store.update_job(
            job_id,
            step=f"Scraping {len(selected)} archived pages...",
            progress=15,
        )

        # Phases 3+4 OVERLAP: extract pages AS they download instead of waiting for
        # every page first. The scraper (which owns all the anti-block pacing) calls
        # our on_page hook with each finished page; a single consumer batches them
        # onto a worker thread and pushes live per-category counts. So findings
        # appear on the loading page while pages are still downloading, and the
        # CPU-bound extraction overlaps the (rate-limited, mostly-idle) network wait.
        cat_set = set(categories) if categories else None
        accum = new_accum()
        page_seq: dict = {}
        _q: asyncio.Queue = asyncio.Queue()
        _DONE = object()

        async def _on_page(p):
            await _q.put(p)

        scrape_stats: dict = {}
        scrape_task = asyncio.ensure_future(
            scrape_snapshots(selected, job_id, on_page=_on_page, stats=scrape_stats)
        )
        scrape_task.add_done_callback(lambda _t: _q.put_nowait(_DONE))

        def _process_batch(batch):
            for p in batch:
                process_page(p, domain, accum, cat_set, page_seq)

        batch: list = []
        ex_seen = 0

        async def _flush():
            nonlocal ex_seen
            if not batch:
                return
            chunk, batch[:] = batch[:], []
            await asyncio.to_thread(_process_batch, chunk)
            ex_seen += len(chunk)
            live_counts = {c: len(accum[c]) for c in ALL_CATEGORIES if accum[c]}
            # Don't fight the scraper's step/progress during the overlap; just
            # stream the live counts so findings fill in as pages arrive.
            await store.update_job(job_id, live_counts=live_counts)

        # The scrape runs as a separate task, so if the pipeline is cancelled
        # (scan timeout) or the consumer raises, we MUST cancel it too — otherwise
        # it keeps fetching from archive.org and pushing onto a queue no one drains.
        try:
            while True:
                item = await _q.get()
                if item is _DONE:
                    break
                if item.get("html") is not None:
                    batch.append(item)
                if len(batch) >= 25:
                    await _flush()
            await _flush()  # tail
        finally:
            if not scrape_task.done():
                scrape_task.cancel()
                try:
                    await scrape_task
                except (asyncio.CancelledError, Exception):
                    pass

        pages = scrape_task.result()   # full list (incl. failed pages) for meta/FTS

        pages_scraped = sum(1 for p in pages if p["html"] is not None)
        pages_failed = len(pages) - pages_scraped
        # Pages we skipped because archive.org was refusing this server's IP
        # (hard block), as opposed to genuine archive gaps. Surfaced so the UI
        # can explain an empty scan honestly instead of blaming "archive gaps".
        pages_blocked = sum(1 for p in pages if p.get("error") == "blocked")

        # Finalize: subdomains from the CDX URLs (cheap), then convert the
        # accumulator to sorted result lists off the event loop.
        await store.update_job(job_id, step="Extracting OSINT data...", progress=94)
        mine_subdomains(pages, domain, accum, cat_set)
        results = await asyncio.to_thread(finalize_accum, accum, categories)
        merge_analytics_ids(results)
        # Best-effort favicon hashing (breaker-gated, capped). Never fatal.
        try:
            await hash_favicons(results.get("favicons") or [], domain)
        except Exception as exc:
            logger.debug("favicon hashing skipped: {}", exc)
        results["highlights"] = await asyncio.to_thread(compute_highlights, results, domain)

        # Index the visible text of each scraped page for full-text search
        # (search the archived CONTENT, not just the pivots). Best-effort and
        # capped; never fatal to the scan.
        try:
            live = await store.get_job(job_id)
            _uid = live.get("url_id") if live else None
            if _uid:
                # _visible_text parses HTML per page (selectolax) — also CPU-bound,
                # so build the FTS rows off the event loop too.
                def _build_fts_rows():
                    return [
                        (p.get("timestamp", ""), p.get("url", ""), _visible_text(p.get("html")))
                        for p in pages if p.get("html")
                    ]
                fts_rows = await asyncio.to_thread(_build_fts_rows)
                await index_scan_pages(_uid, fts_rows)
        except Exception as exc:
            logger.debug("page-text indexing skipped for job {}: {}", job_id, exc)

        # Coverage. `snapshots_analyzed` has always been the SELECTED count, not
        # the fetched one, so a scan that stopped at the wall-clock budget looked
        # complete while covering a fraction of its own selection. Record the
        # gap rather than leaving the reader to subtract two numbers whose
        # meaning is not obvious.
        pages_attempted = len(pages)
        truncated = pages_attempted < len(selected)
        truncation_reason = None
        if truncated:
            truncation_reason = (
                "scrape_budget" if scrape_stats.get("budget_exhausted") else "incomplete"
            )
            logger.info(
                "Partial coverage for job {}: {}/{} selected snapshots fetched ({})",
                job_id, pages_attempted, len(selected), truncation_reason,
            )
        duration = round(time.time() - start, 1)
        meta = {
            "domain": domain,
            "total_snapshots_found": total_found,
            "snapshots_analyzed": len(selected),
            "pages_scraped": pages_scraped,
            "pages_failed": pages_failed,
            "pages_blocked": pages_blocked,
            "pages_deduped": pages_deduped,
            "pages_attempted": pages_attempted,
            "truncated": truncated,
            "truncation_reason": truncation_reason,
            # Combien d'adresses distinctes ce rapport couvre, et si cette
            # largeur est trop faible pour que la couverture veuille dire
            # quelque chose. Voir filters.is_narrow_sample.
            "unique_paths": unique_paths,
            "narrow_sample": narrow_sample,
            "date_first_seen": date_first,
            "date_last_seen": date_last,
            "scan_duration_seconds": duration,
            # How many entities the scan actually produced. db.admin_recent_jobs
            # already read meta["total_findings"], but nothing ever wrote it, so
            # the admin panel and My scans both showed a blank where the number
            # belongs. Counted here, once, instead of re-parsing the whole
            # results blob on every listing.
            "total_findings": sum(
                len(v) for k, v in results.items()
                if k in ALL_CATEGORIES and isinstance(v, list)
            ),
        }

        await store.update_job(
            job_id,
            status="completed",
            progress=100,
            step="Scan complete",
            meta=meta,
            results=results,
        )
        logger.info("Scan completed for job {} in {}s", job_id, duration)

    except Exception as exc:
        logger.error("Scan pipeline error for job {}: {}", job_id, exc)
        raise


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def _assemble_preflight(snapshots: list[dict], domain: str) -> PreflightResponse:
    """Group CDX snapshots into the preflight payload. Pure CPU (no I/O), run via
    asyncio.to_thread so it never blocks the event loop."""
    from urllib.parse import urlparse as _urlparse

    html_snaps = [s for s in snapshots if s.get("mimetype") == "text/html"]
    html_snaps.sort(key=lambda s: s["timestamp"])

    unique_paths: set[str] = set()
    unique_content: set[tuple[str, str]] = set()
    by_path: dict[str, list[dict]] = {}
    for snap in html_snaps:
        path = _normalize_path(snap["url"])
        unique_paths.add(path)
        digest = snap.get("digest")
        if digest:
            unique_content.add((path, digest))
        by_path.setdefault(path, []).append(snap)

    if html_snaps:
        first_ts = html_snaps[0]["timestamp"]
        last_ts = html_snaps[-1]["timestamp"]
        date_first = f"{first_ts[:4]}-{first_ts[4:6]}"
        date_last = f"{last_ts[:4]}-{last_ts[4:6]}"
    else:
        date_first = None
        date_last = None

    suggested_cap = _compute_cap(len(unique_paths), len(html_snaps))

    # Build path groups for Advanced mode
    path_groups: list[PathGroup] = []
    for path, snaps in by_path.items():
        snaps.sort(key=lambda s: s["timestamp"])
        path_groups.append(PathGroup(
            path=path,
            score=_score_path(path),
            count=len(snaps),
            first=snaps[0]["timestamp"],
            last=snaps[-1]["timestamp"],
            snapshots=[
                SnapshotDetail(
                    timestamp=s["timestamp"],
                    url=s["url"],
                    digest=s.get("digest"),
                )
                for s in snaps
            ],
        ))
    path_groups.sort(key=lambda g: (-g.score, g.path))

    # Build subdomain groups for scope selection
    by_subdomain: dict[str, list[dict]] = {}
    for snap in html_snaps:
        try:
            host = _urlparse(snap["url"]).hostname or domain
        except (ValueError, KeyError):
            host = domain
        by_subdomain.setdefault(host, []).append(snap)

    subdomain_groups = []
    for sub, snaps in sorted(by_subdomain.items()):
        snaps.sort(key=lambda s: s["timestamp"])
        subdomain_groups.append(SubdomainGroup(
            subdomain=sub,
            snapshot_count=len(snaps),
            first=f"{snaps[0]['timestamp'][:4]}-{snaps[0]['timestamp'][4:6]}",
            last=f"{snaps[-1]['timestamp'][:4]}-{snaps[-1]['timestamp'][4:6]}",
        ))
    subdomain_groups.sort(key=lambda g: -g.snapshot_count)

    return PreflightResponse(
        domain=domain,
        total_snapshots=len(snapshots),
        html_snapshots=len(html_snaps),
        unique_paths=len(unique_paths),
        unique_content=len(unique_content) if unique_content else len(html_snaps),
        date_range=DateRange(first=date_first, last=date_last),
        # cap 0 (no archived snapshots) is not a valid ScanConfig cap; fall back
        # to None (adaptive default) so an empty domain returns a clean preflight
        # instead of a 500 that the UI shows as "Internal Server Error".
        suggested_config=ScanConfig(cap=suggested_cap or None),
        path_groups=path_groups,
        subdomain_groups=subdomain_groups,
    )


@router.post("/scan/preflight", response_model=PreflightResponse)
async def scan_preflight(body: JobCreate, request: Request):
    """Lightweight CDX probe. bounded so a flaky archive.org returns a
    fast 502 instead of letting the UI spinner sit for 8 minutes.
    """
    if archive_health.is_open():
        raise HTTPException(
            status_code=503,
            detail={"error": "archive_paused", "message": archive_health.status()["message"],
                    "retry_after": archive_health.seconds_remaining()},
        )
    # collapse=timestamp:6 keeps path diversity (one capture per URL+month).
    # The killer was an over-large limit: at server_limit=15000 even a mega
    # domain (lemonde.fr ~ tens of millions of captures) answers in ~15-25s
    # AND stays diverse (thousands of distinct paths). limit=20000 tipped it
    # over the deadline. Fallback to collapse=urlkey (one per URL) if the
    # month-collapse ever stalls; both keep diversity.
    try:
        try:
            # use_cache: a recent CDX result (preflight or scan, < 6h) is reused
            # instead of re-querying archive.org. Big lever against throttling on
            # repeat lookups; the TTL keeps it fresh enough for a preview.
            cdx_result = await fetch_cdx_snapshots(
                body.domain, max_snapshots=15_000,
                server_limit=int(15_000 * CDX_DILUTION_FACTOR),
                deadline_seconds=55, request_timeout=45, retries=2,
                collapse="timestamp:6", use_cache=True,
            )
        except RuntimeError:
            cdx_result = await fetch_cdx_snapshots(
                body.domain, max_snapshots=12_000,
                server_limit=int(12_000 * CDX_DILUTION_FACTOR),
                deadline_seconds=35, request_timeout=30, retries=1,
                collapse="urlkey", use_cache=False,
            )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Archive.org didn't respond in time: {exc}. "
                "Try the scan directly (the full crawl has its own retry "
                "budget) or wait a minute and refresh."
            ),
        )

    # Building the response (grouping up to 15k snapshots + thousands of Pydantic
    # models) is pure CPU; run it off the event loop so a cache-primed flood of
    # preflights can't pin the single worker.
    return await asyncio.to_thread(_assemble_preflight, cdx_result["snapshots"], body.domain)


# ---------------------------------------------------------------------------
# Scan CRUD
# ---------------------------------------------------------------------------

def _apply_hosted_ceiling(
    config: ScanConfig | None, sel_snaps: list[dict] | None
) -> tuple[ScanConfig | None, list[dict] | None]:
    """Bound a scan to the hosted snapshot ceiling (settings.hosted_snapshot_ceiling).

    On the hosted service this keeps archive.org load and scan time bounded while
    the selection stays representative (year-proportional). ceiling == 0 means
    no ceiling — the self-hosted / local mode, which can scan a domain in full.

    - user-selected snapshots over the ceiling are trimmed to a representative
      year-proportional subset (not just the newest N),
    - otherwise the per-scan cap is clamped down, and a missing cap is defaulted
      to the ceiling so a hosted scan is "max but bounded".
    """
    ceiling = settings.hosted_snapshot_ceiling
    if not ceiling or ceiling <= 0:
        return config, sel_snaps
    if sel_snaps:
        if len(sel_snaps) > ceiling:
            sel_snaps = _allocate_budget_by_year(sel_snaps, ceiling)
        return config, sel_snaps
    if config is None:
        return ScanConfig(cap=ceiling), sel_snaps
    if config.cap is None:
        return config.model_copy(update={"cap": ceiling}), sel_snaps
    if config.cap > ceiling:
        return config.model_copy(update={"cap": ceiling}), sel_snaps
    return config, sel_snaps


@router.post("/scan", response_model=ScanCreateResponse)
async def create_scan(body: JobCreate, request: Request):
    # Who owns the scan about to be created. None on the self-hosted build,
    # which has no accounts. Every deduplication lookup below is scoped to this
    # value: a scan never crosses an account boundary, because on this service
    # the domain itself is the sensitive part.
    owner_id: int | None = None
    # Guardrail: if this caller already has this domain in flight, attach them
    # to that scan instead of doubling the archive.org load (double submit, two
    # tabs, a refresh mid-queue).
    if not body.force and not body.selected_snapshots:
        live_same = await store.find_live_job_for_domain(body.domain, owner_id)
        if live_same is not None:
            return ScanCreateResponse(
                job_id="", url_id=live_same["url_id"],
                url=f"/s/{live_same['url_id']}", status=live_same["status"],
                position=store.get_position(live_same["id"]) or 0,
                eta_seconds=store.get_eta_seconds(live_same["id"]),
                reused=True, live=True,
                retention_days=settings.scan_retention_days,
            )

    # Guardrail: if this caller already has a recent completed scan of this
    # domain, return it instead of re-scanning, which would re-hammer
    # archive.org for data they already hold. "Scan more" sets force=True to run
    # a fresh, denser scan. Scoped to the caller: another account's scan of the
    # same domain is none of their business, and its existence is itself the
    # disclosure.
    if not body.force and not body.selected_snapshots:
        from db import find_recent_scan_for_domain
        try:
            existing = await find_recent_scan_for_domain(body.domain, owner_id)
        except Exception as exc:   # never let a lookup failure block a scan
            logger.debug("reuse lookup skipped: {}", exc)
            existing = None
        if existing:
            return ScanCreateResponse(
                job_id="", url_id=existing["url_id"],
                url=f"/s/{existing['url_id']}", status="completed",
                position=0, eta_seconds=0, reused=True,
                retention_days=settings.scan_retention_days,
            )

    # archive.org circuit breaker open: refuse fast, queue nothing, send no
    # request, so we never add load while it is rate-limiting us.
    if archive_health.is_open():
        raise HTTPException(
            status_code=503,
            detail={"error": "archive_paused", "message": archive_health.status()["message"],
                    "retry_after": archive_health.seconds_remaining()},
        )
    if body.config and body.config.categories is not None:
        invalid = [c for c in body.config.categories if c not in ALL_CATEGORIES]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid categories: {', '.join(invalid)}",
            )

    sel_snaps = None
    if body.selected_snapshots:
        # Every selected snapshot must belong to the scanned domain (or a
        # subdomain). Otherwise a client could hand-craft a request to make the
        # server fetch archived copies of an unrelated host through Wayback.
        dom = body.domain.lower()
        for s in body.selected_snapshots:
            host = (urlparse(s.url).hostname or "").lower()
            if not (host == dom or host.endswith("." + dom)):
                raise HTTPException(
                    status_code=422,
                    detail=f"Snapshot URL is not on the scanned domain: {s.url}",
                )
        sel_snaps = [{"timestamp": s.timestamp, "url": s.url} for s in body.selected_snapshots]

    # Bound the scan to the hosted ceiling (no-op when self-hosted/local).
    scan_config, sel_snaps = _apply_hosted_ceiling(body.config, sel_snaps)

    client_ip = get_client_ip(request)
    try:
        res = await store.create_job(
            body.domain,
            client_ip=client_ip,
            config=scan_config,
            selected_snapshots=sel_snaps,
        )
    except PerIpLimitError:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "per_ip_limit",
                "limit": settings.max_active_per_ip,
                "message": "You already have the maximum number of scans in flight from this connection.",
            },
        )
    except QueueFullError:
        avg = int(store.avg_scan_seconds)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_full",
                "queue_size": settings.max_queue_total,
                "eta_seconds": avg,
                "message": "Service is full. Try again in a few minutes.",
            },
            headers={"Retry-After": str(avg)},
        )

    # If the job slotted directly into active (position == 0), no worker tick
    # is needed, dispatch run_scan right away. Jobs that landed in waiting
    # are picked up by the queue worker as slots free.
    if res["position"] == 0:
        asyncio.create_task(run_scan(res["job_id"]))

    return ScanCreateResponse(
        job_id=res["job_id"],
        url_id=res["url_id"],
        url=f"/s/{res['url_id']}",
        status=res["status"],
        position=res["position"],
        eta_seconds=res["eta_seconds"],
    )


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    pos = store.get_position(job_id) if job.get("status") == "queued" else None
    allowed = set(JobStatus.model_fields.keys())
    payload = {k: v for k, v in job.items() if k in allowed}
    return JobStatus(
        **payload,
        position=pos,
        eta_seconds=store.get_eta_seconds(job_id) if pos else None,
        total_in_queue=len(store.waiting),
    )


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/stream")
async def stream_job_status(job_id: str, request: Request):
    """SSE endpoint for real-time job progress updates."""
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    last_event_id = int(request.headers.get("last-event-id", "0"))

    async def event_generator():
        event_id = last_event_id
        last_progress = -1
        last_step = ""
        heartbeat_interval = 15
        last_heartbeat = time.time()

        while True:
            if await request.is_disconnected():
                return

            job = await store.get_job(job_id)
            if job is None:
                event_id += 1
                yield format_sse_event(event_id, "expired", {"status": "expired"})
                return

            progress = job.get("progress", 0)
            step = job.get("step", "")
            status = job.get("status", "queued")

            if progress != last_progress or step != last_step:
                event_id += 1
                event_data = {
                    "status": status,
                    "progress": progress,
                    "step": step,
                }
                event_type = "progress"
                if status == "completed":
                    event_type = "complete"
                    event_data["meta"] = job.get("meta")
                    event_data["results"] = job.get("results")
                elif status == "failed":
                    event_type = "error"

                yield format_sse_event(event_id, event_type, event_data)
                last_progress = progress
                last_step = step
                last_heartbeat = time.time()

            if status in ("completed", "failed"):
                return

            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
