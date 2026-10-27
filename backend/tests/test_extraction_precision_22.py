"""Precision fixes found by the v2.2 audit, each with what used to come out.

- A link to evilexample.com counted as the target example.com (bare endswith),
  so its /admin/login reached the admin endpoints highlight.
- A Google AIza browser key ranked LEAK, though Google documents embedding it.
- AWS's documentation key AKIAIOSFODNN7EXAMPLE ranked LEAK.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.highlights import compute_highlights

DOMAIN = "example.com"
GOOGLE_KEY = "AIzaSyD-" + "abcdefghijklmnopqrstuvwxyz01234"  # AIza + 35


def _run(html):
    pages = [{"html": html, "url": f"https://{DOMAIN}/", "timestamp": "20220601120000"}]
    res = extract_all(pages, DOMAIN)
    return res, compute_highlights(res, DOMAIN)


def _paths(html):
    return {e["path"] for e in _run(html)[0]["endpoints"]}


def test_a_lookalike_domain_is_not_the_target():
    assert "/admin/login" not in _paths('<a href="https://evilexample.com/admin/login">x</a>')
    assert "/admin/login" not in _paths('<a href="https://example.com.evil.net/admin/login">x</a>')


def test_the_target_and_its_subdomains_still_count():
    assert "/admin/login" in _paths('<a href="https://example.com/admin/login">x</a>')
    assert "/admin/login" in _paths('<a href="https://shop.example.com/admin/login">x</a>')
    assert "/admin/login" in _paths('<a href="/admin/login">x</a>')


def test_a_google_browser_key_is_a_pivot_not_a_leak():
    res, hl = _run(f'<script>var k="{GOOGLE_KEY}";</script>')
    [key] = [k for k in res["api_keys"] if k["type"] == "Google_API"]
    assert key["tier"] == "public"
    assert not [h for h in hl if h.get("severity") == "LEAK" and h.get("category") == "api_keys"]


def test_aws_documentation_key_is_ignored():
    res, _ = _run('<pre>aws_access_key_id = AKIAIOSFODNN7EXAMPLE</pre>')
    assert [k for k in res["api_keys"] if k["type"] == "AWS"] == []


def test_a_real_shaped_aws_key_still_leaks():
    res, hl = _run('<script>var a="AKIA2E0A8F3B244C9986";</script>')
    [key] = [k for k in res["api_keys"] if k["type"] == "AWS"]
    assert key["tier"] == "secret"
    assert [h for h in hl if h.get("severity") == "LEAK" and h.get("category") == "api_keys"]
