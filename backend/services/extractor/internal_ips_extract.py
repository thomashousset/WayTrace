# backend/services/extractor/internal_ips_extract.py
"""Internal IP address extraction (RFC1918 + localhost)."""
from __future__ import annotations

import re

from .patterns import INTERNAL_IP_RE

_CSS_COLOR_PREFIXES = ("rgb", "rgba", "hsl")
_CONTEXT_RADIUS = 50
_CSS_LOOKAHEAD = 15
_DOC_CONTEXT_RADIUS = 80

# Phrases that indicate an IP is being *documented* (RFC content, network
# primer, address-space registry), not *leaked*. We target vocabulary that
# only appears in standards / registries, not in ops logs. so "loopback"
# or "multicast" alone wouldn't qualify (a devops page can mention them
# around a real leak). We want specifically RFC-style phrases.
_DOC_CONTEXT_MARKERS = re.compile(
    r"\b(?:"
    r"rfc\s?1918|rfc1918|rfc\s?5737|rfc\s?6598|"
    r"reserved\s+for|private[-\s]use(?:\s+network)?s?|"
    r"address\s+space|addressing\s+plan|special[-\s]purpose|"
    r"documentation\s+(?:purpose|range|prefix|example)|"
    r"example\s+(?:network|address|range|ip)|"
    r"/8\s+reserved|/12\s+reserved|/16\s+reserved|"
    # Range-description prose ("10.0.0.0 through to 10.255.255.255")
    r"through\s+(?:to\s+)?\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    # "Begins with 10." introducing an IP-range explanation
    r"begins?\s+with\s+<?strong>?\d{1,3}\.?"
    r")\b",
    re.IGNORECASE,
)

# A 4-part software version that happens to fall in a private range
# ("version 10.0.0.1", "v10.2.0.1") is indistinguishable by shape from an
# RFC1918 address. A version/release marker right before the match (small
# window) flags it as a version string, not an internal IP.
_VERSION_LOOKBEHIND = 24
_VERSION_CONTEXT = re.compile(
    r"(?:version|release|build|rev(?:ision)?|firmware|kernel|update|"
    r"upgrade|changelog|\bv)\s*$",
    re.IGNORECASE,
)


def _in_version_context(html: str, match_start: int) -> bool:
    look = html[max(0, match_start - _VERSION_LOOKBEHIND):match_start]
    return bool(_VERSION_CONTEXT.search(look))


# A CIDR suffix glued directly to the IP ("10.0.0.0/8") turns the match
# from an address into a prefix definition. Very few operational logs ever
# mention an IP with a CIDR. standards documents and address registries
# almost always do.
_CIDR_SUFFIX = re.compile(r"^\s*/\d{1,2}\b")


def _is_valid_octet(value: str) -> bool:
    try:
        return 0 <= int(value) <= 255
    except ValueError:
        return False


def _validate_ip(ip: str) -> bool:
    parts = ip.split(".")
    return len(parts) == 4 and all(_is_valid_octet(p) for p in parts)


def _in_css_color_context(html: str, match_start: int) -> bool:
    look_start = max(0, match_start - _CSS_LOOKAHEAD)
    prefix = html[look_start:match_start].lower()
    return any(token in prefix for token in _CSS_COLOR_PREFIXES)


def _in_documentation_context(html: str, match_start: int, match_end: int) -> bool:
    """True if the IP sits in text that looks like an RFC / doc, not a leak.

    Two signals:
    1. A CIDR suffix ``/N`` right after the IP, e.g. ``10.0.0.0/8``.
    2. Nearby prose containing standards / registry vocabulary.
    """
    # Fast CIDR check: the char(s) just after the match
    tail = html[match_end : match_end + 6]
    if _CIDR_SUFFIX.match(tail):
        return True
    start = max(0, match_start - _DOC_CONTEXT_RADIUS)
    end = min(len(html), match_end + _DOC_CONTEXT_RADIUS)
    return bool(_DOC_CONTEXT_MARKERS.search(html[start:end]))


def extract_internal_ips(html: str) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []

    for match in INTERNAL_IP_RE.finditer(html):
        ip = match.group(0)

        if not _validate_ip(ip):
            continue

        if _in_css_color_context(html, match.start()):
            continue

        if _in_documentation_context(html, match.start(), match.end()):
            continue

        if _in_version_context(html, match.start()):
            continue

        if ip in seen:
            continue
        seen.add(ip)

        ctx_start = max(0, match.start() - _CONTEXT_RADIUS)
        ctx_end = min(len(html), match.end() + _CONTEXT_RADIUS)
        context = html[ctx_start:ctx_end]

        results.append({"ip": ip, "context": context})

    return results
