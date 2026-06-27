"""Subdomain extraction from DNS-hint links and HTTP header re-mining."""
from __future__ import annotations

from services.extractor import extract_all


def test_subdomain_from_dns_prefetch():
    html = """
    <html><head>
        <link rel="dns-prefetch" href="//cdn.testcorp.io">
        <link rel="preconnect" href="https://api.testcorp.io">
    </head><body>hi</body></html>
    """
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["subdomains"])
    values = [s["value"] for s in results["subdomains"]]
    assert "cdn.testcorp.io" in values
    assert "api.testcorp.io" in values


def test_subdomain_rejects_offdomain_dns_hint():
    html = '<link rel="dns-prefetch" href="//cdn.attacker.com">'
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["subdomains"])
    assert results["subdomains"] == []


def test_subdomain_from_csp_header():
    """CSP header listing first-party origins must be mined into subdomains."""
    html = "<html><body>ok</body></html>"
    pages = [{
        "html": html,
        "url": "https://testcorp.io/",
        "timestamp": "20220601120000",
        "response_headers": {
            "content-security-policy":
                "default-src 'self'; script-src cdn.testcorp.io assets.testcorp.io; "
                "connect-src api.testcorp.io",
        },
    }]
    results = extract_all(pages, "testcorp.io", categories=["subdomains", "http_headers"])
    values = {s["value"] for s in results["subdomains"]}
    assert "cdn.testcorp.io" in values
    assert "assets.testcorp.io" in values
    assert "api.testcorp.io" in values


def test_subdomain_from_cors_header():
    html = "<html><body>ok</body></html>"
    pages = [{
        "html": html,
        "url": "https://testcorp.io/",
        "timestamp": "20220601120000",
        "response_headers": {
            "access-control-allow-origin": "https://api.testcorp.io",
        },
    }]
    results = extract_all(pages, "testcorp.io", categories=["subdomains", "http_headers"])
    values = {s["value"] for s in results["subdomains"]}
    assert "api.testcorp.io" in values
