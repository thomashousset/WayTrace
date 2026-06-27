# backend/services/extractor/connstring_extract.py
"""Connection string extraction from HTML content."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .patterns import CONNSTRING_RE

_TRAILING_PUNCT = re.compile(r"[.,;:'\")\]}>]+$")

_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def _strip_trailing(url: str) -> str:
    return _TRAILING_PUNCT.sub("", url)


def _mask_password(url: str) -> str:
    """Replace :password@ with :****@ in the URL string."""
    # Handles both user:pass@host and :pass@host (password-only) formats
    return re.sub(r"(://[^:@/]*):([^@]+)@", r"\1:****@", url)


def _is_local_no_creds(parsed) -> bool:
    host = (parsed.hostname or "").lower()
    return host in _LOCAL_HOSTS and not parsed.password and not parsed.username


def extract_connection_strings(html: str) -> list[dict]:
    seen_raw: set[str] = set()
    results: list[dict] = []

    for match in CONNSTRING_RE.finditer(html):
        raw = _strip_trailing(match.group(0))

        if raw in seen_raw:
            continue
        seen_raw.add(raw)

        try:
            parsed = urlparse(raw)
        except ValueError:
            continue

        if _is_local_no_creds(parsed):
            continue

        has_credentials = bool(parsed.username or parsed.password)
        proto = parsed.scheme.lower()

        results.append({
            "type": proto,
            "value": _mask_password(raw),
            "has_credentials": has_credentials,
        })

    return results
