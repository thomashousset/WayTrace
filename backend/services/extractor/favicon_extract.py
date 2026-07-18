"""Extractor for favicon and brand-icon URLs from HTML.

Sources captured (we never fetch the assets themselves):
  - <link rel="icon|shortcut icon|alternate icon|apple-touch-icon|
                apple-touch-icon-precomposed|mask-icon|fluid-icon">
  - <link rel="manifest">                  -> type="manifest"
  - <meta name="msapplication-TileImage">  -> type="ms-tile-image"
  - <meta name="msapplication-config">     -> type="ms-tile-config"
  - JSON-LD ``logo`` field                 -> type="logo:json-ld"
  - <meta property="og:image"> *only* when the filename strongly suggests
    a logo/icon/favicon (substring match) -> type="logo:og-image"

Each entry has ``url``, ``type`` and ``sizes`` (sizes is None unless the
``<link sizes="...">`` attribute supplied it).
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

# rel values we treat as a favicon / pinned-tab / fluid-app icon.
_FAVICON_RELS = {
    "icon",
    "shortcut icon",
    "alternate icon",
    "apple-touch-icon",
    "apple-touch-icon-precomposed",
    "mask-icon",
    "fluid-icon",
}

_WAYBACK_SKIP = ("/_static/", "archive.org")

# Heuristic: og:image is usually a hero photo, not a logo. Only capture it
# when the URL filename clearly hints at brand iconography.
_LOGO_HINT_RE = re.compile(r"(?:logo|icon|favicon|brand|wordmark)", re.IGNORECASE)


def _is_skipped(href: str) -> bool:
    return any(skip in href for skip in _WAYBACK_SKIP)


def _resolve(href: str, base_url: str | None) -> str:
    """Resolve a (possibly relative) href against the page's base URL.

    Falls back to the raw href when no base is supplied. preserves the
    legacy behaviour of the older extractor.
    """
    href = href.strip()
    if not href:
        return ""
    if href.startswith("data:"):
        return href
    if base_url:
        try:
            return urljoin(base_url, href)
        except ValueError:
            return href
    return href


def _classify_link_rel(rel: str) -> str:
    if rel == "manifest":
        return "manifest"
    if rel == "mask-icon":
        return "mask-icon"
    if rel == "fluid-icon":
        return "fluid-icon"
    if "apple" in rel:
        return "apple-touch-icon"
    return "favicon"


def extract_favicons(
    html: str,
    tree: HTMLParser | None = None,
    page_url: str | None = None,
) -> list[dict]:
    """Return a deduplicated list of favicon/icon entries found in *html*.

    *tree* can be supplied to reuse an already-parsed document.
    *page_url* is used to resolve relative hrefs (e.g. ``/favicon.ico``)
    against the original page so consumers know which host the icon
    belongs to.
    """
    seen: set[str] = set()
    results: list[dict] = []

    if tree is None:
        tree = HTMLParser(html)

    # Determine an effective base URL for resolution. Prefer an explicit
    # <base href="..."> when present, otherwise fall back to the supplied
    # page_url.
    base_url = page_url
    base_node = tree.css_first("base[href]")
    if base_node is not None:
        base_href = (base_node.attributes.get("href") or "").strip()
        if base_href:
            try:
                base_url = urljoin(page_url or "", base_href) if page_url else base_href
            except ValueError:
                pass

    def _add(url: str, kind: str, sizes: str | None = None) -> None:
        if not url or _is_skipped(url):
            return
        if url in seen:
            return
        seen.add(url)
        results.append({"url": url, "type": kind, "sizes": sizes})

    # ---- <link> tags -------------------------------------------------------
    for node in tree.css("link"):
        rel = (node.attributes.get("rel") or "").strip().lower()
        if not rel or rel not in _FAVICON_RELS and rel != "manifest":
            continue
        href = (node.attributes.get("href") or "").strip()
        if not href:
            continue
        resolved = _resolve(href, base_url)
        if not resolved:
            continue
        kind = _classify_link_rel(rel)
        sizes = node.attributes.get("sizes") or None
        _add(resolved, kind, sizes)

    # ---- <meta> Microsoft tile / config -----------------------------------
    for node in tree.css("meta[name]"):
        name = (node.attributes.get("name") or "").strip().lower()
        content = (node.attributes.get("content") or "").strip()
        if not content:
            continue
        if name == "msapplication-tileimage":
            _add(_resolve(content, base_url), "ms-tile-image")
        elif name == "msapplication-config":
            _add(_resolve(content, base_url), "ms-tile-config")
        elif name in ("msapplication-square70x70logo",
                      "msapplication-square150x150logo",
                      "msapplication-square310x310logo",
                      "msapplication-wide310x150logo"):
            # Sizes are encoded right in the name. preserve them.
            sz_match = re.search(r"(\d+x\d+)", name)
            _add(_resolve(content, base_url), "ms-tile-image",
                 sz_match.group(1) if sz_match else None)

    # ---- og:image (only if filename hints at a logo/icon) -----------------
    for node in tree.css('meta[property="og:image"], meta[name="og:image"]'):
        content = (node.attributes.get("content") or "").strip()
        if not content:
            continue
        # Heuristic: capture only when filename suggests brand iconography.
        try:
            path = urlparse(content).path
        except ValueError:
            continue
        if _LOGO_HINT_RE.search(path or content):
            _add(_resolve(content, base_url), "logo:og-image")

    # ---- JSON-LD logo field ----------------------------------------------
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for logo_url in _iter_jsonld_logos(data):
            _add(_resolve(logo_url, base_url), "logo:json-ld")

    return results


def _iter_jsonld_logos(data):
    """Yield every ``logo`` URL found anywhere in a JSON-LD payload."""
    if isinstance(data, dict):
        logo = data.get("logo")
        if isinstance(logo, str):
            yield logo
        elif isinstance(logo, dict):
            url = logo.get("url") or logo.get("@id")
            if isinstance(url, str):
                yield url
        elif isinstance(logo, list):
            for item in logo:
                if isinstance(item, str):
                    yield item
                elif isinstance(item, dict):
                    url = item.get("url") or item.get("@id")
                    if isinstance(url, str):
                        yield url
        for value in data.values():
            yield from _iter_jsonld_logos(value)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_logos(item)
