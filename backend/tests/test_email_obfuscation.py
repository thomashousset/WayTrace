"""Email obfuscation coverage: Cloudflare, HTML entities, [at]/[dot], JS concat."""
from __future__ import annotations

from services.extractor import extract_all
from services.extractor.extract import _decode_cloudflare_email


# --- Cloudflare __cf_email__ --------------------------------------------

def test_decode_cloudflare_email_round_trip():
    """Encode a known email the way Cloudflare does, then decode."""
    email = "admin@testcorp.io"
    key = 0x6b
    encoded = bytes([key]) + bytes(ord(c) ^ key for c in email)
    assert _decode_cloudflare_email(encoded.hex()) == email


def test_decode_cloudflare_email_invalid_hex_returns_none():
    assert _decode_cloudflare_email("nothex") is None


def test_decode_cloudflare_email_too_short_returns_none():
    assert _decode_cloudflare_email("aa") is None  # 1 byte = only the key


def test_decode_cloudflare_email_non_email_returns_none():
    """Random hex that doesn't XOR-decode to an email must be rejected."""
    assert _decode_cloudflare_email("00aabbccddeeff11") is None


def test_cf_email_in_html_is_extracted():
    email = "foo@example.io"
    key = 0x2c
    encoded = bytes([key]) + bytes(ord(c) ^ key for c in email)
    html = f'<a class="__cf_email__" data-cfemail="{encoded.hex()}">[email protected]</a>'
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    values = [e["value"] for e in results["emails"]]
    assert email in values


# --- HTML numeric entities ----------------------------------------------

def test_email_numeric_entity_at_sign():
    html = "<p>Contact: admin&#64;testcorp.io</p>"
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    assert "admin@testcorp.io" in [e["value"] for e in results["emails"]]


def test_email_hex_entity_at_sign():
    html = "<p>Contact: admin&#x40;testcorp.io</p>"
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    assert "admin@testcorp.io" in [e["value"] for e in results["emails"]]


# --- [at]/[dot] textual obfuscation -------------------------------------

def test_email_bracketed_at_dot():
    html = "<p>Reach us: contact [at] testcorp [dot] io</p>"
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    assert "contact@testcorp.io" in [e["value"] for e in results["emails"]]


def test_email_paren_at_dot():
    html = "<p>contact(at)testcorp(dot)io</p>"
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    assert "contact@testcorp.io" in [e["value"] for e in results["emails"]]


def test_email_obfuscation_prose_false_positive():
    """Prose like 'Click [at] the [dot] below' mustn't emit an email."""
    html = "<p>Click [at] the [dot] below</p>"
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    assert results["emails"] == []


# --- JS concatenation ---------------------------------------------------

def test_email_js_concat():
    html = '<script>var e = "support"+"@"+"testcorp.io";</script>'
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    assert "support@testcorp.io" in [e["value"] for e in results["emails"]]


# --- Dedup across branches ----------------------------------------------

def test_obfuscated_and_plain_email_dedup_to_one():
    """Same email showing up in plain + entity + cf form = one entry."""
    email = "dup@testcorp.io"
    key = 0x33
    cf_hex = (bytes([key]) + bytes(ord(c) ^ key for c in email)).hex()
    html = (
        f"<p>{email}</p>"
        f"<p>dup&#64;testcorp.io</p>"
        f'<a data-cfemail="{cf_hex}"></a>'
    )
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io", categories=["emails"])
    values = [e["value"] for e in results["emails"]]
    assert values.count(email) == 1


# --- JS module specs must NOT register as emails (Boris FP report) -------

def _emails(html: str) -> list[str]:
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    return [e["value"] for e in extract_all(pages, "testcorp.io", categories=["emails"])["emails"]]


def test_js_module_lodash_versioned_not_email():
    html = '<script src="lodash@4.17.15-03135adcc7a9a4897385bbd3c17aaeeda686c07cf714fa796406c5055b6860b8.js"></script>'
    assert _emails(html) == []


def test_js_module_highcharts_versioned_not_email():
    html = '<link href="highcharts@10.3.3-a737922aa76dcbf96f5e4ab10645133596ec4351bf59c96868ff2b35da77faa8.js">'
    assert _emails(html) == []


def test_js_module_domurl_versioned_not_email():
    html = "domurl@2.3.4-9d77003f94c76ee1a306c250b2d4f096a350d074ca16a04be37515a1b9aeab9e.js"
    assert _emails(html) == []


def test_js_module_floating_vue_versioned_not_email():
    html = "floating-vue@5.2.0-e77ede19746bb8a133dc998d55fee154186f9bdf5cc7f8f1967f96f882ea6dd4.js"
    assert _emails(html) == []


def test_js_module_moment_precise_range_versioned_not_email():
    html = "moment-precise-range@1.3.0-cc57e4f1179420f545d6490ebb0a6bfd365547aae094c34bce5d965c17541657.js"
    assert _emails(html) == []


def test_js_module_scoped_long_name_versioned_not_email():
    html = "moment-timezone-with-data-10-year-range@0.5.45-0c81d25134d1009937772a4c282e46f9faa66e886f1d0c7da0901398109fa54d.js"
    assert _emails(html) == []


def test_css_module_versioned_not_email():
    html = "tailwind@3.4.1-deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef.css"
    assert _emails(html) == []


def test_plain_js_file_extension_local_part_not_email():
    """A name@host.js with no version must also be rejected (.js TLD)."""
    html = "<p>vendor@bundle.js</p>"
    assert _emails(html) == []


# --- Real emails must still pass (anti-regression) -----------------------

def test_real_email_with_digits_in_domain_still_extracted():
    html = "<p>contact@2600.eu</p>"
    assert "contact@2600.eu" in _emails(html)


def test_real_email_numeric_local_part_still_extracted():
    html = "<p>info@123-reg.co.uk</p>"
    assert "info@123-reg.co.uk" in _emails(html)


def test_real_named_email_still_extracted():
    html = "<p>jane.doe@testcorp.io</p>"
    assert "jane.doe@testcorp.io" in _emails(html)
