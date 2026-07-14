"""Request-body size cap and preflight rate limit (unauthenticated DoS guards)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config import settings
from main import _BodySizeLimitMiddleware, app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_body_size_middleware_rejects_oversized_via_content_length():
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    inner_called = {"v": False}

    async def inner_app(scope, receive, send):
        inner_called["v"] = True

    mw = _BodySizeLimitMiddleware(inner_app, max_bytes=100)

    # Over the cap -> 413, inner app never invoked (body never buffered).
    over = {"type": "http", "method": "POST", "headers": [(b"content-length", b"101")]}
    await mw(over, receive, send)
    assert inner_called["v"] is False
    assert sent and sent[0]["type"] == "http.response.start" and sent[0]["status"] == 413

    # Under the cap -> passes through to the app.
    inner_called["v"] = False
    sent.clear()
    ok = {"type": "http", "method": "POST", "headers": [(b"content-length", b"50")]}
    await mw(ok, receive, send)
    assert inner_called["v"] is True


@pytest.mark.asyncio
async def test_body_size_cap_wired_into_the_app():
    # The real app must reject an oversized POST body before parsing it.
    cap = settings.max_request_body_bytes
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/scan/preflight", content=b"x" * (cap + 1),
                         headers={"Content-Type": "application/json"})
    assert r.status_code == 413


# --- preflight rate limit (hosted build only; ratelimit.py is server-only) ---


@pytest.mark.asyncio
async def test_preflight_is_rate_limited(monkeypatch):
    # ratelimit.py is stripped from the public/solo build (no accounts), and so
    # is the preflight rate-limit block — skip there.
    ratelimit = pytest.importorskip("services.ratelimit")
    import routers.scan as scan_mod
    from services import archive_health

    ratelimit.reset_all()
    monkeypatch.setattr(archive_health, "is_open", lambda: False)

    async def fake_cdx(*a, **k):
        return {"snapshots": []}

    monkeypatch.setattr(scan_mod, "fetch_cdx_snapshots", fake_cdx)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        codes = []
        # preflight bucket = 20 / IP / min; the 21st from the same client is 429.
        for _ in range(22):
            r = await c.post("/api/scan/preflight", json={"domain": "example.com"},
                             headers={"X-Real-IP": "203.0.113.7"})
            codes.append(r.status_code)

    assert codes[0] == 200, codes[:3]
    assert 429 in codes
    assert codes[-1] == 429
    ratelimit.reset_all()
