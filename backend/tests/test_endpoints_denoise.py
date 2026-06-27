"""Endpoint denoise: static assets, cached copies, and SVG data-urls must
not reach the endpoint category. Real app/API routes must survive.

Covers the 80/20 filter added to `_extract_links` in extract.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


TS = "20220601120000"


def _run(html: str, domain: str = "example.com"):
    pages = [{"html": html, "url": f"https://{domain}/", "timestamp": TS}]
    return extract_all(pages, domain)


def _endpoint_paths(results) -> list[str]:
    return [e["path"] for e in results["endpoints"]]


def _asset_paths(results) -> list[str]:
    return [e["path"] for e in results["assets"]]


# ---------------------------------------------------------------------------
# Positive: real app / API / admin routes MUST be captured as endpoints.
# ---------------------------------------------------------------------------


class TestEndpointPositive:
    def test_plain_app_route(self):
        html = '<a href="/contact">contact</a>'
        assert "/contact" in _endpoint_paths(_run(html))

    def test_deep_pretty_url(self):
        html = '<a href="/ecole-2600/formation-bac-plus-5">x</a>'
        assert "/ecole-2600/formation-bac-plus-5" in _endpoint_paths(_run(html))

    def test_wp_json_api_route(self):
        html = '<a href="/wp-json/oembed/1.0/embed">embed</a>'
        assert "/wp-json/oembed/1.0/embed" in _endpoint_paths(_run(html))

    def test_rest_api_route(self):
        html = '<a href="/api/users/42">user</a>'
        assert "/api/users/42" in _endpoint_paths(_run(html))

    def test_graphql_endpoint(self):
        html = '<a href="/graphql">gql</a>'
        assert "/graphql" in _endpoint_paths(_run(html))

    def test_v1_versioned_api(self):
        html = '<a href="/v1/accounts/me">me</a>'
        assert "/v1/accounts/me" in _endpoint_paths(_run(html))

    def test_admin_route(self):
        html = '<a href="/admin">admin</a>'
        assert "/admin" in _endpoint_paths(_run(html))

    def test_login_route(self):
        html = '<a href="/login">login</a>'
        assert "/login" in _endpoint_paths(_run(html))

    def test_oauth_route(self):
        html = '<a href="/oauth/authorize?client_id=abc">go</a>'
        assert "/oauth/authorize" in _endpoint_paths(_run(html))

    def test_well_known_route(self):
        html = '<a href="/.well-known/security.txt">sec</a>'
        assert "/.well-known/security.txt" in _endpoint_paths(_run(html))


# ---------------------------------------------------------------------------
# Negative: asset noise MUST NOT be captured as endpoints (may go to assets).
# ---------------------------------------------------------------------------


class TestEndpointNegative:
    def test_css_file_not_endpoint(self):
        p = "/wp-content/uploads/elementor/css/post-5.css"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)

    def test_js_min_file_not_endpoint(self):
        p = "/wp-includes/js/jquery/jquery.min.js"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)

    def test_cached_css_not_endpoint(self):
        p = "/wp-content/cache/min/1/wp-content/themes/hello-elementor-child/style.css"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)

    def test_elementor_plugin_asset_not_endpoint(self):
        p = "/wp-content/plugins/elementor-pro/assets/css/widget-nav-menu.min.css"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)

    def test_wp_includes_dist_js_not_endpoint(self):
        p = "/wp-includes/js/dist/hooks.min.js"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)

    def test_svg_dataurl_leaked_href_rejected(self):
        svg = (
            "%3Csvg%20xmlns%3D'http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg'"
            "%20viewBox%3D'0%200%200%200'%3E%3C%2Fsvg%3E"
        )
        html = f"<a href=\"data:image/svg+xml,{svg}\">x</a>"
        results = _run(html)
        # Data urls have empty path; nothing should leak.
        for p in _endpoint_paths(results):
            assert "%3Csvg" not in p
            assert "%3csvg" not in p
        for p in _asset_paths(results):
            assert "%3Csvg" not in p

    def test_font_file_not_endpoint(self):
        p = "/wp-content/themes/acme/fonts/icons.woff2"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)

    def test_image_file_not_endpoint(self):
        p = "/wp-content/uploads/2023/05/hero.jpg"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)

    def test_favicon_dropped_from_both(self):
        html = '<a href="/favicon.ico">fav</a>'
        results = _run(html)
        assert "/favicon.ico" not in _endpoint_paths(results)
        assert "/favicon.ico" not in _asset_paths(results)

    def test_static_dir_not_endpoint(self):
        p = "/static/app.bundle.js"
        html = f'<a href="{p}">x</a>'
        results = _run(html)
        assert p not in _endpoint_paths(results)
        assert p in _asset_paths(results)


# ---------------------------------------------------------------------------
# Allowlist override: prefix looks asset-ish but route is real.
# ---------------------------------------------------------------------------


class TestEndpointAllowlistOverride:
    def test_wp_json_with_json_extension_kept(self):
        # wp-json is an API, keep it even though .json is elsewhere.
        html = '<a href="/wp-json/wp/v2/posts">posts</a>'
        assert "/wp-json/wp/v2/posts" in _endpoint_paths(_run(html))

    def test_admin_subroute_kept(self):
        html = '<a href="/admin/users/create">x</a>'
        assert "/admin/users/create" in _endpoint_paths(_run(html))


# ---------------------------------------------------------------------------
# 80/20 smoke test: mix of 10 junk + 2 real, only the 2 real must survive.
# ---------------------------------------------------------------------------


def test_eighty_twenty_denoise():
    html = """
    <a href="/wp-content/uploads/elementor/css/post-5.css">a</a>
    <a href="/wp-content/plugins/elementor-pro/assets/css/widget-nav-menu.min.css">b</a>
    <a href="/wp-content/cache/min/1/style.css">c</a>
    <a href="/wp-includes/js/dist/hooks.min.js">d</a>
    <a href="/wp-includes/js/jquery/jquery.min.js">e</a>
    <a href="/wp-content/uploads/2023/05/hero.jpg">f</a>
    <a href="/wp-content/themes/acme/fonts/icons.woff2">g</a>
    <a href="/static/app.bundle.js">h</a>
    <a href="/dist/main.abc123.js">i</a>
    <a href="/_next/static/chunks/framework.js">j</a>
    <a href="/contact">real-1</a>
    <a href="/wp-json/oembed/1.0/embed">real-2</a>
    """
    results = _run(html)
    paths = set(_endpoint_paths(results))
    assert "/contact" in paths
    assert "/wp-json/oembed/1.0/embed" in paths
    # Root '/' is also valid (every page has an implicit self-link in some HTML;
    # here we do not emit it since there is no <a href="/">). Just assert no
    # junk leaked.
    for junk in [
        "/wp-content/uploads/elementor/css/post-5.css",
        "/wp-includes/js/jquery/jquery.min.js",
        "/static/app.bundle.js",
        "/_next/static/chunks/framework.js",
    ]:
        assert junk not in paths
