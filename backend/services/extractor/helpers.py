"""Shared helper functions for the extractor."""
from __future__ import annotations

import re

from .patterns import EMAIL_EXCLUDE, EMAIL_PLACEHOLDER_DOMAINS, IMAGE_EXTENSIONS, ASSET_EXTENSIONS, EMAIL_SEMVER_DOMAIN_RE, WAYBACK_TOOLBAR_RE, WAYBACK_SCRIPT_RE, WAYBACK_DIV_RE


def ts_to_month(timestamp: str) -> str:
    return f"{timestamp[:4]}-{timestamp[4:6]}"


def update_entity(
    store: dict, key: str, month: str, extra: dict | None = None
) -> None:
    if key in store:
        entry = store[key]
        entry["occurrences"] += 1
        if month < entry["first_seen"]:
            entry["first_seen"] = month
        if month > entry["last_seen"]:
            entry["last_seen"] = month
    else:
        entry = {
            "first_seen": month,
            "last_seen": month,
            "occurrences": 1,
        }
        if extra:
            entry.update(extra)
        store[key] = entry


def is_email_excluded(email: str) -> bool:
    email_lower = email.lower()
    local = email_lower.split("@")[0]
    domain = email_lower.split("@")[1] if "@" in email_lower else ""
    # Exclude placeholder local parts
    if any(exc in local for exc in EMAIL_EXCLUDE):
        return True
    # Exclude placeholder domains
    if domain in EMAIL_PLACEHOLDER_DOMAINS:
        return True
    # Exclude image file extensions
    for ext in IMAGE_EXTENSIONS:
        if email_lower.endswith(ext):
            return True
    # Exclude JS/CSS/asset module specs (``lodash@4.17.15-<hash>.js``): the
    # domain ends in a code/asset extension and/or starts with a semver.
    for ext in ASSET_EXTENSIONS:
        if email_lower.endswith(ext):
            return True
    if EMAIL_SEMVER_DOMAIN_RE.match(domain):
        return True
    return False


def normalize_phone(raw: str) -> str:
    return re.sub(r"[^\d+]", "", raw)


def canonicalize_phone_key(digits_only: str) -> str:
    """Return a canonical dedup key for a phone number.

    The default extractor key was the raw digits-only string, which kept
    ``0188615589`` and ``+33188615589`` as two separate entities for the
    same number (oteria.fr v2 scan emitted both with 652 + 28 occurrences).
    This helper folds the common French national/international variants
    onto a single E.164-style key (``33188615589``) so dedup works:

    - ``+33XXXXXXXXX`` (digits_only == ``33XXXXXXXXX``) -> ``33XXXXXXXXX``
    - ``0XXXXXXXXX`` (10 national digits with FR-shape lead) -> ``33XXXXXXXXX``
    - everything else -> unchanged digits

    The FR-shape gate (``[1-79]`` after the leading 0) avoids miscoercing
    Belgian/Dutch 10-digit numbers (which would start with 04, 05 etc.
    and could otherwise satisfy the simpler ``startswith('0')`` check).
    """
    if not digits_only:
        return digits_only
    if len(digits_only) == 11 and digits_only.startswith("33"):
        return digits_only
    if (
        len(digits_only) == 10
        and digits_only.startswith("0")
        and digits_only[1] in "123456789"
        and digits_only[1] != "8"  # 08 premium / oddities stay raw
    ):
        return "33" + digits_only[1:]
    return digits_only


def phone_display(canonical_key: str, raw: str) -> str:
    """Pretty-print a canonical phone key for display, preferring +CC form."""
    if canonical_key.startswith("33") and len(canonical_key) == 11:
        return "+" + canonical_key
    if "+" in raw:
        return raw
    return canonical_key


def strip_wayback_artifacts(html: str) -> str:
    """Remove Wayback Machine injected toolbar, scripts, and divs."""
    html = WAYBACK_TOOLBAR_RE.sub("", html)
    html = WAYBACK_SCRIPT_RE.sub("", html)
    html = WAYBACK_DIV_RE.sub("", html)
    return html


def is_wayback_comment(text: str) -> bool:
    """Check if an HTML comment is a Wayback Machine artifact."""
    lower = text.lower().strip()
    return any(kw in lower for kw in (
        "wayback", "toolbar", "wm-ipp", "begin wayback", "end wayback",
        "_static/", "archive.org",
    ))
