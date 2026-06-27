"""Detect public status pages.

Two paths: hosted tenants on the usual providers (Statuspage.io,
Instatus, Better Stack, FreshStatus, StatusHub) and custom-domain
pages such as ``status.example.com`` that point at the same providers
through a CNAME. The custom-domain branch is what catches the bigger
vendors who hide their tenant behind their own DNS.
"""
from __future__ import annotations

import re

from .helpers import update_entity
from .patterns import STATUS_PAGE_PATTERNS


_PIVOT_TEMPLATES = {
    "statuspage.io": "https://{slug}.statuspage.io/",
    "instatus.com": "https://{slug}.instatus.com/",
    "betterstack": "https://{slug}.betteruptime.com/",
    "freshstatus": "https://{slug}.freshstatus.io/",
    "statushub": "https://{slug}.statushub.io/",
}

# Subdomains that conventionally host a status / uptime page on a
# vendor's own domain.
_CUSTOM_HOSTNAME_PREFIXES = ("status.", "health.", "incidents.", "uptime.")
_CUSTOM_DOMAIN_RE = re.compile(
    r"\bhttps?://((?:status|health|incidents|uptime)\.[a-z0-9][a-z0-9.\-]+\.[a-z]{2,})\b",
    re.IGNORECASE,
)


def extract_status_pages(raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['status_pages']`` with discovered status pages."""
    for provider, pattern in STATUS_PAGE_PATTERNS.items():
        for match in pattern.finditer(raw_text):
            slug = match.group(1).lower()
            if not slug or len(slug) < 2:
                continue
            # Skip the providers' own subdomains.
            if slug in ("www", "blog", "docs", "help", "status"):
                continue
            key = f"{provider}:{slug}"
            pivot = _PIVOT_TEMPLATES.get(provider, "").format(slug=slug)
            update_entity(
                accum["status_pages"],
                key,
                month,
                {
                    "provider": provider,
                    "slug": slug,
                    "pivot_url": pivot,
                },
            )

    # Custom-domain status pages: status.<apex>, health.<apex>, etc.
    for match in _CUSTOM_DOMAIN_RE.finditer(raw_text):
        host = match.group(1).lower().rstrip(".")
        if "." not in host:
            continue
        if not any(host.startswith(p) for p in _CUSTOM_HOSTNAME_PREFIXES):
            continue
        key = f"custom-domain:{host}"
        update_entity(
            accum["status_pages"],
            key,
            month,
            {
                "provider": "custom-domain",
                "slug": host,
                "pivot_url": f"https://{host}/",
            },
        )
