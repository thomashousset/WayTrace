"""Tests for the cloud_buckets extractor (_cat_cloud_buckets).

Covers the four supported providers via S3_RE, GCS_RE, AZURE_RE and
DO_SPACES_RE. Each emitted item is a dict whose match string lives under
the ``value`` key (lower-cased by the extractor).
"""
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
    return extract_all(pages, "example.com")["cloud_buckets"]


def _values(items: list[dict]) -> set[str]:
    return {it["value"] for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_s3_virtual_hosted_url():
    html = '<a href="https://my-bucket.s3.amazonaws.com/file.txt">x</a>'
    assert "my-bucket.s3.amazonaws.com/file.txt" in _values(_run(html))


def test_detects_s3_regional_dotted_endpoint():
    html = "https://my-bucket.s3.us-west-2.amazonaws.com/key"
    assert "my-bucket.s3.us-west-2.amazonaws.com/key" in _values(_run(html))


def test_detects_s3_regional_dashed_endpoint():
    html = "https://my-bucket.s3-eu-west-1.amazonaws.com/key"
    assert "my-bucket.s3-eu-west-1.amazonaws.com/key" in _values(_run(html))


def test_detects_s3_website_endpoint():
    html = "https://mybucket.s3-website-us-east-1.amazonaws.com/"
    assert "mybucket.s3-website-us-east-1.amazonaws.com/" in _values(_run(html))


def test_detects_gcs_bucket():
    html = "https://storage.googleapis.com/my-bucket/object.png"
    # GCS_RE only captures the host + bucket path segment, not the object.
    assert "storage.googleapis.com/my-bucket" in _values(_run(html))


def test_detects_azure_blob():
    html = "https://myaccount.blob.core.windows.net/container/blob"
    assert "myaccount.blob.core.windows.net/container/blob" in _values(_run(html))


def test_detects_digitalocean_spaces():
    html = "https://myspace.nyc3.digitaloceanspaces.com/file"
    assert "myspace.nyc3.digitaloceanspaces.com/file" in _values(_run(html))


def test_detects_case_insensitively_and_lowercases_value():
    # Mixed-case input must still match and be normalised to lowercase.
    html = "https://MySpace.NYC3.DigitalOceanSpaces.com/photo.jpg"
    assert "myspace.nyc3.digitaloceanspaces.com/photo.jpg" in _values(_run(html))


# ---------------------------------------------------------------------------
# False positives, must NOT be detected
# ---------------------------------------------------------------------------


def test_ignores_plain_website_url():
    html = "https://www.example.com/page"
    assert _run(html) == []


def test_ignores_cloudfront_cdn():
    html = "https://d111111abcdef8.cloudfront.net/img.png"
    assert _run(html) == []


def test_ignores_google_fonts_cdn():
    # fonts.googleapis.com is a CDN host, not a storage bucket.
    html = '<link href="https://fonts.googleapis.com/css?family=Roboto">'
    assert _run(html) == []


def test_ignores_bare_s3_word_in_prose():
    html = "<p>the s3 bucket is configured elsewhere</p>"
    assert _run(html) == []

def test_ignores_blob_javascript_keyword():
    # The Blob constructor / object-URL keyword must not look like Azure.
    html = "<script>const b = new Blob([data]); blob core stuff</script>"
    assert _run(html) == []


def test_ignores_s3_substring_inside_word():
    # No dot before "s3", so "translates3things" is not a bucket host.
    html = "<p>translates3things and friends</p>"
    assert _run(html) == []


def test_ignores_bare_amazonaws_without_s3_subdomain():
    html = "<p>see amazonaws.com/foo for details</p>"
    assert _run(html) == []


# ---------------------------------------------------------------------------
# Behaviour notes (documented current behaviour, not asserted as desirable)
# ---------------------------------------------------------------------------


def test_path_style_s3_url_detected():
    # Path-style S3 (bucket in the path: https://s3.amazonaws.com/<bucket>/key)
    # is detected alongside virtual-hosted URLs.
    html = "https://s3.amazonaws.com/my-bucket/key.txt"
    assert any("s3.amazonaws.com/my-bucket" in v for v in _values(_run(html)))


def test_non_aws_s3_label_rejected():
    # A ".s3-" label on a non-AWS host must not be mistaken for a bucket;
    # S3_RE is anchored on amazonaws.com.
    html = "<p>see assets.s3-static.cdn/app.js for more</p>"
    assert _run(html) == []
