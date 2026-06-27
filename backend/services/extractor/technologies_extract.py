"""Technology fingerprint extractor.

Writes to ``accum["technologies"]`` from three sources:
1. ``<meta name="generator">`` and ``<meta name="powered-by">`` tags.
2. HTML comments matching ``TECH_COMMENT_RE`` (e.g. ``<!-- Powered by Drupal -->``).
3. ``<script src>`` and ``<link href>`` URLs matched against ``SCRIPT_TECH_PATTERNS``.
4. CMS fingerprints from ``CMS_CLASS_INDICATORS`` on ``class``/``id`` attributes.
"""
from __future__ import annotations

import re

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


def extract_technologies(
    tree: HTMLParser, raw_text: str, month: str, accum: dict,
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
            chunk = chunk.strip()
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
        blob = " ".join(
            v for v in (
                attrs.get("class"),
                attrs.get("id"),
                attrs.get("href"),
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
