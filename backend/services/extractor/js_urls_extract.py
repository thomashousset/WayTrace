# backend/services/extractor/js_urls_extract.py
"""JS inline URL extraction from inline <script> tags."""
from __future__ import annotations

from selectolax.parser import HTMLParser

from .patterns import JS_API_ASSIGNMENT_RE, JS_URL_RE

_STRIP_TRAILING = ".,;:)}]'\"`"

_SKIP_DOMAINS: frozenset[str] = frozenset(
    {
        "cdnjs.cloudflare.com",
        "cdn.jsdelivr.net",
        "unpkg.com",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "google-analytics.com",
        "googletagmanager.com",
        "googlesyndication.com",
        "googleadservices.com",
        "facebook.net",
        "connect.facebook.net",
        "hotjar.com",
        "mixpanel.com",
        "segment.com",
        "web.archive.org",
        "archive.org",
        "schema.org",
        "w3.org",
    }
)


def _strip_trailing(url: str) -> str:
    return url.rstrip(_STRIP_TRAILING)


def _is_cdn_or_tracking(url: str) -> bool:
    # Extract host portion (between :// and next /)
    try:
        host = url.split("://", 1)[1].split("/")[0].lower()
    except IndexError:
        return False
    # Match exact domain or any subdomain ending with a skip domain
    for skip in _SKIP_DOMAINS:
        if host == skip or host.endswith("." + skip):
            return True
    return False


def extract_js_urls(html: str, tree: HTMLParser | None = None) -> list[dict]:
    """Return list of dicts with keys: url, context ("assignment" | "inline").

    *tree* can be supplied to avoid a redundant HTMLParser allocation.
    """
    if tree is None:
        tree = HTMLParser(html)
    seen: set[str] = set()
    results: list[dict] = []

    for node in tree.css("script"):
        # Skip external scripts (those with a src attribute)
        if node.attributes.get("src") is not None:
            continue

        text = node.text(deep=True) or ""
        if not text:
            continue

        # First pass: assignment patterns (higher signal)
        for match in JS_API_ASSIGNMENT_RE.finditer(text):
            url = _strip_trailing(match.group(1))
            if not url or url in seen:
                continue
            if _is_cdn_or_tracking(url):
                continue
            seen.add(url)
            results.append({"url": url, "context": "assignment"})

        # Second pass: general inline URLs
        for match in JS_URL_RE.finditer(text):
            url = _strip_trailing(match.group(0))
            if not url or url in seen:
                continue
            if _is_cdn_or_tracking(url):
                continue
            seen.add(url)
            results.append({"url": url, "context": "inline"})

    return results
