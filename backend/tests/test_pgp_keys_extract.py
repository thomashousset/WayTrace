"""Tests for the pgp_keys extractor."""
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
    return extract_all(pages, "example.com")["pgp_keys"]


def _kinds(items: list[dict]) -> set[str]:
    return {it["kind"] for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_inline_public_key_block():
    html = (
        "<pre>-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        "mQINBFxyz12345abcdefghijklmnopqrstuvwx\n"
        "-----END PGP PUBLIC KEY BLOCK-----</pre>"
    )
    items = _run(html)
    assert "block" in _kinds(items)


def test_detects_fingerprint_grouped_in_4s():
    html = (
        "<p>Fingerprint: ABCD 1234 EF56 7890 ABCD "
        "1234 EF56 7890 ABCD 1234</p>"
    )
    items = _run(html)
    assert "fingerprint" in _kinds(items)


def test_detects_fingerprint_without_spaces():
    # A contiguous 40-hex run is only treated as a fingerprint with PGP context.
    html = "<p>PGP fingerprint: ABCD1234EF567890ABCD1234EF567890ABCD1234</p>"
    items = _run(html)
    assert "fingerprint" in _kinds(items)


def test_detects_keybase_reference():
    html = '<a href="https://keybase.io/alice">keybase</a>'
    items = _run(html)
    assert any(it["kind"] == "keybase" and it["identifier"] == "alice" for it in items)


def test_fingerprint_has_openpgp_pivot():
    html = "<p>ABCD 1234 EF56 7890 ABCD 1234 EF56 7890 ABCD 1234</p>"
    items = _run(html)
    fp = next(it for it in items if it["kind"] == "fingerprint")
    assert fp["pivot_url"].startswith("https://keys.openpgp.org/search?q=0x")
    assert fp["identifier"] == "ABCD1234EF567890"


def test_keybase_pivot_url():
    html = "https://keybase.io/bob42"
    items = _run(html)
    entry = next(it for it in items if it["kind"] == "keybase")
    assert entry["pivot_url"] == "https://keybase.io/bob42"


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_ignores_all_zero_fingerprint():
    html = "<p>0000 0000 0000 0000 0000 0000 0000 0000 0000 0000</p>"
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_ignores_all_f_fingerprint():
    html = "<p>FFFF FFFF FFFF FFFF FFFF FFFF FFFF FFFF FFFF FFFF</p>"
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_ignores_keybase_reserved_slug():
    html = '<a href="https://keybase.io/docs">documentation</a>'
    items = _run(html)
    assert not any(it["kind"] == "keybase" and it["identifier"] == "docs" for it in items)


def test_ignores_short_hex_string():
    html = "<p>deadbeef cafe1234</p>"
    assert _run(html) == []


def test_ignores_random_prose():
    html = "<p>No crypto material here at all.</p>"
    assert _run(html) == []


# ---------------------------------------------------------------------------
# Positive - new capabilities
# ---------------------------------------------------------------------------


def test_detects_private_key_block():
    html = (
        "<pre>-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
        "lQOYBFxyz12345abcdefghijklmnopqrstuvwx\n"
        "-----END PGP PRIVATE KEY BLOCK-----</pre>"
    )
    items = _run(html)
    assert any(it["kind"] == "block" and it.get("armor") == "private" for it in items)


def test_detects_keyid_long_with_context():
    html = "<p>PGP key id: 0xA1B2C3D4E5F60718</p>"
    items = _run(html)
    assert any(it["kind"] == "keyid" and it["identifier"] == "0xA1B2C3D4E5F60718" for it in items)


def test_detects_keyid_short_with_context():
    html = "<code>gpg --recv-keys 0xA1B2C3D4</code>"
    items = _run(html)
    assert any(it["kind"] == "keyid" and it["identifier"] == "0xA1B2C3D4" for it in items)


def test_detects_keyserver_link():
    html = '<a href="https://keys.openpgp.org/search?q=0xA1B2C3D4E5F60718">key</a>'
    items = _run(html)
    assert any(it["kind"] == "keyserver" for it in items)


def test_detects_fingerprint_grouped_in_8s():
    html = "<p>fingerprint ABCD1234 EF567890 ABCD1234 EF567890 ABCD1234</p>"
    items = _run(html)
    assert "fingerprint" in _kinds(items)


def test_detects_pks_lookup_keyserver():
    html = '<a href="https://pgp.example.org/pks/lookup?op=get&search=0xABCD">k</a>'
    items = _run(html)
    assert any(it["kind"] == "keyserver" for it in items)


# ---------------------------------------------------------------------------
# Negative - new guards
# ---------------------------------------------------------------------------


def test_ignores_keyid_without_context():
    html = "<p>theme color token 0xA1B2C3D4E5F60718 in the palette</p>"
    items = _run(html)
    assert not any(it["kind"] == "keyid" for it in items)


def test_ignores_sha1_like_hash_near_git_context():
    # 40-hex that is really a git commit id; hash context must suppress it.
    html = "<p>git commit 1a2b3c4d5e6f70891a2b3c4d5e6f70891a2b3c4d merged</p>"
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_ignores_sha1_like_hash_near_checksum_context():
    html = "<p>sha1 checksum: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b</p>"
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_grouped_fingerprint_still_detected_despite_hash_word_far_away():
    # Grouped formatting is a strong human-printed signal; the hash guard only
    # applies to ambiguous contiguous runs.
    html = "<p>Key fingerprint = ABCD 1234 EF56 7890 ABCD 1234 EF56 7890 ABCD 1234</p>"
    items = _run(html)
    assert "fingerprint" in _kinds(items)


# ---------------------------------------------------------------------------
# Negative - contiguous 40-hex without PGP context (Boris FP report)
# ---------------------------------------------------------------------------


def test_ignores_contiguous_hash_no_context():
    # Bare 40-hex (SHA-1 / asset hash) with zero PGP context must not register.
    html = "<p>6EF6165F6315C49401a2b3c4d5e6f70891a2b3c4d in the build manifest</p>"
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_ignores_sri_asset_hash_no_context():
    html = '<script src="/static/app.9c2c60e2b978c2461a2b3c4d5e6f70891a2b3c4d.js"></script>'
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_ignores_git_object_id_no_context():
    html = "<a href='/tree/da39a3ee5e6b4b0d3255bfef95601890afd80709'>source</a>"
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_ignores_etag_style_hash_no_context():
    html = '<meta name="build" content="0123456789abcdef0123456789abcdef01234567">'
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_ignores_random_40hex_in_prose_no_context():
    html = "<p>token deadc0debeef1234deadc0debeef1234deadc0de saved</p>"
    items = _run(html)
    assert "fingerprint" not in _kinds(items)


def test_contiguous_fingerprint_with_gpg_context_still_detected():
    html = "<code>gpg --recv-key ABCD1234EF567890ABCD1234EF567890ABCD1234</code>"
    items = _run(html)
    assert "fingerprint" in _kinds(items)


def test_grouped_fingerprint_no_context_still_detected():
    # Grouping alone is a strong human-printed PGP signal; no context needed.
    html = "<p>ABCD 1234 EF56 7890 ABCD 1234 EF56 7890 ABCD 1234</p>"
    items = _run(html)
    assert "fingerprint" in _kinds(items)
