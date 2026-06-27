"""Sitemaps, robots.txt, humans.txt, security.txt discovery.

These artifacts often live at well-known paths and provide pivots into
site structure and disclosure policies.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from .helpers import update_entity


_PATH_KINDS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"/sitemap_index\.xml\b", re.IGNORECASE), "sitemap"),
    (re.compile(r"/sitemap\.xml\b", re.IGNORECASE), "sitemap"),
    (re.compile(r"/sitemap[a-z0-9_\-]*\.xml\b", re.IGNORECASE), "sitemap"),
    (re.compile(r"/robots\.txt\b", re.IGNORECASE), "robots"),
    (re.compile(r"/humans\.txt\b", re.IGNORECASE), "humans"),
    (re.compile(r"/\.well-known/security\.txt\b", re.IGNORECASE), "security"),
    (re.compile(r"/security\.txt\b", re.IGNORECASE), "security"),
    # Programmatic-ad seller disclosures: `/ads.txt` (per-domain) and
    # `/app-ads.txt` (mobile inventory). Both are public OSINT pivots that
    # link a domain to ad networks, SSPs, and ultimately the operator.
    (re.compile(r"/ads\.txt\b", re.IGNORECASE), "ads"),
    (re.compile(r"/app-ads\.txt\b", re.IGNORECASE), "ads"),
)


# Match any URL (absolute or root-relative) hitting one of those paths.
_URL_RE = re.compile(
    r"(?:https?://[^\s\"'<>]+|/[A-Za-z0-9._/\-]+)",
)


def _classify(url: str) -> str:
    low = url.lower()
    for rx, kind in _PATH_KINDS:
        if rx.search(low):
            return kind
    return ""


def _emit(accum: dict, url: str, kind: str, month: str) -> None:
    if not kind or not url:
        return
    url = url.split("#", 1)[0]
    key = url
    update_entity(
        accum["sitemaps_and_robots"],
        key,
        month,
        {"url": url, "kind": kind},
    )


def extract_sitemaps(
    tree: HTMLParser,
    raw_text: str,
    page_url: str,
    month: str,
    accum: dict,
) -> None:
    """Populate ``accum['sitemaps_and_robots']`` with discovered files."""

    # 1. Explicit <link rel="sitemap"> declarations.
    for node in tree.css("link[rel=sitemap][href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(page_url, href)
        _emit(accum, absolute, "sitemap", month)

    # 2. URL sweep for well-known paths anywhere in the HTML.
    for m in _URL_RE.finditer(raw_text):
        candidate = m.group(0)
        kind = _classify(candidate)
        if not kind:
            continue
        if candidate.startswith("/"):
            candidate = urljoin(page_url, candidate)
        # Skip wayback-rewritten URLs that don't represent the real resource.
        try:
            host = urlparse(candidate).hostname or ""
        except ValueError:
            continue
        if host == "web.archive.org":
            continue
        _emit(accum, candidate, kind, month)
