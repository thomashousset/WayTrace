"""RSS / Atom feed discovery extractor.

Emits both explicit ``<link rel="alternate">`` feed declarations and
URL-based heuristics (`/feed/`, `/rss.xml`, `/atom.xml`, ...).
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .helpers import update_entity


# URL tail patterns that almost always denote a feed.
# Longest alternative first so "/rss.xml" beats "/rss".
# Covers WordPress (`/feed/`, `?feed=rss2|atom|rdf`), Atom/RSS XMLs,
# Hugo / Jekyll-style `/index.xml`, Blogger (`/feeds/posts/default`),
# Tumblr (`/rss`), Substack (`/feed`), Ghost (`/rss/`).
_FEED_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?"
    r"(?:"
    r"/feed\.xml|/rss\.xml|/atom\.xml|/index\.xml"
    r"|/feeds/posts/default(?:/?-/[^\s\"'<>]+)?(?:\?[^\s\"'<>]*)?"
    r"|/feed/rss2/?|/feed/atom/?|/feed/?|/rss/?"
    r"|\?feed=(?:rss2?|atom|rdf)(?:&[^\s\"'<>]*)?"
    r")"
    r"(?=[\s\"'<>]|$)",
    re.IGNORECASE,
)

# Pattern for stripping stylesheet link tags from raw HTML before sweep.
_STYLESHEET_LINK_RE = re.compile(
    r"<link\b[^>]*\brel\s*=\s*['\"]?stylesheet['\"]?[^>]*>",
    re.IGNORECASE,
)


def _classify(url: str, declared_type: str = "") -> str:
    t = declared_type.lower()
    if "atom" in t:
        return "atom"
    if "rss" in t:
        return "rss"
    low = url.lower()
    if "atom" in low:
        return "atom"
    return "rss"


def _emit(accum: dict, url: str, feed_type: str, title: str, month: str) -> None:
    if not url:
        return
    # Strip fragment / trailing whitespace.
    url = url.strip().split("#", 1)[0]
    if not url.lower().startswith(("http://", "https://")):
        return
    update_entity(
        accum["rss_feeds"],
        url,
        month,
        {"url": url, "feed_type": feed_type, "title": title},
    )


def extract_rss_feeds(
    tree: HTMLParser,
    raw_text: str,
    page_url: str,
    month: str,
    accum: dict,
) -> None:
    """Populate ``accum['rss_feeds']`` with discovered feed URLs."""

    # 1. Explicit <link rel="alternate" type="application/rss+xml|atom+xml">
    for node in tree.css("link[rel=alternate][href]"):
        href = (node.attributes.get("href") or "").strip()
        declared = (node.attributes.get("type") or "").lower()
        if not href:
            continue
        if "rss+xml" not in declared and "atom+xml" not in declared:
            continue
        absolute = urljoin(page_url, href)
        title = (node.attributes.get("title") or "").strip()
        _emit(accum, absolute, _classify(absolute, declared), title, month)

    # 2. Fallback URL sweep for bare /feed or /rss.xml references.
    # Drop stylesheet <link> tags first so `feed.xml` stylesheets don't count.
    sweep_text = _STYLESHEET_LINK_RE.sub("", raw_text)
    for m in _FEED_URL_RE.finditer(sweep_text):
        url = m.group(0)
        # Skip obvious wayback artifacts (safety net; helpers already stripped).
        if "web.archive.org" in url:
            continue
        _emit(accum, url, _classify(url), "", month)
