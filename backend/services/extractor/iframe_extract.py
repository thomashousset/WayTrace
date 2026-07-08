"""Extractor for iframe sources embedded in HTML pages."""
from __future__ import annotations

from urllib.parse import urlparse

from selectolax.parser import HTMLParser

# (hostname_suffix, service) pairs. Matching is host-suffix based to avoid
# the "fake-youtube.com.evil.tld" substring false-positive. Path-specific
# classifications (google_maps) check the URL path after hostname match.
_SERVICE_MAP: list[tuple[str, str]] = [
    ("youtube.com", "youtube"),
    ("youtube-nocookie.com", "youtube"),
    ("youtu.be", "youtube"),
    ("vimeo.com", "vimeo"),
    ("google.com", "google_maps"),   # narrowed to /maps in path below
    ("maps.google.com", "google_maps"),
    ("docs.google.com", "google_docs"),
    ("calendly.com", "calendly"),
    ("typeform.com", "typeform"),
    ("hubspot.com", "hubspot"),
    ("intercom.io", "intercom"),
    ("spotify.com", "spotify"),
    ("soundcloud.com", "soundcloud"),
    ("twitter.com", "twitter"),
    ("platform.twitter.com", "twitter"),
]

_SKIP_DOMAINS: frozenset[str] = frozenset(
    [
        "doubleclick.net",
        "googlesyndication.com",
        "pagead2.googlesyndication.com",
        "amazon-adsystem.com",
        "web.archive.org",
    ]
)

_SKIP_PREFIXES: tuple[str, ...] = ("about:blank", "javascript:", "data:")


def _classify_service(url: str) -> str:
    """Return a service name for *url* or ``"unknown"`` if none matches.

    Matches by host suffix (host == suffix OR host.endswith('.' + suffix))
    so "fake-youtube.com.evil.tld" does NOT match "youtube.com". Special
    case for google.com which only counts as google_maps when the path
    begins with /maps.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "unknown"
    host = (parsed.hostname or "").lower()
    if not host:
        return "unknown"
    path = parsed.path or ""
    for suffix, service in _SERVICE_MAP:
        if host == suffix or host.endswith("." + suffix):
            if service == "google_maps" and suffix == "google.com":
                if not path.startswith("/maps"):
                    continue
            return service
    return "unknown"


def _is_ad_domain(hostname: str) -> bool:
    """Return True if *hostname* matches any skip domain."""
    for skip in _SKIP_DOMAINS:
        if hostname == skip or hostname.endswith("." + skip):
            return True
    return False


def extract_iframe_sources(html: str, tree: HTMLParser | None = None) -> list[dict]:
    """Parse *html* and return a deduplicated list of iframe source dicts.

    Pass *tree* to reuse an already-parsed document and skip a redundant
    HTMLParser allocation.

    Each dict contains:
      - ``url``:     the raw src attribute value
      - ``service``: classified service name or ``"unknown"``
      - ``domain``:  hostname extracted via :func:`urllib.parse.urlparse`
    """
    if tree is None:
        tree = HTMLParser(html)
    seen: set[str] = set()
    results: list[dict] = []

    for node in tree.css("iframe"):
        src = node.attributes.get("src", "") or ""
        src = src.strip()

        if not src:
            continue

        if any(src.startswith(prefix) for prefix in _SKIP_PREFIXES):
            continue

        parsed = urlparse(src)
        hostname = parsed.hostname or ""

        if _is_ad_domain(hostname):
            continue

        if src in seen:
            continue
        seen.add(src)

        results.append(
            {
                "url": src,
                "service": _classify_service(src),
                "domain": hostname,
            }
        )

    return results
