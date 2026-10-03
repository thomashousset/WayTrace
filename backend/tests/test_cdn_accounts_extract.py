"""Tests for the cdn_accounts extractor (media/content/search SaaS account ids)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
    return extract_all(pages, "example.com")["cdn_accounts"]


def _find(items: list[dict], vendor: str) -> dict | None:
    return next((it for it in items if it["vendor"] == vendor), None)


# ---------------- Positive ----------------

def test_cloudinary_cloud_name():
    html = '<img src="https://res.cloudinary.com/acme-media/image/upload/v1/hero.jpg">'
    e = _find(_run(html), "cloudinary")
    assert e is not None
    assert e["account_id"] == "acme-media"
    assert e["pivot_url"] == "https://res.cloudinary.com/acme-media/"


def test_imgix_source():
    html = '<img src="https://acmestore.imgix.net/photo.jpg?w=800">'
    e = _find(_run(html), "imgix")
    assert e is not None
    assert e["account_id"] == "acmestore"


def test_contentful_space_id():
    html = '<img src="https://images.ctfassets.net/abc123def456/asset/pic.png">'
    e = _find(_run(html), "contentful")
    assert e is not None
    assert e["account_id"] == "abc123def456"


def test_sanity_project_id():
    html = '<img src="https://cdn.sanity.io/images/pr0j3ct1/production/img.png">'
    e = _find(_run(html), "sanity")
    assert e is not None
    assert e["account_id"] == "pr0j3ct1"


def test_algolia_app_id():
    html = '<script>fetch("https://LATENCY123-dsn.algolia.net/1/indexes")</script>'
    e = _find(_run(html), "algolia")
    assert e is not None
    assert e["account_id"] == "LATENCY123"


def test_cloudflare_images_account_hash():
    html = '<img src="https://imagedelivery.net/Xa9bC2dE3fG4hI5jK6lM7n/photo/public">'
    e = _find(_run(html), "cloudflare_images")
    assert e is not None
    assert e["account_id"] == "Xa9bC2dE3fG4hI5jK6lM7n"


def test_wistia_account():
    html = '<script src="https://acmevideos.wistia.com/embed/config.js"></script>'
    e = _find(_run(html), "wistia")
    assert e is not None
    assert e["account_id"] == "acmevideos"


def test_bunny_pull_zone():
    html = '<img src="https://acme-assets.b-cdn.net/logo.png">'
    e = _find(_run(html), "bunny")
    assert e is not None
    assert e["account_id"] == "acme-assets"


# ---------------- Negative ----------------

def test_cloudinary_demo_still_captured_but_generic_host_not():
    # A plain cloudinary.com marketing link has no cloud name path -> no id.
    html = '<a href="https://cloudinary.com/pricing">pricing</a>'
    assert _run(html) == []


def test_imgix_vendor_infra_subdomain_ignored():
    html = '<a href="https://www.imgix.net/">imgix</a>'
    assert _run(html) == []


def test_algolia_wrong_length_app_id_ignored():
    # 5-char, not the required 10.
    html = '<script>fetch("https://ABC12-dsn.algolia.net/")</script>'
    assert _run(html) == []


def test_plain_cdn_hostname_ignored():
    html = '<img src="https://cdn.jsdelivr.net/npm/lib@1/dist.js">'
    assert _run(html) == []


def test_bunny_www_infra_ignored():
    html = '<a href="https://www.b-cdn.net/">BunnyCDN</a>'
    assert _run(html) == []


def test_unrelated_image_host_ignored():
    html = '<img src="https://images.unsplash.com/photo-123.jpg">'
    assert _run(html) == []
