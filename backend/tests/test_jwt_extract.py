# backend/tests/test_jwt_extract.py
"""Tests for JWT token extraction."""
import base64
import json

import pytest

from services.extractor.jwt_extract import extract_jwts, _JWT_VALID_ALG
from services.extractor.patterns import JWT_RE


def test_jwt_regex_matches_valid_token():
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwiZW1haWwiOiJ0ZXN0QGV4YW1wbGUuY29tIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    assert JWT_RE.search(token) is not None


def test_jwt_regex_no_false_positive_on_short():
    assert JWT_RE.search("eyJhbGci.eyJzdWI.abc") is None


def test_jwt_regex_matches_in_url():
    url = "https://example.com/auth?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    assert JWT_RE.search(url) is not None


def test_jwt_regex_matches_in_html():
    html = '<script>var token = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4iLCJyb2xlIjoic3VwZXIifQ.abc1234567890abcdef";</script>'
    assert JWT_RE.search(html) is not None


def _make_token(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"fakesignature12345678").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def test_extract_jwts_from_html():
    token = _make_token({"sub": "user123", "email": "admin@example.com"})
    html = f'<script>var t = "{token}";</script>'
    results = extract_jwts(html, "http://example.com/", "20200101120000")
    assert len(results) == 1
    assert results[0]["token"] == token
    assert results[0]["claims"]["email"] == "admin@example.com"
    assert results[0]["source"] == "html"


def test_extract_jwts_from_url():
    token = _make_token({"sub": "user123", "role": "admin"})
    url = f"http://example.com/api?token={token}"
    results = extract_jwts("", url, "20200101120000")
    assert len(results) == 1
    assert results[0]["source"] == "url"
    assert results[0]["claims"]["role"] == "admin"


def test_extract_jwts_flags_sensitive_claims():
    token = _make_token({"email": "a@b.com", "password": "hunter2secret", "api_key": "xyzAPIKEY"})
    results = extract_jwts(f"token={token}", "http://example.com/", "20200101120000")
    assert len(results) == 1
    sensitive = results[0]["sensitive_claims"]
    assert "email" in sensitive
    assert "password" in sensitive
    assert "api_key" in sensitive
    # Sensitive values must be masked. never echoed in full.
    assert sensitive["password"] != "hunter2secret"
    assert sensitive["api_key"] != "xyzAPIKEY"
    assert sensitive["password"].endswith("***")


def test_extract_jwts_requires_valid_alg():
    """A base64 blob whose header has no recognised alg is rejected."""
    # header = {"not_jwt": true}  (no alg field)
    bad_header = base64.urlsafe_b64encode(b'{"not_jwt":true}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(b'{"sub":"a"}').rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"x" * 20).rstrip(b"=").decode()
    fake = f"{bad_header}.{body}.{sig}"
    # Must not match the three-part regex OR, if it does, must be rejected
    # by alg validation.
    results = extract_jwts(fake, "http://x.com/", "20200101120000")
    assert results == []


def test_extract_jwts_rejects_unknown_alg():
    """alg: 'MD5' must be rejected. not in JWS spec."""
    header = base64.urlsafe_b64encode(b'{"alg":"MD5","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(b'{"sub":"user1"}').rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"x" * 20).rstrip(b"=").decode()
    fake = f"{header}.{body}.{sig}"
    results = extract_jwts(fake, "http://x.com/", "20200101120000")
    assert results == []


def test_extract_jwts_returns_alg():
    token = _make_token({"sub": "user1"})
    results = extract_jwts(f"token={token}", "http://x.com/", "20200101120000")
    assert len(results) == 1
    assert results[0]["alg"] == "HS256"


def test_extract_jwts_no_match():
    results = extract_jwts("<html>no tokens here</html>", "http://example.com/", "20200101120000")
    assert results == []


def test_extract_jwts_deduplicates():
    token = _make_token({"sub": "user1"})
    html = f"token1={token} and again token2={token}"
    results = extract_jwts(html, "http://example.com/", "20200101120000")
    assert len(results) == 1
