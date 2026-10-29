"""Technology fingerprint extractor.

Writes to ``accum["technologies"]`` from three sources:
1. ``<meta name="generator">`` and ``<meta name="powered-by">`` tags.
2. HTML comments matching ``TECH_COMMENT_RE`` (e.g. ``<!-- Powered by Drupal -->``).
3. ``<script src>`` and ``<link href>`` URLs matched against ``SCRIPT_TECH_PATTERNS``.
4. CMS fingerprints from ``CMS_CLASS_INDICATORS`` on ``class``/``id`` attributes.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from .helpers import update_entity
from .patterns import (
    CMS_CLASS_INDICATORS,
    SCRIPT_TECH_PATTERNS,
    TECH_COMMENT_RE,
)


_TECH_ALIASES = {
    "wp": "WordPress", "wordpress.org": "WordPress", "wordpress.com": "WordPress",
    "joomla!": "Joomla",
}


# Text in front of a link to the CMS project's own site that makes it the
# site's credit line ("Powered by Joomla!", "Propulsé par Drupal"), not a
# directory entry or a review.
_POWERED_BY_RE = re.compile(
    r"(?:powered\s+by|propuls[ée]\s+par|r[ée]alis[ée]\s+avec|built\s+with|"
    r"made\s+with|fait\s+avec|motoris[ée]\s+par|runs\s+on|running\s+on)\s*$",
    re.IGNORECASE,
)


def _is_target_host(host: str, domain: str) -> bool:
    host = host.rstrip(".")
    return host == domain or host.endswith("." + domain)


def _foreign_link(node, href: str, domain: str) -> bool:
    """A navigation link to another site. Its /wp-content/ path says the
    OTHER site runs WordPress (a partner's PDF, a press article), not the
    target. Assets (src, stylesheet href) still count wherever they are
    hosted: a WordPress site often serves wp-content from a CDN host.

    Still the target's own: a proxy URL carrying the target's host as its
    first path segment (i0.wp.com/acme.fr/wp-content/...), a host named
    after the target's first label (acme.wpengine.com, acme.files.wordpress.com),
    and a credit link ("Powered by <a href=joomla.org>")."""
    if not domain or node.tag != "a" or not href:
        return False
    lower = href.lower()
    if not any(ind in lower for ind in CMS_CLASS_INDICATORS):
        return False                      # nothing in it to count anyway
    parsed = urlparse(href.strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return False                      # relative: the target itself
    if _is_target_host(host, domain):
        return False
    first_segment = parsed.path.lstrip("/").split("/", 1)[0].lower()
    if first_segment and _is_target_host(first_segment, domain):
        return False
    if host.split(".", 1)[0] == domain.split(".", 1)[0]:
        return False
    return not _POWERED_BY_RE.search(_text_before(node))


def _text_before(node, limit: int = 60) -> str:
    """Up to ``limit`` characters of the siblings just before ``node``."""
    parts, size, sib = [], 0, node.prev
    while sib is not None and size < limit:
        text = sib.text(deep=True) or ""
        parts.append(text)
        size += len(text)
        sib = sib.prev
    return "".join(reversed(parts))[-limit:]


def extract_technologies(
    tree: HTMLParser, raw_text: str, month: str, accum: dict, domain: str = "",
) -> None:
    for node in tree.css('meta[name="generator"], meta[name="powered-by"]'):
        content = (node.attributes.get("content") or "").strip()
        if not content:
            continue
        # A generator string can list multiple technologies separated by
        # commas, semicolons, or " and " (e.g. "WordPress 6.4; WP Rocket
        # 3.17; Yoast SEO"). Split on all three so each plugin counts as
        # its own fingerprint.
        for chunk in re.split(r"\s*(?:[,;]|\s+and\s+)\s*", content):
            # "Joomla! 1.5 - Open Source Content Management": the brand's "!"
            # and the tagline after " - " are not part of the name.
            chunk = re.sub(r"(?<=\w)!", "", chunk.split(" - ", 1)[0]).strip()
            if not chunk:
                continue
            # Split off trailing semver-ish version: "WP Rocket 3.17.1.4".
            m = re.match(
                r"^(?P<tech>[A-Za-z][A-Za-z0-9 .+\-]*?)"
                r"(?:\s+v?(?P<ver>\d+(?:\.\d+){0,3}[A-Za-z0-9.+\-]*))?$",
                chunk,
            )
            if m:
                tech = m.group("tech").strip()
                version = m.group("ver")
            else:
                # Fallback for unanchored chunks like "plugin-name:5.5.6":
                # first whitespace token is the technology, the second a
                # possible version. Catches weird CMS strings.
                parts = chunk.split()
                if not parts:
                    continue
                tech, version = parts[0], (parts[1] if len(parts) > 1 else None)
            if not tech or len(tech) > 80:
                continue
            # A real technology token is a word, not a fragment of a browser
            # user-agent string. Legacy pages authored in Netscape Composer set
            # <meta generator> to the UA ("Mozilla/3.0Gold (Win95; I) [Netscape]"),
            # which the split above shreds into "Mozilla/3.0Gold", "(Win95",
            # "I)", "[Netscape]". Reject anything not letter-led and free of
            # URL/UA punctuation.
            if not re.match(r"^[A-Za-z][A-Za-z0-9.+\- ]*$", tech):
                continue
            if version is not None and not re.match(r"^v?\d", version):
                version = None
            tech = _TECH_ALIASES.get(tech.lower(), tech)
            update_entity(
                accum["technologies"], tech.lower(), month,
                {"technology": tech, "version": version},
            )

    for match in TECH_COMMENT_RE.finditer(raw_text):
        tech = match.group(1)
        update_entity(
            accum["technologies"], tech.lower(), month,
            {"technology": tech, "version": None},
        )

    # CMS class/id/asset-path indicators. Scan ONLY DOM attribute values
    # (class, id, href, src) so prose mentioning a CMS name in body text
    # doesn't get tagged as an install signal.
    seen_cms: set[str] = set()
    for node in tree.css("[class], [id], [href], [src]"):
        attrs = node.attributes or {}
        href = attrs.get("href")
        if href and _foreign_link(node, href, domain):
            href = None
        blob = " ".join(
            v for v in (
                attrs.get("class"),
                attrs.get("id"),
                href,
                attrs.get("src"),
            ) if v
        ).lower()
        if not blob:
            continue
        for indicator, tech in CMS_CLASS_INDICATORS.items():
            tech_key = tech.lower()
            if tech_key in seen_cms:
                continue
            if indicator in blob:
                seen_cms.add(tech_key)
                update_entity(
                    accum["technologies"], tech_key, month,
                    {"technology": tech, "version": None},
                )

    seen_techs: set[str] = set()
    for node in tree.css("script[src], link[href]"):
        src = node.attributes.get("src", "") or node.attributes.get("href", "")
        if not src:
            continue
        for tech_name, pattern in SCRIPT_TECH_PATTERNS.items():
            if tech_name.lower() in seen_techs:
                continue
            if pattern.search(src):
                seen_techs.add(tech_name.lower())
                update_entity(
                    accum["technologies"], tech_name.lower(), month,
                    {"technology": tech_name, "version": None},
                )
