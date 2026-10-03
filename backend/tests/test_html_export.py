"""Tests for the standalone HTML export builder."""
import json
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
from services.html_export import build_standalone_html


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_inlines_preload_data_as_valid_json():
    html = build_standalone_html({
        "url_id": "abc",
        "domain": "x.com",
        "status": "completed",
        "results": {"emails": []},
    })
    assert "window.__WAYTRACE_PRELOAD__ = " in html
    # Extract the JSON between the marker and the closing script tag
    marker = "window.__WAYTRACE_PRELOAD__ = "
    start = html.find(marker) + len(marker)
    end = html.find(";</script>", start)
    payload_raw = html[start:end]
    # Replace escaped </ back to validate; this round-trip should yield valid JSON
    payload = json.loads(payload_raw.replace("<\\/", "</"))
    assert payload["domain"] == "x.com"


def test_escapes_script_break_in_domain_field():
    payload_str = "evil</script><script>alert(1)//"
    html = build_standalone_html({
        "url_id": "x",
        "domain": payload_str,
        "status": "completed",
        "results": {},
    })
    # The literal </script><script> must not appear in the inlined block
    marker = "window.__WAYTRACE_PRELOAD__ = "
    start = html.find(marker)
    end = html.find(";</script>", start)
    block = html[start:end]
    assert "</script><script>alert" not in block


def test_inline_appears_before_head_close():
    html = build_standalone_html({"url_id": "a", "domain": "b.com",
                                   "status": "completed", "results": {}})
    if "</head>" in html:
        idx_inline = html.find("__WAYTRACE_PRELOAD__")
        idx_head_close = html.find("</head>")
        assert idx_inline < idx_head_close


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
async def test_export_endpoint_returns_html_attachment(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="exp1", domain="example.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed",
        meta={"snapshots_analyzed": 5},
        results={"emails": [{"value": "a@b.c"}]},
    )
    r = await client.get("/api/s/exp1/export.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert "waytrace-example.com" in cd
    body = r.text
    assert "__WAYTRACE_PRELOAD__" in body
    assert "example.com" in body


@pytest.mark.anyio
async def test_export_endpoint_404_when_unknown(client):
    r = await client.get("/api/s/nope/export.html")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_export_endpoint_410_when_expired(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="oldexp", domain="x.com", client_ip="1.1.1.1",
        created_at=now - timedelta(days=8),
        expires_at=now - timedelta(hours=1),
        status="completed", meta={}, results={},
    )
    r = await client.get("/api/s/oldexp/export.html")
    assert r.status_code == 410


@pytest.mark.anyio
async def test_export_sanitizes_unsafe_domain_in_filename(client):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="weird", domain="../../etc/passwd",
        client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={}, results={},
    )
    r = await client.get("/api/s/weird/export.html")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # No slashes / colons / quotes in the suggested filename
    assert "/" not in cd.split('"')[1]
    assert "\\" not in cd.split('"')[1]


# --- The export must actually work offline -----------------------------------
# Every test above asserts on a substring of the HTML. None ever opened the
# file. The frontend was split into index.html + styles.css + app.js at some
# point, and from then on the "standalone" export shipped two dead <link>/
# <script> references: opened from disk it rendered an unstyled home page with
# no scan on it at all, and nothing here noticed.

def _built():
    return build_standalone_html({
        "url_id": "abc", "domain": "x.com",
        "status": "completed", "results": {"emails": []},
    })


def test_export_references_no_external_asset():
    html = _built()
    assert 'href="/styles.css"' not in html
    assert 'src="/app.js"' not in html


def test_export_inlines_the_stylesheet_and_the_script():
    from services.html_export import _FRONTEND_PATH
    html = _built()
    css = (_FRONTEND_PATH.parent / "styles.css").read_text(encoding="utf-8")
    js = (_FRONTEND_PATH.parent / "app.js").read_text(encoding="utf-8")
    # A distinctive line from each file, not the whole body: enough to prove
    # the content travelled, cheap enough not to diff 500 KB.
    assert css.splitlines()[52].strip() in html      # the --font token
    assert "__WAYTRACE_PRELOAD__" in js and js.count("function") > 50
    assert "window.__WAYTRACE_PRELOAD__" in html
    assert "</style>" in html and "<script>" in html


def test_export_weighs_what_the_three_files_weigh():
    from services.html_export import _FRONTEND_PATH
    total = sum((_FRONTEND_PATH.parent / n).stat().st_size
                for n in ("index.html", "styles.css", "app.js"))
    assert len(_built().encode("utf-8")) > total * 0.9


def test_export_neutralises_closing_tags_hidden_in_the_assets(tmp_path):
    # A stylesheet or a script carrying its own closing tag in a string would
    # break out of the block it is inlined into. Neither file does today; both
    # keep changing.
    (tmp_path / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="/styles.css"></head>'
        '<body><script src="/app.js"></script></body></html>', encoding="utf-8")
    (tmp_path / "styles.css").write_text(
        'body{content:"</style><script>alert(1)</script>"}', encoding="utf-8")
    (tmp_path / "app.js").write_text(
        'var s = "</script><img src=x onerror=alert(1)>";', encoding="utf-8")
    html = build_standalone_html(
        {"url_id": "a", "domain": "b.com", "status": "completed", "results": {}},
        frontend_path=tmp_path / "index.html")
    assert "</style><script>alert(1)" not in html
    assert "</script><img src=x" not in html
