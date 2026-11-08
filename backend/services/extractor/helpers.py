"""Shared helper functions for the extractor."""
from __future__ import annotations

import re
from contextvars import ContextVar

from .patterns import EMAIL_EXCLUDE, EMAIL_EXCLUDE_ANYWHERE, EMAIL_EXCLUDE_WHOLE, EMAIL_PLACEHOLDER_DOMAINS, IMAGE_EXTENSIONS, ASSET_EXTENSIONS, EMAIL_SEMVER_DOMAIN_RE, WAYBACK_TOOLBAR_RE, WAYBACK_SCRIPT_RE, WAYBACK_DIV_RE


def ts_to_month(timestamp: str) -> str:
    return f"{timestamp[:4]}-{timestamp[4:6]}"


# The entries the page being extracted touched. process_page sets it around
# one page so it can record which archived address carried each value, which
# is what later lets a report say a value was removed rather than merely
# "not seen again" (finalize.resolve_presence). None outside a page.
TOUCHED: ContextVar[list | None] = ContextVar("wt_touched", default=None)


def update_entity(
    store: dict, key: str, month: str, extra: dict | None = None
) -> None:
    touched = TOUCHED.get()
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
    if touched is not None:
        touched.append(entry)
    else:
        # Touched outside a page (subdomains mined from the capture index, a
        # merge): its dates no longer come from pages alone, so no removal is
        # claimed for it.
        entry["_untracked"] = True


_GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "ssh.dev.azure.com",
              "vs-ssh.visualstudio.com", "codeberg.org", "git.sr.ht"}
# 32 hex digits is a Sentry DSN key. Shorter runs are left alone: a
# 16-digit local part is also a numeric mailbox (Facebook ids, fax gateways).
_HEX_KEY_RE = re.compile(r"[0-9a-f]{32,}")
_LOCAL_SEP_RE = re.compile(r"[.\-_+]")


def _hits_word_start(local: str, word: str) -> bool:
    """``word`` in ``local`` as a plain substring, or across separators
    when it starts where a word of ``local`` starts."""
    if word in local:
        return True
    starts, squashed, at_start = set(), [], True
    for ch in local:
        if _LOCAL_SEP_RE.match(ch):
            at_start = True
            continue
        if at_start:
            starts.add(len(squashed))
            at_start = False
        squashed.append(ch)
    squashed = "".join(squashed)
    i = squashed.find(word)
    while i != -1:
        if i in starts:
            return True
        i = squashed.find(word, i + 1)
    return False


def is_email_excluded(email: str) -> bool:
    email_lower = email.lower()
    local = email_lower.split("@")[0]
    domain = email_lower.split("@")[1] if "@" in email_lower else ""
    # Exclude placeholder local parts: whole words for the ambiguous ones,
    # anywhere for those no real name contains (see EMAIL_EXCLUDE).
    words = {w for w in re.split(r"[.\-_+0-9]+", local) if w}
    if words & EMAIL_EXCLUDE:
        return True
    if local.rstrip("0123456789") in EMAIL_EXCLUDE_WHOLE:
        return True
    if any(_hits_word_start(local, exc) for exc in EMAIL_EXCLUDE_ANYWHERE):
        return True
    # Exclude placeholder domains
    if domain in EMAIL_PLACEHOLDER_DOMAINS:
        return True
    # Not mailboxes, only shaped like them: an SSH remote (git@github.com:org/
    # repo.git) and an error-tracker DSN, whose "local part" is a hex key
    # (5f3c1a9b2e…@o12345.ingest.sentry.io). No person has a 16-hex-digit
    # mailbox name.
    if local == "git" and domain in _GIT_HOSTS:
        return True
    if _HEX_KEY_RE.fullmatch(local):
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


_BUCKET_SHAPES = (
    # <bucket>.s3[.-region|-website-region].amazonaws.com/...  The name is
    # taken up to the LAST ".s3" of the host: acme.s3-backup is a bucket of
    # its own, not acme. Region pieces carry no inner hyphen, so they cannot
    # split a run two ways.
    (re.compile(r"^(?P<b>[a-z0-9][a-z0-9.\-]*)\.s3(?:[.\-][a-z0-9]+)*\.amazonaws\.com(?:[/:?#]|$)"), "s3"),
    # s3[.-region].amazonaws.com/<bucket>/...
    (re.compile(r"^s3(?:[.\-][a-z0-9\-]+)*\.amazonaws\.com/(?P<b>[a-z0-9._\-]+)"), "s3"),
    (re.compile(r"^storage\.googleapis\.com/(?P<b>[a-z0-9._\-]+)"), "gcs"),
    # <account>.blob.core.windows.net[/<container>]
    (re.compile(r"^(?P<b>[a-z0-9]+\.blob\.core\.windows\.net(?:/[a-z0-9\-$]+)?)"), "azure"),
    # <region>.digitaloceanspaces.com/<bucket> (path-style), then virtual-hosted
    (re.compile(r"^[a-z]{3}\d\.digitaloceanspaces\.com/(?P<b>[a-z0-9.\-]+)"), "do"),
    (re.compile(r"^(?P<b>[a-z0-9.\-]+?)\.(?:[a-z]{3}\d)\.digitaloceanspaces\.com|"
                r"^(?P<b2>[a-z0-9.\-]+)\.digitaloceanspaces\.com"), "do"),
)


def bucket_key(value: str) -> str:
    """The bucket a cloud-storage URL points into, as "provider:name".

    One bucket shows up under many URLs (one per object, virtual-hosted or
    path-style, with or without a region), and the report counted each URL
    as a bucket: four images in one bucket read "4 cloud buckets exposed".
    The entities keep their full URL (the object path is the finding: a
    backup.sql is not a logo.png); this key is what gets counted."""
    v = (value or "").lower().split("://", 1)[-1]
    for pattern, provider in _BUCKET_SHAPES:
        m = pattern.match(v)
        if m:
            name = next(g for g in m.groups() if g)
            return f"{provider}:{name}"
    return v

