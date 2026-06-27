import json
import os
import tempfile
import zlib
import aiosqlite
import pytest
import pytest_asyncio
from db import init_db
from routers.analyze import run_analysis


@pytest_asyncio.fixture
async def seeded_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    html = '<html><body><p>Contact us at admin@testcorp.org</p><a href="/api/v1">API</a></body></html>'
    compressed = zlib.compress(html.encode())
    async with aiosqlite.connect(path) as db:
        await db.execute("INSERT INTO domains (name) VALUES ('example.com')")
        await db.execute(
            "INSERT INTO snapshots (domain_id, url, timestamp, mimetype, selected) "
            "VALUES (1, 'http://example.com/', '20200101120000', 'text/html', 1)"
        )
        await db.execute("INSERT INTO pages (snapshot_id, html, status) VALUES (1, ?, 'done')", (compressed,))
        await db.commit()
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_run_analysis_extracts_email(seeded_db):
    results = await run_analysis(1, seeded_db)
    emails = results.get("emails", [])
    values = [e["value"] for e in emails]
    assert "admin@testcorp.org" in values


@pytest.mark.asyncio
async def test_run_analysis_extracts_endpoints(seeded_db):
    results = await run_analysis(1, seeded_db)
    endpoints = results.get("endpoints", [])
    paths = [e["path"] for e in endpoints]
    assert "/api/v1" in paths


@pytest.mark.asyncio
async def test_run_analysis_stores_findings(seeded_db):
    await run_analysis(1, seeded_db)
    async with aiosqlite.connect(seeded_db) as db:
        cursor = await db.execute("SELECT count(*) FROM findings WHERE domain_id = 1")
        count = (await cursor.fetchone())[0]
    assert count >= 1


@pytest.mark.asyncio
async def test_run_analysis_idempotent(seeded_db):
    await run_analysis(1, seeded_db)
    await run_analysis(1, seeded_db)
    async with aiosqlite.connect(seeded_db) as db:
        cursor = await db.execute("SELECT count(*) FROM findings WHERE domain_id = 1 AND category = 'emails'")
        count = (await cursor.fetchone())[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Persistence coverage. every category produced by the extractor must
# survive `if value is None: continue` in run_analysis. categories whose
# items don't carry a top-level `value` key (assets uses `path`,
# analytics_ids uses `id_value`, etc.) need explicit branches in
# `_item_value`. without them, the finding extracts fine but never lands
# in SQLite. observed in the wild on oteria.fr (analytics_ids extract=2,
# persist=0).
# ---------------------------------------------------------------------------

from routers.analyze import _item_value
from services.extractor.finalize import ALL_CATEGORIES


_PERSIST_FIXTURES = {
    "emails":              {"value": "jane@example.com"},
    "subdomains":          {"value": "api.example.com", "source": "html"},
    "api_keys":            {"type": "Stripe", "value": "sk_live_abc", "tier": "secret"},
    "cloud_buckets":       {"value": "x.s3.amazonaws.com"},
    "analytics_trackers":  {"type": "GA4", "id": "G-AAAAAAAAAA"},
    "endpoints":           {"path": "/admin"},
    "assets":              {"path": "/static/app.css", "type": "css"},
    "social_profiles":     {"platform": "github", "handle": "x", "url": "https://github.com/x"},
    "technologies":        {"technology": "React", "version": "18"},
    "persons":             {"name": "Jane Doe", "context": ""},
    "phones":              {"raw": "+33 1 42 68 53 00", "normalized": "+33142685300"},
    "organizations":       {"name": "Acme Inc", "type": "Org", "url": "https://acme"},
    "addresses":           {"value": "1 rue X", "street": "1 rue X", "city": "Paris", "postal_code": "75001", "country": "FR"},
    "linked_documents":    {"url": "https://x/d.pdf", "extension": "pdf"},
    "html_comments":       {"comment": "build 12.3"},
    "meta_info":           {"property": "description", "content": "About"},
    "jwt_tokens":          {"token": "eyJ.eyJ.x", "claims": {}, "sensitive_claims": [], "source": "html"},
    "directory_listings":  {"path": "/files/", "server_type": "Apache", "url": "https://x/files/"},
    "hidden_fields":       {"name": "csrf", "value": "tok", "form_action": "/post"},
    "internal_ips":        {"ip": "10.0.0.1", "context": "snippet"},
    "adsense_ids":         {"type": "publisher", "id": "ca-pub-1234567890123456"},
    "verification_tags":   {"service": "google", "verification_id": "abc"},
    "iframe_sources":      {"url": "https://yt/x", "service": "YouTube", "domain": "yt"},
    "js_urls":             {"url": "https://api/x", "context": "fetch"},
    "connection_strings":  {"type": "postgres", "value": "postgres://x", "has_credentials": True},
    "crypto_addresses":    {"type": "btc", "address": "bc1q...", "validated": True, "validation_method": "bech32"},
    "favicons":            {"url": "/favicon.ico", "type": "icon", "sizes": None},
    "outgoing_links":      {"url": "https://ext", "domain": "ext.com", "category": "social", "service": ""},
    "hosting":             {"provider": "Cloudflare", "signal": "header"},
    "http_headers":        {"type": "Server", "header": "Server", "value": "nginx/1.27"},
    "french_business_ids": {"type": "siren", "value": "123456789", "raw": "123 456 789", "validated": True},
    "analytics_ids":       {"platform": "ga4", "id_value": "G-AAAAAAAAAA", "pivot_url": "https://ga"},
    "cookie_consent":      {"platform": "OneTrust", "account_id": "abc", "pivot_url": "https://ot"},
    "rss_feeds":           {"url": "https://x/feed.xml", "feed_type": "rss", "title": "T"},
    "github_repos":        {"owner": "acme", "repo": "core", "raw_url": "https://github.com/acme/core", "pivot_url": "https://github.com/acme/core"},
    "sitemaps_and_robots": {"url": "https://x/sitemap.xml", "kind": "sitemap"},
    "pgp_keys":            {"kind": "fingerprint", "identifier": "ABCD" * 10, "pivot_url": "https://x"},
    "bug_bounty_programs": {"platform": "hackerone", "handle": "acme", "pivot_url": "https://h1/acme"},
    "captcha_providers":   {"provider": "recaptcha", "sitekey": "6Lc...", "pivot_url": ""},
    "status_pages":        {"provider": "statuspage.io", "slug": "acme", "pivot_url": "https://acme.statuspage.io"},
    "job_boards":          {"platform": "greenhouse", "slug": "acme", "pivot_url": "https://boards.greenhouse.io/acme"},
    "auth_providers":      {"platform": "auth0", "tenant": "acme", "pivot_url": "https://acme.auth0.com"},
    "html_titles":         {"property": "title", "content": "Example Corp. Home"},
}


def test_persist_fixtures_cover_all_categories():
    """If ALL_CATEGORIES grows but this map doesn't, fail loudly. silent
    persistence loss is the exact bug we're guarding against here."""
    missing = sorted(set(ALL_CATEGORIES) - set(_PERSIST_FIXTURES))
    assert not missing, f"missing persistence fixture for: {missing}"


@pytest.mark.parametrize("category,item", sorted(_PERSIST_FIXTURES.items()))
def test_item_value_returns_non_empty_for_every_category(category, item):
    value = _item_value(category, item)
    assert value is not None, f"{category}: _item_value returned None"
    assert isinstance(value, str), f"{category}: expected str, got {type(value).__name__}"
    assert value, f"{category}: _item_value returned empty string"
