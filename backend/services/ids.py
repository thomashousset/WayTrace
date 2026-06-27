"""Generate URL-safe public IDs for scans.

url_id is what appears in the public path /s/{url_id} and identifies a
scan to anyone who has been given the URL. It must be unguessable so
people can't enumerate other users' scans.
"""
from __future__ import annotations

import secrets


def generate_url_id() -> str:
    """Return a 24-char URL-safe random identifier (~144 bits entropy)."""
    return secrets.token_urlsafe(18)
