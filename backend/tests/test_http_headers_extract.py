"""Tests for HTTP response header extraction."""
from services.extractor.http_headers_extract import (
    extract_http_headers,
    _parse_cookie_names,
    _is_valid_cookie_name,
)


# --- Positive cases ---------------------------------------------------------

def test_server_header():
    results = extract_http_headers({"server": "nginx/1.18.0"})
    assert len(results) == 1
    assert results[0]["type"] == "server"
    assert results[0]["value"] == "nginx/1.18.0"


def test_x_powered_by():
    results = extract_http_headers({"x-powered-by": "PHP/7.4.3"})
    assert any(r["type"] == "x_powered_by" and "PHP" in r["value"] for r in results)


def test_aspnet_version():
    results = extract_http_headers({
        "x-aspnet-version": "4.0.30319",
        "x-aspnetmvc-version": "5.2",
    })
    types = {r["type"] for r in results}
    assert "aspnet_version" in types
    assert "aspnetmvc_version" in types


def test_hsts():
    results = extract_http_headers({
        "strict-transport-security": "max-age=31536000; includeSubDomains; preload",
    })
    assert any(r["type"] == "hsts" for r in results)


def test_csp():
    results = extract_http_headers({
        "content-security-policy": "default-src 'self'; script-src 'self' 'unsafe-inline'",
    })
    assert any(r["type"] == "csp" for r in results)


def test_via_header():
    results = extract_http_headers({"via": "1.1 varnish, 1.1 cloudfront"})
    assert any(r["type"] == "via" for r in results)


def test_cloudflare_fingerprint():
    results = extract_http_headers({
        "server": "cloudflare",
        "cf-ray": "8abc12345def-SJC",
        "cf-cache-status": "HIT",
    })
    types = {r["type"] for r in results}
    assert "server" in types
    assert "cf_ray" in types
    assert "cf_cache" in types


def test_vercel_fingerprint():
    results = extract_http_headers({
        "x-vercel-id": "sfo1::iad1::abc",
        "x-vercel-cache": "HIT",
    })
    types = {r["type"] for r in results}
    assert "vercel_id" in types
    assert "vercel_cache" in types


def test_netlify_fingerprint():
    results = extract_http_headers({"x-nf-request-id": "01HZABC123"})
    assert any(r["type"] == "netlify_id" for r in results)


def test_github_pages_fingerprint():
    results = extract_http_headers({"x-github-request-id": "ABCD:1234"})
    assert any(r["type"] == "github_req" for r in results)


def test_akamai_fingerprint():
    results = extract_http_headers({"x-akamai-transformed": "9 1234 0 pmb=mRUM"})
    assert any(r["type"] == "akamai" for r in results)


def test_security_headers_bundle():
    results = extract_http_headers({
        "x-frame-options": "DENY",
        "x-content-type-options": "nosniff",
        "x-xss-protection": "1; mode=block",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "geolocation=(), camera=()",
    })
    types = {r["type"] for r in results}
    assert types == {"x_frame", "x_content_type", "x_xss", "referrer", "permissions"}


def test_drupal_detection():
    results = extract_http_headers({
        "x-drupal-cache": "HIT",
        "x-drupal-dynamic-cache": "HIT",
    })
    types = {r["type"] for r in results}
    assert "drupal_cache" in types
    assert "drupal_dynamic" in types


# --- Case insensitivity -----------------------------------------------------

def test_case_insensitive_header_names():
    results = extract_http_headers({"Server": "nginx", "X-Powered-By": "Rails"})
    types = {r["type"] for r in results}
    assert "server" in types
    assert "x_powered_by" in types


# --- Cookie name extraction -------------------------------------------------

def test_single_cookie_name():
    results = extract_http_headers({
        "set-cookie": "sessionid=abc123; Path=/; HttpOnly",
    })
    cookies = [r["value"] for r in results if r["type"] == "cookie_name"]
    assert cookies == ["sessionid"]


def test_multiple_cookie_names_comma_separated():
    results = extract_http_headers({
        "set-cookie": "sess=abc; Path=/, csrf=xyz; HttpOnly, tracking=1",
    })
    cookies = {r["value"] for r in results if r["type"] == "cookie_name"}
    assert cookies == {"sess", "csrf", "tracking"}


def test_cookie_with_expires_containing_comma():
    # "Expires=Mon, 01 Jan 2030..." has a comma but isn't a cookie separator
    results = extract_http_headers({
        "set-cookie": "session=abc; Expires=Wed, 09 Jun 2021 10:18:14 GMT; Path=/",
    })
    cookies = {r["value"] for r in results if r["type"] == "cookie_name"}
    assert cookies == {"session"}


def test_cookie_values_never_captured():
    results = extract_http_headers({
        "set-cookie": "PHPSESSID=very-secret-value-12345; Path=/",
    })
    for r in results:
        if r["type"] == "cookie_name":
            assert "very-secret-value" not in r["value"]
            assert r["value"] == "PHPSESSID"


# --- Negative / false-positive rejection ------------------------------------

def test_empty_headers_dict():
    assert extract_http_headers({}) == []


def test_none_headers():
    assert extract_http_headers(None) == []


def test_unknown_header_skipped():
    results = extract_http_headers({"x-custom-weird-header": "value"})
    assert results == []


def test_empty_server_value_skipped():
    results = extract_http_headers({"server": ""})
    assert results == []


def test_whitespace_only_value_skipped():
    results = extract_http_headers({"server": "   "})
    assert results == []


def test_non_string_value_skipped():
    # Guards against accidentally stringifying a non-string
    results = extract_http_headers({"server": 12345})
    assert results == []


def test_value_truncation():
    long_csp = "default-src 'self'; " + "a " * 1000
    results = extract_http_headers({"content-security-policy": long_csp})
    assert len(results) == 1
    assert len(results[0]["value"]) <= 500


# --- Cookie name validation -------------------------------------------------

def test_is_valid_cookie_name_valid():
    assert _is_valid_cookie_name("sessionid")
    assert _is_valid_cookie_name("csrf_token")
    assert _is_valid_cookie_name("ab.cd-ef")


def test_is_valid_cookie_name_invalid():
    assert not _is_valid_cookie_name("")
    assert not _is_valid_cookie_name("has space")
    assert not _is_valid_cookie_name("has=equals")
    assert not _is_valid_cookie_name("a" * 70)  # too long


def test_parse_cookie_names_empty():
    assert _parse_cookie_names("") == []


# Integration: the full pipeline must populate http_headers from a page's
# response_headers (regression guard for the scraper->extract_all wiring).
def test_http_headers_populated_via_extract_all():
    from services.extractor import extract_all
    pages = [{
        "html": "<html><body>hi</body></html>",
        "url": "https://example.com/",
        "timestamp": "20220601120000",
        "response_headers": {
            "server": "nginx",
            "x-powered-by": "PHP/8.1",
            "set-cookie": "sessionid=abc123; Path=/; HttpOnly",
        },
    }]
    out = extract_all(pages, "example.com")["http_headers"]
    assert out, "http_headers should be populated from response_headers"
    assert any(i["value"] == "nginx" for i in out)
    assert any(i["type"] == "cookie_name" and i["value"] == "sessionid" for i in out)


def test_http_headers_empty_without_response_headers():
    from services.extractor import extract_all
    pages = [{"html": "<html></html>", "url": "https://example.com/", "timestamp": "20220601120000"}]
    assert extract_all(pages, "example.com")["http_headers"] == []
