from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from loguru import logger

from config import settings, APP_VERSION
from db import init_db
from routers import health, scan
from routers import public as public_router
from services.background_tasks import queue_worker_loop, cleanup_loop
from store import store


def _configure_logging() -> None:
    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, format=fmt, level=settings.log_level)


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("WayTrace starting up")
    health.set_start_time()
    await init_db(settings.database_url)

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
    # No public API discoverability in prod. The OpenAPI schema lists the
    # legacy /api/collect / /api/analyze admin-facing endpoints that we don't
    # want random visitors poking at. Set EXPOSE_API_DOCS=1 in dev to enable.
    docs_url="/api/docs" if settings.expose_api_docs else None,
    redoc_url="/api/redoc" if settings.expose_api_docs else None,
    openapi_url="/api/openapi.json" if settings.expose_api_docs else None,
)

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


# Content-Security-Policy (Caddy already sets HSTS / nosniff / frame-options).
# 'unsafe-inline' is required: the single-file frontend uses inline <script>,
# inline styles and inline event handlers. Even with it, the CSP blocks
# external script origins, restricts XHR to same-origin, and kills framing.
# Favicon thumbnails load from web.archive.org + Google's favicon service.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data: https:; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


app.include_router(scan.router)
app.include_router(health.router)
app.include_router(public_router.router)

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


@app.get("/styles.css", include_in_schema=False)
async def serve_styles():
    return FileResponse(FRONTEND_DIR / "styles.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
async def serve_app_js():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="text/javascript")


@app.get("/")
async def serve_frontend():
    return FileResponse(FRONTEND_DIR / "index.html")


# Direct share URLs like https://waytrace.org/s/abc123 (no hash fragment)
# need to serve index.html so the JS router can promote the pathname into
# its #/s/{url_id} hash route on boot. Without this, pasted/email-stripped
# links return 404 and the scan is unreachable.
@app.get("/s/{url_id}", include_in_schema=False)
async def serve_scan_view(url_id: str):
    return FileResponse(FRONTEND_DIR / "index.html")
