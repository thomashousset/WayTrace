from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from loguru import logger

from config import settings, APP_VERSION
from db import init_db
from routers import health, scan
from routers import public as public_router
from services.background_tasks import queue_worker_loop, cleanup_loop
from store import store


def _log_file_path() -> Path | None:
    """Where the persistent log goes, or None if we cannot write one.

    Beside the database, which is the only directory a deployment guarantees to
    keep. The container's stdout log is attached to the container, so rebuilding
    the image destroys it: that is how the record of twenty-three failed scans
    was lost. Never fatal, a container that cannot write a log must still serve.
    """
    try:
        base = Path(settings.log_dir) if settings.log_dir else \
            Path(settings.database_url).expanduser().resolve().parent / "logs"
        base.mkdir(parents=True, exist_ok=True)
        return base / "waytrace.log"
    except OSError:
        return None


def _configure_logging() -> None:
    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, format=fmt, level=settings.log_level)

    # Persistent copy, so a deploy stops costing the diagnostic history.
    #
    # Retention deliberately tracks the scan retention window rather than being
    # generous: these lines name the domains that were scanned, and on this
    # service a domain is an investigation target. The log must not outlive the
    # scan it describes. 0 retention days (self-host "keep forever") means no
    # deletion here either.
    #
    # It carries no url_id: uvicorn's access log is a stdlib logger writing
    # straight to stdout, it never reaches this sink, and it is switched off in
    # the image anyway.
    path = _log_file_path()
    if path is not None:
        days = settings.scan_retention_days
        try:
            logger.add(
                path, format=fmt, level=settings.log_level,
                rotation="20 MB",
                retention=(f"{days} days" if days > 0 else None),
                compression="gz", encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not open the persistent log at {}: {}", path, exc)


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("WayTrace starting up")
    health.set_start_time()
    await init_db(settings.database_url)

    # Maintenance banner state survives restarts (app_state KV).
    from services import maintenance
    await maintenance.load_from_db()

    # Self-host config panel overrides survive restarts too (app_state KV).
    from services import runtime_config
    await runtime_config.load_overrides()

    # Restart-proof queue: re-enqueue jobs that were queued/running when the
    # previous process died (same job_id/url_id, so links keep working).
    restored = await store.restore_pending_jobs()
    if restored:
        logger.info("Restored {} queued/running scan(s) from before the restart", restored)

    worker = asyncio.create_task(queue_worker_loop(store, scan.run_scan))
    cleaner = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        logger.info("WayTrace shutting down")
        for task in (worker, cleaner):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(
    title="WayTrace",
    description="OSINT tool using the Wayback Machine to reconstruct domain history",
    version=APP_VERSION,
    lifespan=lifespan,
    # No public API discoverability in prod: don't hand random visitors an
    # interactive map of the API surface. Set EXPOSE_API_DOCS=1 in dev to enable.
    docs_url="/api/docs" if settings.expose_api_docs else None,
    redoc_url="/api/redoc" if settings.expose_api_docs else None,
    openapi_url="/api/openapi.json" if settings.expose_api_docs else None,
)

class _BodySizeLimitMiddleware:
    """Reject oversized request bodies (via Content-Length) before the app reads
    them, so an unauthenticated POST can't buffer hundreds of MB and OOM the
    single worker. A reverse proxy in front may cap the body too; this covers
    the app directly (self-hosted / no proxy)."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        too_big = int(value) > self.max_bytes
                    except ValueError:
                        too_big = False
                    if too_big:
                        payload = b'{"detail":"Request body too large."}'
                        await send({"type": "http.response.start", "status": 413,
                                    "headers": [(b"content-type", b"application/json"),
                                                (b"content-length", str(len(payload)).encode())]})
                        await send({"type": "http.response.body", "body": payload})
                        return
                    break
        await self.app(scope, receive, send)


app.add_middleware(_BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # No cookies, no session, no auth. leaving credentials disabled avoids
    # turning on a whole class of CORS attacks the moment someone later
    # adds a session.
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Accept"],
)

_STATIC_CACHE = "public, max-age=2592000"  # 30 days for icons/manifest


@app.middleware("http")
async def static_asset_cache(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/icons/") or path in ("/favicon.ico", "/manifest.webmanifest"):
        response.headers.setdefault("Cache-Control", _STATIC_CACHE)
    return response


# Content-Security-Policy (HSTS / nosniff / frame-options come from the
# reverse proxy in production).
# 'unsafe-inline' is required: the single-file frontend uses inline <script>,
# inline styles and inline event handlers. Even with it, the CSP blocks
# external script origins, restricts XHR to same-origin, and kills framing.
# Favicon thumbnails load from web.archive.org + Google's favicon service.
_TS = ""
_CSP = (
    "default-src 'self'; "
    f"script-src 'self' 'unsafe-inline' {_TS}; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data: https:; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "connect-src 'self'; "
    f"frame-src 'self' {_TS}; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
).replace("  ", " ")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Anything addressed by a url_id is reached by holding the link, and the
    # link is the credential. Set here rather than per route so an endpoint
    # added later under /s/ cannot be forgotten.
    path = request.url.path
    if path.startswith("/s/") or path.startswith("/api/s/"):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    return response


app.include_router(scan.router)
app.include_router(health.router)
app.include_router(public_router.router)
from routers import selfhost_config as selfhost_config_router
app.include_router(selfhost_config_router.router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Static assets (favicons, web app manifest, OG images)
from fastapi.staticfiles import StaticFiles as _StaticFiles
if (FRONTEND_DIR / "icons").is_dir():
    app.mount("/icons", _StaticFiles(directory=str(FRONTEND_DIR / "icons")), name="icons")


@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon():
    return FileResponse(FRONTEND_DIR / "icons" / "favicon.ico", media_type="image/x-icon")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def serve_manifest():
    return FileResponse(
        FRONTEND_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/robots.txt", include_in_schema=False)
async def serve_robots():
    robots = FRONTEND_DIR / "robots.txt"
    if robots.exists():
        return FileResponse(robots, media_type="text/plain")
    # Sensible default rather than serving a binary icon as robots.txt.
    return PlainTextResponse("User-agent: *\nAllow: /\n")


# RFC 9116. Built here rather than shipped as a file for two reasons. The
# address belongs to whoever runs the instance, and a file in the tree would
# hand this one's mailbox to every self-hosted copy, which is the mistake the
# legal page already made once. And Expires is mandatory and must stay under a
# year out, so a literal date is a quiet time bomb: computing it per request
# means the file cannot go stale on a service nobody is watching.
#
# No contact configured, no file. An instance with nowhere to route a report
# should say so with a 404 rather than publish an empty promise.
_SECURITY_TXT_TTL_DAYS = 180


@app.get("/.well-known/security.txt", include_in_schema=False)
@app.get("/security.txt", include_in_schema=False)
async def serve_security_txt():
    contact = (settings.operator_contact or "").strip()
    if not contact:
        raise HTTPException(status_code=404)
    # A bare address is the common case; anything with a scheme is already a URI.
    uri = contact if "://" in contact or contact.startswith("mailto:") \
        else f"mailto:{contact}"
    expires = (datetime.now(timezone.utc)
               + timedelta(days=_SECURITY_TXT_TTL_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # public_base_url is a server-only setting and does not exist in the
    # generated tree, so reading it as an attribute would 500 on exactly the
    # self-hosted install that bothered to configure a contact.
    base = (getattr(settings, "public_base_url", "") or "").rstrip("/")
    lines = [
        "# WayTrace is one small server run by one person.",
        "#",
        "# Reports are welcome and a person answers them. If you want to test",
        "# actively, say so first so we can agree a scope: the scans on this",
        "# server are investigations in progress that belong to the people who",
        "# ran them, and that data is not mine to gamble with.",
        "#",
        "# There is no bounty. There is credit, and a straight answer.",
        f"Contact: {uri}",
        f"Expires: {expires}",
        "Preferred-Languages: en, fr",
        "Policy: https://github.com/thomashousset/WayTrace/blob/main/SECURITY.md",
    ]
    if base:
        lines.append(f"Canonical: {base}/.well-known/security.txt")
    return PlainTextResponse("\n".join(lines) + "\n",
                             media_type="text/plain; charset=utf-8")


# Entry assets must revalidate on every load (no-cache still allows the
# conditional-GET 304 path via ETag/Last-Modified). Without this, browsers
# apply heuristic freshness and keep serving a stale styles.css/app.js for
# days after a deploy.
_REVALIDATE = {"Cache-Control": "no-cache"}


@app.get("/styles.css", include_in_schema=False)
async def serve_styles():
    return FileResponse(FRONTEND_DIR / "styles.css", media_type="text/css", headers=_REVALIDATE)


@app.get("/app.js", include_in_schema=False)
async def serve_app_js():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="text/javascript", headers=_REVALIDATE)


# index.html is read once at import; each request re-injects the instance
# defaults so the boot script can apply the default theme/name BEFORE paint
# (no FOUC). Kept as raw text so the injection is a single cheap str.replace.
_INDEX_HTML = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
_INDEX_PLACEHOLDER = "<!--WT_DEFAULTS-->"


def _serve_index():
    """Serve index.html. When there are no instance defaults to inject (hosted,
    or a self-host install that set none) fall back to FileResponse so the
    ETag/Last-Modified conditional-GET (304) path is preserved and the head is
    byte-identical to the static file. Only a self-host instance WITH a default
    theme or name pays the per-request injection + full-body transfer."""
    if not (settings.config_panel_enabled and (settings.default_theme or settings.instance_name)):
        return FileResponse(FRONTEND_DIR / "index.html", headers=_REVALIDATE)
    payload = json.dumps({"theme": settings.default_theme, "name": settings.instance_name})
    # json.dumps escapes quotes/backslashes but NOT "</script>" or the JS line
    # separators U+2028/U+2029, so an instance_name containing those could break
    # out of the inline <script>. Neutralise them (standard safe-JSON-in-HTML
    # escaping) so a self-set name can never inject.
    payload = (payload.replace("<", "\\u003c").replace(">", "\\u003e")
                      .replace(chr(0x2028), "\\u2028").replace(chr(0x2029), "\\u2029"))
    blob = f"<script>window.__WT_DEFAULTS__={payload}</script>"
    html = _INDEX_HTML.replace(_INDEX_PLACEHOLDER, blob)
    return HTMLResponse(html, headers=_REVALIDATE)


@app.get("/")
async def serve_frontend():
    return _serve_index()


# Direct share URLs like https://waytrace.org/s/abc123 (no hash fragment)
# need to serve index.html so the JS router can promote the pathname into
# its #/s/{url_id} hash route on boot. Without this, pasted/email-stripped
# links return 404 and the scan is unreachable.
@app.get("/s/{url_id}", include_in_schema=False)
async def serve_scan_view(url_id: str):
    return _serve_index()
