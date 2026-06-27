"""Tests for the js_urls extractor (inline <script> URL / API endpoint extraction)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{
        "html": html,
        "url": "https://example.com/",
        "timestamp": "20220601120000",
    }]
    return extract_all(pages, "example.com")["js_urls"]


def _urls(items: list[dict]) -> set[str]:
    return {it["url"] for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_apiurl_assignment():
    html = '<script>var apiUrl = "https://api.internal.example.com/v1/users";</script>'
    items = _run(html)
    assert any(
        it["url"] == "https://api.internal.example.com/v1/users"
        and it["context"] == "assignment"
        for it in items
    )


def test_detects_baseurl_assignment():
    html = '<script>const baseUrl = "https://backend.example.org/api";</script>'
    items = _run(html)
    assert any(
        it["url"] == "https://backend.example.org/api" and it["context"] == "assignment"
        for it in items
    )


def test_detects_endpoint_assignment():
    html = '<script>endpoint: "https://svc.example.net/graphql/query"</script>'
    items = _run(html)
    assert any(
        it["url"] == "https://svc.example.net/graphql/query"
        and it["context"] == "assignment"
        for it in items
    )


def test_detects_inline_fetch_url():
    html = '<script>fetch("https://api.example.com/data/endpoint/long/path")</script>'
    items = _run(html)
    assert any(
        it["url"] == "https://api.example.com/data/endpoint/long/path"
        and it["context"] == "inline"
        for it in items
    )


def test_detects_server_url_assignment():
    html = '<script>window.serverUrl = "https://internal.corp.example/api/service";</script>'
    items = _run(html)
    assert "https://internal.corp.example/api/service" in _urls(items)


def test_assignment_wins_over_inline_dedup():
    # Same URL appears as assignment and as a plain inline reference: the
    # higher-signal "assignment" context is recorded once (deduped).
    html = (
        '<script>var apiUrl = "https://api.example.com/v1/endpoint/data"; '
        'fetch("https://api.example.com/v1/endpoint/data");</script>'
    )
    items = _run(html)
    matches = [it for it in items if it["url"] == "https://api.example.com/v1/endpoint/data"]
    assert len(matches) == 1
    assert matches[0]["context"] == "assignment"


def test_detects_http_inline_url():
    html = '<script>const u = "http://legacy.example.com/old/api/resource";</script>'
    items = _run(html)
    assert "http://legacy.example.com/old/api/resource" in _urls(items)


# ---------------------------------------------------------------------------
# False-positive / negative
# ---------------------------------------------------------------------------


def test_ignores_url_in_anchor_href():
    # URLs in normal markup (not inside a <script>) must not be extracted.
    html = '<a href="https://api.example.com/data/endpoint/long/path">link</a>'
    assert _run(html) == []


def test_ignores_url_in_prose_text():
    html = "<p>Visit https://api.example.com/some/long/internal/path here</p>"
    assert _run(html) == []


def test_ignores_external_script_src():
    # A <script> with a src attribute is external; its (inline) body is skipped.
    html = (
        '<script src="https://cdn.x.com/lib.js">'
        'var u = "https://api.example.com/some/long/internal/path";</script>'
    )
    assert _run(html) == []


def test_ignores_cdn_jsdelivr():
    html = '<script>var u = "https://cdn.jsdelivr.net/npm/foo/dist/bundle.min.js";</script>'
    assert _run(html) == []


def test_ignores_google_analytics():
    html = '<script>var u = "https://www.google-analytics.com/collect?v=1&tid=x";</script>'
    assert _run(html) == []


def test_ignores_facebook_net_subdomain():
    # Subdomain of a skip-listed tracking domain is filtered too.
    html = '<script>var u = "https://connect.facebook.net/en_US/sdk/longpath.js";</script>'
    assert _run(html) == []


def test_ignores_wayback_archive_host():
    html = '<script>var u = "https://web.archive.org/web/2022/http://x.com/page";</script>'
    assert _run(html) == []


def test_ignores_short_url_below_min_length():
    # JS_URL_RE requires at least 15 chars after the scheme; a tiny URL is dropped.
    html = '<script>var u = "https://a.co/x";</script>'
    assert _run(html) == []


def test_ignores_relative_path_assignment():
    # JS_API_ASSIGNMENT_RE requires an http(s):// value; a relative path is not
    # captured even when assigned to apiUrl.
    html = '<script>var apiUrl = "/api/v1/internal/users";</script>'
    assert _run(html) == []
