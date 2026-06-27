# backend/services/extractor/jwt_extract.py
"""JWT token extraction from URLs and HTML content."""
from __future__ import annotations

import base64
import binascii
import json

from .patterns import JWT_RE

SENSITIVE_CLAIM_KEYS = {
    "email", "password", "api_key", "access_token", "refresh_token",
    "session_id", "secret", "credential", "username", "passwd",
}

INTERESTING_CLAIM_KEYS = {
    "sub", "name", "role", "scope", "iss", "aud", "exp", "iat",
} | SENSITIVE_CLAIM_KEYS

# JWS defines these algorithms; anything outside makes the base64 match
# almost certainly a false positive (a random eyJ...base64 blob in JSON).
_JWT_VALID_ALG = {
    "HS256", "HS384", "HS512",
    "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512",
    "ES256", "ES256K", "ES384", "ES512",
    "EdDSA",
    "none",
}

# Values in sensitive claims often contain literal credentials; don't
# echo them raw. Keep a short prefix for correlation + redact the rest.
def _mask_sensitive(value):
    if not isinstance(value, str):
        return value
    if len(value) <= 4:
        return "***"
    return value[:4] + "***"


def _decode_jwt_part(part: str) -> dict | None:
    padding = 4 - len(part) % 4
    if padding != 4:
        part += "=" * padding
    try:
        decoded = base64.urlsafe_b64decode(part)
        return json.loads(decoded)
    # binascii.Error is the actual class urlsafe_b64decode raises on
    # malformed input in Py3; ValueError was only partially correct.
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return None


def extract_jwts(html: str, page_url: str, timestamp: str) -> list[dict]:
    results: list[dict] = []
    seen_tokens: set[str] = set()

    def _process(token: str, source: str) -> None:
        if token in seen_tokens:
            return
        seen_tokens.add(token)
        parts = token.split(".")
        # Real JWS = 3 parts (header.payload.sig), JWE = 5. Anything else
        # is a base64 coincidence, reject.
        if len(parts) not in (3, 5):
            return
        header = _decode_jwt_part(parts[0])
        # Require a JOSE header with a recognised alg; this is the single
        # strongest filter against the 'eyJ' base64 collision family.
        if not isinstance(header, dict):
            return
        alg = header.get("alg")
        if alg not in _JWT_VALID_ALG:
            return
        payload = _decode_jwt_part(parts[1])
        if not isinstance(payload, dict):
            return
        interesting = {k: v for k, v in payload.items() if k in INTERESTING_CLAIM_KEYS}
        sensitive = {
            k: _mask_sensitive(v) for k, v in payload.items()
            if k in SENSITIVE_CLAIM_KEYS
        }
        results.append({
            "token": token,
            "alg": alg,
            "claims": interesting if interesting else payload,
            "sensitive_claims": sensitive,
            "source": source,
            "timestamp": timestamp,
        })

    for match in JWT_RE.finditer(page_url):
        _process(match.group(0), "url")
    if html:
        for match in JWT_RE.finditer(html):
            _process(match.group(0), "html")

    return results
