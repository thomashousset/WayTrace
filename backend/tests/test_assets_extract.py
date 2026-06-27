"""Tests for the `assets` extractor: stylesheets, scripts, images, fonts,
media. Covers both the dedicated extract_assets pass (link/script/img/source)
and the endpoint-diversion path (<a href> to an asset file).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


TS = "20220601120000"


def _run(html: str, domain: str = "example.com"):
    pages = [{"html": html, "url": f"https://{domain}/", "timestamp": TS}]
    return extract_all(pages, domain)


def _assets(results) -> list[dict]:
    return results["assets"]


def _by_path(results) -> dict[str, dict]:
    return {a["path"]: a for a in results["assets"]}


# ---------------------------------------------------------------------------
# Positive: <link>/<script>/<img>/<source> with internal paths feed `assets`.
# ---------------------------------------------------------------------------


class TestAssetsPositive:
    def test_stylesheet_link(self):
        html = '<link rel="stylesheet" href="/wp-content/themes/acme/style.css">'
        by = _by_path(_run(html))
        assert "/wp-content/themes/acme/style.css" in by
        assert by["/wp-content/themes/acme/style.css"]["type"] == "stylesheet"

    def test_script_src(self):
        html = '<script src="/wp-includes/js/jquery/jquery.min.js"></script>'
        by = _by_path(_run(html))
        assert "/wp-includes/js/jquery/jquery.min.js" in by
        assert by["/wp-includes/js/jquery/jquery.min.js"]["type"] == "script"

    def test_image_src(self):
        html = '<img src="/wp-content/uploads/2024/02/hero.webp">'
        by = _by_path(_run(html))
        assert "/wp-content/uploads/2024/02/hero.webp" in by
        assert by["/wp-content/uploads/2024/02/hero.webp"]["type"] == "image"

    def test_font_via_link_preload(self):
        html = (
            '<link rel="preload" as="font" type="font/woff2" '
            'href="/assets/fonts/inter.woff2" crossorigin>'
        )
        by = _by_path(_run(html))
        assert "/assets/fonts/inter.woff2" in by
        assert by["/assets/fonts/inter.woff2"]["type"] == "font"

    def test_video_source(self):
        html = '<video><source src="/media/intro.mp4" type="video/mp4"></video>'
        by = _by_path(_run(html))
        assert "/media/intro.mp4" in by
        assert by["/media/intro.mp4"]["type"] == "video"

    def test_anchor_pointing_at_asset_is_diverted(self):
        # <a href> pointing at an asset flows through _extract_links and
        # lands in assets, not endpoints.
        html = '<a href="/wp-content/plugins/elementor-pro/assets/css/widget.min.css">x</a>'
        results = _run(html)
        by = _by_path(results)
        assert "/wp-content/plugins/elementor-pro/assets/css/widget.min.css" in by
        assert not any(
            e["path"].endswith("widget.min.css")
            for e in results["endpoints"]
        )

    def test_multiple_asset_types_coexist(self):
        html = """
        <link rel="stylesheet" href="/assets/app.css">
        <script src="/assets/app.js"></script>
        <img src="/assets/logo.svg">
        """
        by = _by_path(_run(html))
        assert by["/assets/app.css"]["type"] == "stylesheet"
        assert by["/assets/app.js"]["type"] == "script"
        assert by["/assets/logo.svg"]["type"] == "image"

    def test_asset_temporal_metadata(self):
        html = '<link rel="stylesheet" href="/x/style.css">'
        results = _run(html)
        a = _by_path(results)["/x/style.css"]
        assert a["first_seen"] == "2022-06"
        assert a["last_seen"] == "2022-06"
        assert a["occurrences"] == 1


# ---------------------------------------------------------------------------
# Negative: external / data-urls / wayback artifacts must NOT appear.
# ---------------------------------------------------------------------------


class TestAssetsNegative:
    def test_external_cdn_script_not_collected(self):
        html = '<script src="https://cdn.thirdparty.com/thing.js"></script>'
        assert "/thing.js" not in [a["path"] for a in _assets(_run(html))]

    def test_data_url_image_ignored(self):
        html = '<img src="data:image/png;base64,iVBORw0KGgo=">'
        assert _assets(_run(html)) == []

    def test_wayback_web_prefix_skipped(self):
        html = '<script src="/web/20200101000000/https://example.com/app.js"></script>'
        # Should not land in assets (wayback artifact); path may or may not
        # exist but must not start with /web/.
        for a in _assets(_run(html)):
            assert not a["path"].startswith("/web/")
