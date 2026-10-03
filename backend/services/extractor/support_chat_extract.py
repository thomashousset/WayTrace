"""Live-chat / helpdesk widget detector, with the per-site tenant id.

The ``technologies`` category already records that a vendor's *script* is
present; this category goes one step further and pulls the account / property
identifier embedded in the widget snippet. That id is the real OSINT pivot: the
same Intercom app_id, Crisp website id or Zendesk subdomain reused across two
sites ties them to one operator.

Detection is gated on a distinctive vendor host or global before any id is
read, so a bare ``app_id`` or ``license`` token in unrelated code never
registers.
"""
from __future__ import annotations

import re

from .helpers import update_entity


# vendor -> list of detection regexes (a distinctive host or global marker).
_VENDOR_PATTERNS: dict[str, list[re.Pattern]] = {
    "intercom": [
        re.compile(r"widget\.intercom\.io", re.IGNORECASE),
        re.compile(r"js\.intercomcdn\.com", re.IGNORECASE),
        re.compile(r"\bintercomSettings\b", re.IGNORECASE),
    ],
    "crisp": [
        re.compile(r"client\.crisp\.chat", re.IGNORECASE),
        re.compile(r"\bCRISP_WEBSITE_ID\b"),
    ],
    "tawk_to": [
        re.compile(r"embed\.tawk\.to/[0-9a-f]{24}", re.IGNORECASE),
    ],
    "drift": [
        re.compile(r"js\.driftt\.com", re.IGNORECASE),
        re.compile(r"widget\.drift\.com", re.IGNORECASE),
    ],
    "zendesk": [
        re.compile(r"static\.zdassets\.com", re.IGNORECASE),
        re.compile(r"ekr\.zdassets\.com", re.IGNORECASE),
        re.compile(r"\b[a-z0-9][a-z0-9-]*\.zendesk\.com\b", re.IGNORECASE),
    ],
    "livechat": [
        re.compile(r"cdn\.livechatinc\.com", re.IGNORECASE),
    ],
    "tidio": [
        re.compile(r"code\.tidio\.co/[a-z0-9]+\.js", re.IGNORECASE),
    ],
    "olark": [
        re.compile(r"static\.olark\.com", re.IGNORECASE),
        re.compile(r"\bolark\.identify\b", re.IGNORECASE),
    ],
    "helpscout": [
        re.compile(r"beacon-v2\.helpscout\.net", re.IGNORECASE),
    ],
    "smartsupp": [
        re.compile(r"smartsuppchat\.com", re.IGNORECASE),
        re.compile(r"\b_smartsupp\b", re.IGNORECASE),
    ],
    "freshchat": [
        re.compile(r"wchat\.freshchat\.com", re.IGNORECASE),
    ],
}


# vendor -> id-capturing regex. Best-effort; may find nothing (id stays "").
_ID_PATTERNS: dict[str, re.Pattern] = {
    # Intercom app_id: 6-10 lowercase alnum, e.g. app_id: "a1b2c3d4".
    "intercom": re.compile(r"app_id\s*[:=]\s*['\"]([a-z0-9]{6,10})['\"]"),
    # Crisp website id is a UUID.
    "crisp": re.compile(
        r"CRISP_WEBSITE_ID\s*=\s*['\"]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12})['\"]",
        re.IGNORECASE,
    ),
    # Tawk.to property id: 24 hex, first path segment of the embed URL.
    "tawk_to": re.compile(r"embed\.tawk\.to/([0-9a-f]{24})", re.IGNORECASE),
    # Drift org id inside drift.load('...') or the include URL.
    "drift": re.compile(
        r"drift\.load\(\s*['\"]([a-z0-9]{6,})['\"]"
        r"|driftt\.com/include/[^'\"]+/([a-z0-9]+)\.js",
        re.IGNORECASE,
    ),
    # Zendesk web-widget key (UUID) or the tenant subdomain.
    "zendesk": re.compile(
        r"zdassets\.com/ekr/snippet\.js\?key=([0-9a-f-]{36})"
        r"|\b([a-z0-9][a-z0-9-]*)\.zendesk\.com\b",
        re.IGNORECASE,
    ),
    # LiveChat numeric license id.
    "livechat": re.compile(r"license\s*[:=]\s*['\"]?(\d{5,})"),
    "tidio": re.compile(r"code\.tidio\.co/([a-z0-9]+)\.js", re.IGNORECASE),
    "olark": re.compile(r"olark\.identify\(\s*['\"]([A-Za-z0-9-]+)['\"]"),
    "helpscout": re.compile(
        r"Beacon\(\s*['\"]init['\"]\s*,\s*['\"]([0-9a-f]{8}-[0-9a-f-]{27,})['\"]",
        re.IGNORECASE,
    ),
    "smartsupp": re.compile(r"_smartsupp\.key\s*=\s*['\"]([a-z0-9]+)['\"]", re.IGNORECASE),
    # Freshchat token is a UUID passed to init.
    "freshchat": re.compile(
        r"token\s*:\s*['\"]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12})['\"]",
        re.IGNORECASE,
    ),
}

# Zendesk subdomains that are the vendor's own infra, not a tenant.
_ZENDESK_NON_TENANT = {
    "static", "ekr", "cdn", "assets", "www", "d3", "d3v", "widget",
}

_LABELS = {
    "intercom": "Intercom",
    "crisp": "Crisp",
    "tawk_to": "Tawk.to",
    "drift": "Drift",
    "zendesk": "Zendesk",
    "livechat": "LiveChat",
    "tidio": "Tidio",
    "olark": "Olark",
    "helpscout": "Help Scout",
    "smartsupp": "Smartsupp",
    "freshchat": "Freshchat",
}


def _pivot_for(vendor: str, tenant_id: str) -> str:
    if not tenant_id:
        portals = {
            "intercom": "https://www.intercom.com/",
            "crisp": "https://crisp.chat/",
            "tawk_to": "https://www.tawk.to/",
            "drift": "https://www.drift.com/",
            "zendesk": "https://www.zendesk.com/",
            "livechat": "https://www.livechat.com/",
            "tidio": "https://www.tidio.com/",
            "olark": "https://www.olark.com/",
            "helpscout": "https://www.helpscout.com/",
            "smartsupp": "https://www.smartsupp.com/",
            "freshchat": "https://www.freshworks.com/live-chat-software/",
        }
        return portals.get(vendor, "")
    if vendor == "zendesk":
        return f"https://{tenant_id}.zendesk.com/"
    if vendor == "intercom":
        return f"https://app.intercom.com/a/apps/{tenant_id}/"
    return ""


def _capture_id(vendor: str, raw_text: str) -> str:
    rx = _ID_PATTERNS.get(vendor)
    if rx is None:
        return ""
    m = rx.search(raw_text)
    if not m:
        return ""
    # Take the first non-empty capture group (some patterns alternate).
    tenant = next((g for g in m.groups() if g), "")
    if vendor == "zendesk" and tenant.lower() in _ZENDESK_NON_TENANT:
        return ""
    return tenant


def extract_support_chat(raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['support_chat']`` with detected chat/support widgets."""
    for vendor, patterns in _VENDOR_PATTERNS.items():
        if not any(rx.search(raw_text) for rx in patterns):
            continue
        tenant_id = _capture_id(vendor, raw_text)
        key = f"{vendor}:{tenant_id}" if tenant_id else vendor
        update_entity(
            accum["support_chat"],
            key,
            month,
            {
                "vendor": vendor,
                "label": _LABELS.get(vendor, vendor),
                "tenant_id": tenant_id,
                "pivot_url": _pivot_for(vendor, tenant_id),
            },
        )
