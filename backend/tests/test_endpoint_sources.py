"""Endpoint extraction from inline JS, data-*, meta-refresh, assets."""
from __future__ import annotations

from services.extractor import extract_all


def _paths(results):
    return {e["path"] for e in results["endpoints"]}


def test_endpoint_from_inline_fetch():
    """fetch('/api/users') inside an inline script must be harvested."""
    html = """
    <html><body>
      <script>
        async function load() {
          const r = await fetch('/api/users');
          return r.json();
        }
      </script>
    </body></html>
    """
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["endpoints"])
    assert "/api/users" in _paths(results)


def test_endpoint_from_axios_and_xhr():
    html = """
    <script>
      axios.get("/internal/reports", {});
      xhr.open('POST', '/admin/login');
    </script>
    """
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["endpoints"])
    ps = _paths(results)
    assert "/internal/reports" in ps
    assert "/admin/login" in ps


def test_endpoint_from_data_attrs():
    html = """
    <button data-href="/checkout/pay">Pay</button>
    <div hx-get="/items/42"></div>
    <form formaction="/submit">x</form>
    """
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["endpoints"])
    ps = _paths(results)
    assert "/checkout/pay" in ps
    assert "/items/42" in ps
    assert "/submit" in ps


def test_endpoint_from_meta_refresh():
    html = '<meta http-equiv="refresh" content="0;url=/legacy-redirect">'
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["endpoints"])
    assert "/legacy-redirect" in _paths(results)


def test_endpoint_from_link_preload():
    """Preload hints to JS assets route to the assets bucket, not endpoints."""
    html = '<link rel="preload" href="/assets/app.js" as="script">'
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io")
    asset_paths = {a["path"] for a in results.get("assets", [])}
    assert "/assets/app.js" in asset_paths


def test_endpoint_asset_noise_skipped():
    """Images / fonts / maps should not flood the endpoint bucket."""
    html = """
    <link rel="icon" href="/favicon.png">
    <link rel="stylesheet" href="/styles.css.map">
    <img src="/images/banner.jpg">
    """
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["endpoints"])
    ps = _paths(results)
    assert "/favicon.png" not in ps
    assert "/images/banner.jpg" not in ps
    assert "/styles.css.map" not in ps


def test_endpoint_external_host_skipped():
    """External script src dropped; same-domain JS routes to assets bucket."""
    html = """
    <script src="https://cdn.other.com/lib.js"></script>
    <script src="/local/bundle.js"></script>
    """
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io")
    asset_paths = {a["path"] for a in results.get("assets", [])}
    assert "/lib.js" not in asset_paths
    assert "/local/bundle.js" in asset_paths


def test_endpoint_inline_script_body_capped():
    """A massive inline blob shouldn't kill regex time. just not mined."""
    big_blob = 'x' * 250_000
    html = f'<script>{big_blob}; fetch("/should-not-be-seen");</script>'
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["endpoints"])
    assert "/should-not-be-seen" not in _paths(results)
