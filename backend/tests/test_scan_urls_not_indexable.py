"""A scan URL is the credential, so it must not be invited into an index.

The 24-char url_id is a capability: whoever holds the link reads the scan.
robots.txt carried `Allow: /s/` from the days of the public feed, where
published scans were meant to be found. The feed is gone; the invitation
stayed. Nothing on the site links to a scan any more, so a crawler only
reaches one if a holder posts it somewhere, which is exactly the case where
being indexed turns one person's share into a permanent public record.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config import settings
from db import init_db, save_job
from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    await init_db(db_path)
    yield
    import db as _db
    _db._db_path = None


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_robots_does_not_invite_crawlers_into_scan_urls(client):
    body = (await client.get("/robots.txt")).text
    assert "Allow: /s/" not in body
    assert "Disallow: /s/" in body


@pytest.mark.anyio
async def test_robots_still_lets_the_homepage_be_found(client):
    body = (await client.get("/robots.txt")).text
    assert "Allow: /$" in body
    assert "Disallow: /api/" in body


@pytest.mark.anyio
async def test_a_scan_page_tells_crawlers_not_to_index_it(client):
    # robots.txt is a request; the header is the instruction that survives a
    # crawler which fetched the URL anyway, from a link it found elsewhere.
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="noindex1", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    for path in ("/s/noindex1", "/api/s/noindex1",
                 "/api/s/noindex1/export.html", "/api/s/noindex1/export.csv"):
        r = await client.get(path)
        tag = r.headers.get("x-robots-tag", "")
        assert "noindex" in tag, f"{path} carries no X-Robots-Tag ({tag!r})"


@pytest.mark.anyio
async def test_the_homepage_stays_indexable(client):
    r = await client.get("/")
    assert "noindex" not in r.headers.get("x-robots-tag", "")


def test_the_reverse_proxy_does_not_shadow_the_apps_robots():
    """Production served Caddy's inline copy, so the file above was dead text.

    The two said different things: the app disallowed /s/, the proxy invited
    crawlers into it, and only the proxy's answer ever reached a crawler."""
    from pathlib import Path
    caddyfile = Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile"
    if not caddyfile.exists():          # stripped from the public build
        return
    body = caddyfile.read_text(encoding="utf-8")
    assert "handle /robots.txt" not in body
    assert "User-agent:" not in body


def test_the_access_log_redacts_both_shapes_of_a_scan_url():
    """/s/{url_id} is the link people paste; /api/s/{url_id} is what the JS
    calls. The filter named only the second, so the token most likely to be
    written to disk was the one left in clear."""
    import re
    from pathlib import Path
    caddyfile = Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile"
    if not caddyfile.exists():
        return
    line = [l for l in caddyfile.read_text(encoding="utf-8").splitlines()
            if "request>uri regexp" in l]
    assert len(line) == 1
    pattern = re.search(r'regexp "([^"]+)"', line[0]).group(1)
    rx = re.compile(pattern)
    uid = "wrk00yyyyyyyyyyyyyyyyyyy"
    for url in (f"/s/{uid}", f"/api/s/{uid}", f"/api/s/{uid}/export.json",
                f"/api/s/{uid}/search?q=secret"):
        assert uid not in rx.sub("[gone]", url), f"{url} keeps its token"
    assert rx.sub("[gone]", "/api/health") == "/api/health"
