# backend/services/extractor/outgoing_links_extract.py
"""Outgoing external link extraction with social-platform categorization."""
from __future__ import annotations

from selectolax.parser import HTMLParser

_SOCIAL_DOMAINS: dict[str, str] = {
    "twitter.com": "twitter",
    "x.com": "x",
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "github.com": "github",
    "tiktok.com": "tiktok",
    "snapchat.com": "snapchat",
    "telegram.org": "telegram",
    "t.me": "telegram",
    "discord.gg": "discord",
    "discord.com": "discord",
    "reddit.com": "reddit",
    "mastodon.social": "mastodon",
    "mastodon.online": "mastodon",
    "threads.net": "threads",
    "pinterest.com": "pinterest",
    "tumblr.com": "tumblr",
    "vimeo.com": "vimeo",
    "twitch.tv": "twitch",
    "medium.com": "medium",
}

_SKIP_SCHEMES = frozenset({"javascript:", "mailto:", "tel:", "#"})
_ARCHIVE_DOMAINS = frozenset({"web.archive.org", "archive.org"})

# Domain-parking placeholders Wayback caches when a domain expires. They
# carry no real outgoing-link signal and inflate findings.
_PARKING_DOMAINS = frozenset({
    "sedo.com", "sedoparking.com", "parkingcrew.net", "bodis.com",
    "dan.com", "afternic.com", "uniregistry.com", "hugedomains.com",
})

# Substrings that mark intent/share endpoints rather than real profiles.
# Twitter/Facebook/etc share buttons all funnel through these paths.
# Also covers Xing's `op=share`, Google+'s `?v=compose` (legacy share
# pop-up), Pinterest pinit, and AddThis-style aggregators.
_INTENT_PATH_FRAGMENTS = (
    "/share?", "/share/", "/sharer", "/sharer.php",
    "/intent/", "/intent?",
    "/dialog/", "/dialog?",
    "/submit?", "/submit/",
    "/plus.google.com/+", "plus.google.com/share",
    "/widgets.js", "/platform.js",
    # Xing share buttons: /app/user?op=share&url=...
    "op=share",
    # Google+ legacy share / compose pop-ups
    "/app/plus/x?v=compose", "/app/plus/x/?v=compose",
    "v=compose",
    # Pinterest pin-it / addthis aggregators
    "pinterest.com/pin/create",
    "addtoany.com/share",
    "addthis.com/bookmark",
)


def _extract_hostname(url: str) -> str | None:
    """Return lowercased hostname from an absolute URL, or None on failure."""
    try:
        after_scheme = url.split("://", 1)[1]
        host = after_scheme.split("/")[0].split("?")[0].split("#")[0].lower()
        # Strip port if present
        host = host.split(":")[0]
        return host if host else None
    except IndexError:
        return None


def _is_same_domain(host: str, domain: str) -> bool:
    """Return True if host is the target domain or any subdomain of it."""
    domain = domain.lower()
    host = host.lower()
    return host == domain or host.endswith("." + domain)


def _is_archive(host: str) -> bool:
    for skip in _ARCHIVE_DOMAINS:
        if host == skip or host.endswith("." + skip):
            return True
    return False


def _is_parking(host: str) -> bool:
    for park in _PARKING_DOMAINS:
        if host == park or host.endswith("." + park):
            return True
    return False


def _is_intent(href: str) -> bool:
    lowered = href.lower()
    return any(frag in lowered for frag in _INTENT_PATH_FRAGMENTS)


def _classify(host: str) -> tuple[str, str]:
    """Return (category, service) for the given hostname."""
    for social_domain, service in _SOCIAL_DOMAINS.items():
        if host == social_domain or host.endswith("." + social_domain):
            return "social", service
    return "other", ""


def extract_outgoing_links(html: str, domain: str, tree: HTMLParser | None = None) -> list[dict]:
    """Return list of dicts with keys: url, domain, category, service.

    *tree* can be supplied by the orchestrator to reuse an already-parsed
    document.

    Skips internal links, relative links, javascript/mailto/tel/# schemes,
    and archive.org URLs. Deduplicates by URL.
    """
    if tree is None:
        tree = HTMLParser(html)
    seen: set[str] = set()
    results: list[dict] = []

    for node in tree.css("a[href]"):
        href = node.attributes.get("href", "") or ""
        href = href.strip()

        if not href:
            continue

        # Skip fragment-only, javascript, mailto, tel
        if any(href.startswith(scheme) for scheme in _SKIP_SCHEMES) or href == "#":
            continue

        # Protocol-relative URLs (//host/path) are absolute; give them a scheme
        # so they are treated as real outgoing links, not skipped as relative.
        if href.startswith("//"):
            href = "https:" + href

        # Skip relative links (no scheme)
        if "://" not in href:
            continue

        host = _extract_hostname(href)
        if host is None:
            continue

        # Skip archive.org
        if _is_archive(host):
            continue

        # Skip parked-domain placeholders (sedo, hugedomains, etc.)
        if _is_parking(host):
            continue

        # Skip social share/intent endpoints. they are widget URLs, not
        # actual profiles or partner sites.
        if _is_intent(href):
            continue

        # Skip internal (same domain or subdomain)
        if _is_same_domain(host, domain):
            continue

        # Dedup by URL
        if href in seen:
            continue
        seen.add(href)

        category, service = _classify(host)
        results.append({
            "url": href,
            "domain": host,
            "category": category,
            "service": service,
        })

    return results
