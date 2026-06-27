"""Tests for the endpoints extractor (_cat_endpoints)."""
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
    return extract_all(pages, "example.com")["endpoints"]


def _paths(html: str) -> set[str]:
    return {it["path"] for it in _run(html)}


# ---------------------------------------------------------------------------
# Positive - same-domain paths the extractor captures
# ---------------------------------------------------------------------------


def test_captures_api_link():
    assert "/api/v1/users" in _paths('<a href="/api/v1/users">api</a>')


def test_captures_admin_link():
    assert "/admin" in _paths('<a href="/admin">admin</a>')


def test_captures_login_link():
    assert "/login" in _paths('<a href="/login">login</a>')


def test_captures_form_action():
    html = '<form action="/submit-form"><input></form>'
    assert "/submit-form" in _paths(html)


def test_captures_data_attr_and_htmx_attr():
    html = '<button data-href="/account/settings" hx-get="/api/data">x</button>'
    paths = _paths(html)
    assert "/account/settings" in paths
    assert "/api/data" in paths


def test_captures_inline_script_paths():
    html = '<script>fetch("/api/v2/items"); var u = "/dashboard/home";</script>'
    paths = _paths(html)
    assert "/api/v2/items" in paths
    assert "/dashboard/home" in paths


def test_captures_meta_refresh_target():
    html = '<meta http-equiv="refresh" content="0;url=/profile">'
    assert "/profile" in _paths(html)


def test_captures_wp_json_despite_dotted_path():
    # /wp-json/ is allowlisted so it stays an endpoint, not an asset.
    assert "/wp-json/wp/v2/posts" in _paths('<a href="/wp-json/wp/v2/posts">x</a>')


def test_captures_same_domain_subdomain_link_path():
    # api.example.com is within the configured domain; its path is recorded.
    html = '<a href="https://api.example.com/v1/ping">x</a>'
    assert "/v1/ping" in _paths(html)


def test_normalizes_trailing_slash():
    # Trailing slash is stripped during canonicalisation.
    assert "/blog/post" in _paths('<a href="/blog/post/">x</a>')


# ---------------------------------------------------------------------------
# False-positive - things that must NOT appear as endpoints
# ---------------------------------------------------------------------------


def test_excludes_css_asset():
    assert "/style.css" not in _paths('<a href="/style.css">css</a>')


def test_excludes_png_asset():
    assert "/logo.png" not in _paths('<a href="/logo.png">png</a>')


def test_excludes_js_and_font_assets():
    paths = _paths('<a href="/app.js">j</a><a href="/font.woff2">w</a>')
    assert "/app.js" not in paths
    assert "/font.woff2" not in paths


def test_excludes_pure_anchor():
    assert _paths('<a href="#section">anchor</a>') == set()


def test_excludes_off_domain_link():
    assert _paths('<a href="https://other.com/foo">ext</a>') == set()


def test_excludes_mailto_tel_javascript():
    html = (
        '<a href="mailto:a@b.com">m</a>'
        '<a href="tel:+15551234">t</a>'
        '<a href="javascript:void(0)">j</a>'
    )
    assert _paths(html) == set()


def test_excludes_dedicated_extractor_paths():
    # robots.txt / favicon.ico have their own extractors and are hard-dropped.
    paths = _paths('<a href="/robots.txt">r</a><a href="/favicon.ico">f</a>')
    assert "/robots.txt" not in paths
    assert "/favicon.ico" not in paths


def test_excludes_form_action_anchor_only():
    # A form action of "#" is not a real endpoint.
    assert _paths('<form action="#"><input></form>') == set()
