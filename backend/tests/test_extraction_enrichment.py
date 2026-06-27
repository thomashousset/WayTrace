"""Integration tests for all 7 new extraction categories."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all, compute_highlights


RICH_HTML = """
<html>
<head>
    <meta name="google-site-verification" content="dBw5CvburAxi537Rp9qi5uG2174Vb6JwHwIRwPSLIK8">
    <meta name="facebook-domain-verification" content="abcdef1234567890abcdef1234567890">
</head>
<body>
    <form action="/api/submit">
        <input type="hidden" name="csrf_token" value="a1b2c3d4e5f6a7b8c9d0">
        <input type="hidden" name="redirect" value="https://internal.example.com/dashboard">
    </form>

    <p>Backend server: 10.0.1.50 port 8080</p>
    <p>Cache at 192.168.1.100</p>

    <script>
    google_ad_client = "ca-pub-1234567890123456";
    </script>
    <ins class="adsbygoogle" data-ad-slot="9876543210"></ins>

    <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
    <iframe src="https://calendly.com/team/meeting"></iframe>

    <script>
    var config = {
        apiUrl: "https://api.internal.example.com/v2",
        wsEndpoint: "https://ws.example.com/socket"
    };
    fetch("https://staging.example.com/api/users");
    </script>

    <p>DB: mysql://admin:s3cret@db.prod.internal:3306/myapp</p>
    <p>Cache: redis://:hunter2@redis.prod.internal:6379/0</p>
</body>
</html>
"""

_PAGES = [{"html": RICH_HTML, "url": "https://example.com/", "timestamp": "20220601120000"}]


def test_all_new_categories_extracted():
    results = extract_all(_PAGES, "example.com")

    # hidden_fields: >= 2, names include csrf_token and redirect
    hidden = results["hidden_fields"]
    assert len(hidden) >= 2
    names = {f["name"] for f in hidden}
    assert "csrf_token" in names
    assert "redirect" in names

    # internal_ips: >= 2, includes 10.0.1.50 and 192.168.1.100
    ips = results["internal_ips"]
    assert len(ips) >= 2
    ip_values = {entry["ip"] for entry in ips}
    assert "10.0.1.50" in ip_values
    assert "192.168.1.100" in ip_values

    # adsense_ids: >= 1, includes adsense_publisher type
    ads = results["adsense_ids"]
    assert len(ads) >= 1
    ad_types = {a["type"] for a in ads}
    assert "adsense_publisher" in ad_types

    # verification_tags: >= 2, services include google and facebook
    verif = results["verification_tags"]
    assert len(verif) >= 2
    services = {v["service"] for v in verif}
    assert "google" in services
    assert "facebook" in services

    # iframe_sources: >= 2, services include youtube and calendly
    iframes = results["iframe_sources"]
    assert len(iframes) >= 2
    iframe_services = {i["service"] for i in iframes}
    assert "youtube" in iframe_services
    assert "calendly" in iframe_services

    # js_urls: >= 1, at least one URL contains "api.internal"
    js_urls = results["js_urls"]
    assert len(js_urls) >= 1
    all_js_url_strs = [u["url"] for u in js_urls]
    assert any("api.internal" in url for url in all_js_url_strs)

    # connection_strings: >= 2, types include mysql and redis, credentials masked
    conns = results["connection_strings"]
    assert len(conns) >= 2
    conn_types = {c["type"] for c in conns}
    assert "mysql" in conn_types
    assert "redis" in conn_types
    # Credentials must be masked - no plaintext passwords
    for conn in conns:
        assert "s3cret" not in conn["value"]
        assert "hunter2" not in conn["value"]
    # At least one connection string has masked credentials marker
    assert any("****" in c["value"] for c in conns)


def test_new_categories_in_highlights():
    results = extract_all(_PAGES, "example.com")
    highlights = compute_highlights(results, "example.com")

    hl_categories = {h["category"] for h in highlights}
    hl_by_cat = {h["category"]: h for h in highlights}

    # connection_strings with credentials should appear as LEAK
    assert "connection_strings" in hl_categories
    assert hl_by_cat["connection_strings"]["severity"] == "LEAK"

    # internal_ips should appear in highlights
    assert "internal_ips" in hl_categories


def test_existing_categories_still_work():
    html = """
<html><body>
<p>Contact: admin@acme-corp.io</p>
<p>Call us: +33 1 42 68 53 00</p>
<a href="https://twitter.com/example">Twitter</a>
<script src="https://www.googletagmanager.com/gtag/js?id=G-ABC123DEF4"></script>
</body></html>
"""
    pages = [{"html": html, "url": "https://acme-corp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "acme-corp.io")

    # emails still extracted
    email_values = [e["value"] for e in results["emails"]]
    assert "admin@acme-corp.io" in email_values

    # phones still extracted
    assert len(results["phones"]) >= 1

    # social_profiles still extracted
    platforms = [s["platform"] for s in results["social_profiles"]]
    assert "twitter" in platforms
