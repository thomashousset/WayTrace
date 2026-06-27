"""Coverage for the static-asset surface served by main.py.

These routes have no business logic but are part of the public contract:
- Direct share URLs (path-only /s/{url_id}) must serve the SPA shell so
  pasted/email-stripped links don't 404.
- /favicon.ico must be a multi-resolution ICO (browsers pick the best frame).
- /manifest.webmanifest must be valid JSON with all icon sizes referenced.
- /icons/* must be mounted from the frontend bundle.
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_root_serves_html_shell():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text
    assert "<title>" in r.text


@pytest.mark.anyio
async def test_share_url_path_only_serves_shell():
    """A direct /s/{url_id} URL (no hash fragment) must serve the SPA shell.

    Without this, pasted links and email-stripped URLs 404 because the
    backend only knows about /api/s/{url_id}, not /s/{url_id}. The JS in
    the shell promotes the path to the hash router on boot.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/s/anyTokenHere123")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text


@pytest.mark.anyio
async def test_favicon_ico_is_multi_resolution():
    """favicon.ico must contain multiple frame sizes (not a lone 16x16).

    Modern browsers, Windows pinned-tab UI, and HiDPI desktops look for
    32x32+ frames; a single 16x16 ICO renders blurry everywhere except
    the address bar.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/favicon.ico")
    assert r.status_code == 200
    body = r.content
    # ICO header: reserved(2) + type(2) + count(2)
    _, ico_type, count = struct.unpack("<HHH", body[:6])
    assert ico_type == 1, "not an ICO file"
    assert count >= 4, f"favicon.ico has only {count} frames, expected >=4"


@pytest.mark.anyio
async def test_manifest_references_icons():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/manifest.webmanifest")
    assert r.status_code == 200
    manifest = json.loads(r.content)
    assert manifest["name"] == "WayTrace"
    icon_sizes = {icon["sizes"] for icon in manifest["icons"]}
    # Must include at least 192 and 512 for Add-to-Home-Screen on Android.
    assert "192x192" in icon_sizes
    assert "512x512" in icon_sizes
    # Must include at least one maskable icon for adaptive Android launchers.
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])


@pytest.mark.anyio
async def test_icons_mount_serves_png():
    """The /icons mount must serve the PNG bundle (not 404)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/icons/icon-192.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # First 4 bytes of any PNG are the signature.
    assert r.content[:4] == b"\x89PNG"
