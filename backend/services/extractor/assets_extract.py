"""Asset extraction: CSS/JS/image/font/media paths, siblings of endpoints.

Operators still want visibility on what a target site shipped (WordPress
plugins, Elementor CSS chunks, custom JS bundles, ...) when fingerprinting
a tech stack, but this noise MUST NOT pollute the endpoint list. This
module owns the `assets` category.

Public surface:
    extract_assets(tree, month, accum)   -> mutates accum["assets"]
    classify_asset_path(path)            -> str | None  (type by suffix)
    is_asset_path(path)                  -> bool        (asset subtree)
    ASSET_SUFFIX_TO_TYPE                 -> dict[str, str]
"""
from __future__ import annotations

from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from .helpers import update_entity


# ---------------------------------------------------------------------------
# Suffix -> asset type. `.json` / `.xml` are deliberately NOT assets: sites
# expose real API/data surfaces under those (e.g. /wp-json/, /sitemap.xml
# the latter is dropped elsewhere). Keep them out so they flow to endpoints.
# ---------------------------------------------------------------------------
ASSET_SUFFIX_TO_TYPE: dict[str, str] = {
    # Stylesheets
    ".css": "stylesheet",
    ".scss": "stylesheet",
    ".less": "stylesheet",
    # Scripts
    ".js": "script",
    ".mjs": "script",
    ".cjs": "script",
    ".map": "script",
    # Images
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".avif": "image",
    ".svg": "image",
    ".bmp": "image",
    ".tiff": "image",
    # Fonts
    ".woff": "font",
    ".woff2": "font",
    ".ttf": "font",
    ".otf": "font",
    ".eot": "font",
    # Media
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
    ".mp3": "audio",
    ".ogg": "audio",
    ".wav": "audio",
    ".flac": "audio",
    # Archives / misc downloads
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".rar": "archive",
    ".7z": "archive",
}


# Subtrees that are, by convention, asset/build output (not app routes).
ASSET_SUBTREES = (
    "/wp-content/cache/",
    "/wp-content/plugins/",
    "/wp-content/themes/",
    "/wp-content/uploads/",
    "/wp-includes/",
    "/node_modules/",
    "/_next/static/",
    "/__nuxt/",
    "/assets/",
    "/static/",
    "/dist/",
    "/vendor/",
    "/build/",
    "/bundles/",
    "/min/",
)


def classify_asset_path(path: str) -> str | None:
    """Return the asset type inferred from *path*'s file suffix, or None.

    Ignores query string / fragment. Case-insensitive.
    """
    if not path:
        return None
    # Strip query/fragment if some caller slipped one through.
    for sep in ("?", "#"):
        cut = path.find(sep)
        if cut != -1:
            path = path[:cut]
    lower = path.lower()
    # Walk suffixes longest-first to avoid `.tar.gz` misclassifying.
    last_slash = lower.rfind("/")
    tail = lower[last_slash + 1:] if last_slash != -1 else lower
    if "." not in tail:
        return None
    dot = tail.rfind(".")
    suffix = tail[dot:]
    return ASSET_SUFFIX_TO_TYPE.get(suffix)


def is_asset_path(path: str) -> bool:
    """True if *path* lives inside a conventional asset subtree."""
    if not path:
        return False
    lower = path.lower()
    return any(sub in lower for sub in ASSET_SUBTREES)


def _internal_path(url: str, allow_relative: bool = True) -> str | None:
    """Normalize a src/href into an internal path, or None if external/junk."""
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    # External host (we do not know the domain here, so we accept host-less
    # URLs and same-relative paths; cross-origin asset URLs are skipped to
    # avoid counting third-party CDN junk). The caller already filtered in
    # `_extract_links` for anchor tags; for <script>/<link>/<img> we stay
    # conservative.
    host = parsed.hostname or ""
    path = parsed.path or ""
    if host:
        # Same-origin assets are typically emitted relative or host-less;
        # absolute URLs pointing at foreign CDNs aren't useful here.
        return None
    if not path:
        return None
    if path.startswith("/web/"):  # Wayback artifact
        return None
    if not allow_relative and not path.startswith("/"):
        return None
    # Drop fragments / query. We only key on the path.
    return path.rstrip("/") or path


def extract_assets(tree: HTMLParser, month: str, accum: dict) -> None:
    """Collect stylesheet/script/image/font/media paths into accum['assets'].

    Looks at:
      - <link rel="stylesheet" href>
      - <link rel="preload|modulepreload" as="..." href>
      - <script src>
      - <img src>
      - <source src>, <video src>, <audio src>
    """
    # Stylesheets + generic <link href> that point to assets
    for node in tree.css("link[href]"):
        href = node.attributes.get("href", "")
        path = _internal_path(href)
        if not path:
            continue
        rel = (node.attributes.get("rel") or "").lower()
        atype = classify_asset_path(path)
        if atype is None and "stylesheet" in rel:
            atype = "stylesheet"
        if atype is None and is_asset_path(path):
            atype = "other"
        if atype is None:
            continue
        update_entity(
            accum["assets"], path, month,
            {"path": path, "type": atype},
        )

    # Scripts
    for node in tree.css("script[src]"):
        src = node.attributes.get("src", "")
        path = _internal_path(src)
        if not path:
            continue
        atype = classify_asset_path(path) or "script"
        update_entity(
            accum["assets"], path, month,
            {"path": path, "type": atype},
        )

    # Images
    for node in tree.css("img[src]"):
        src = node.attributes.get("src", "")
        path = _internal_path(src)
        if not path:
            continue
        atype = classify_asset_path(path) or "image"
        update_entity(
            accum["assets"], path, month,
            {"path": path, "type": atype},
        )

    # Media sources
    for selector in ("source[src]", "video[src]", "audio[src]"):
        for node in tree.css(selector):
            src = node.attributes.get("src", "")
            path = _internal_path(src)
            if not path:
                continue
            atype = classify_asset_path(path)
            if atype is None:
                # Fall back by tag name.
                tag = node.tag.lower() if node.tag else ""
                atype = "video" if tag == "video" else (
                    "audio" if tag == "audio" else "other"
                )
            update_entity(
                accum["assets"], path, month,
                {"path": path, "type": atype},
            )
