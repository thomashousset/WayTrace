"""Analytics and tag-manager identifier extractor.

Captures cross-platform analytics IDs that serve as correlation pivots:
GA4, Universal Analytics, Google Tag Manager, Hotjar, Matomo, Mixpanel,
Segment, Yandex Metrica, Plausible, Fathom.

Every entity emits ``platform``, ``id_value``, and a ``pivot_url`` pointing
to the platform portal or reference page when one exists.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from .helpers import update_entity


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Google family
_GA4_RE = re.compile(r"\bG-[A-Z0-9]{10}\b")
_UA_RE = re.compile(r"\bUA-\d{4,10}-\d{1,4}\b")
_GTM_RE = re.compile(r"\bGTM-[A-Z0-9]{6,9}\b")

# Hotjar: either hjid field or script URL
_HOTJAR_HJID_RE = re.compile(r"hjid\s*[:=]\s*['\"]?(\d{5,8})['\"]?")
_HOTJAR_URL_RE = re.compile(r"static\.hotjar\.com/c/hotjar-(\d{5,8})")

# Matomo (formerly Piwik)
# Matches both setSiteId(7) / setSiteId('7') and the _paq.push(["setSiteId","7"])
# array form used by the standard Matomo JS snippet.
_MATOMO_SITE_RE = re.compile(r"setSiteId['\"]?\s*[,(]\s*['\"]?(\d{1,6})")
_MATOMO_URL_RE = re.compile(r"piwik\.php\?idsite=(\d{1,6})")
_MATOMO_URL_RE2 = re.compile(r"matomo\.php\?idsite=(\d{1,6})")

# Mixpanel
_MIXPANEL_RE = re.compile(r"mixpanel\.init\(\s*['\"]([a-f0-9]{32})['\"]")

# Segment
_SEGMENT_RE = re.compile(r"analytics\.load\(\s*['\"]([A-Za-z0-9]{20,40})['\"]")

# Yandex Metrica
_YANDEX_YM_RE = re.compile(r"\bym\(\s*(\d{7,9})\s*,")
_YANDEX_URL_RE = re.compile(r"mc\.yandex\.ru/watch/(\d{7,9})")

# Fathom
_FATHOM_RE = re.compile(r'data-site=["\']([A-Z]{8})["\']')


# Placeholder / obvious test IDs we refuse to emit.
_ID_DENYLIST = {
    "GTM-XXXXXX",
    "GTM-XXXXXXX",
    "UA-000000-1",
    "UA-0-1",
    "G-XXXXXXXXXX",
}


def _pivot_for(platform: str, id_value: str) -> str:
    """Build the best pivot URL we can for a given platform + id."""
    p = platform.lower()
    if p in ("ga4", "ua"):
        return f"https://www.google.com/search?q=%22{id_value}%22"
    if p == "gtm":
        return f"https://www.googletagmanager.com/gtm.js?id={id_value}"
    if p == "hotjar":
        return f"https://insights.hotjar.com/sites/{id_value}/"
    if p == "matomo":
        return f"https://www.google.com/search?q=%22idsite%3D{id_value}%22+matomo"
    if p == "mixpanel":
        return f"https://mixpanel.com/report/{id_value}/"
    if p == "segment":
        return f"https://app.segment.com/workspaces/write-keys?q={id_value}"
    if p == "yandex_metrica":
        return f"https://metrika.yandex.com/dashboard?id={id_value}"
    if p == "plausible":
        return f"https://plausible.io/{id_value}"
    if p == "fathom":
        return f"https://app.usefathom.com/share/{id_value}/"
    return ""


def _emit(accum: dict, platform: str, id_value: str, month: str) -> None:
    if not id_value:
        return
    if id_value in _ID_DENYLIST:
        return
    key = f"{platform}:{id_value}"
    update_entity(
        accum["analytics_ids"],
        key,
        month,
        {
            "platform": platform,
            "id_value": id_value,
            "pivot_url": _pivot_for(platform, id_value),
        },
    )


def extract_analytics_ids(
    tree: HTMLParser, raw_text: str, month: str, accum: dict
) -> None:
    """Populate ``accum['analytics_ids']`` from the parsed page."""

    # Direct regex sweeps over the raw HTML for high-entropy identifiers.
    for m in _GA4_RE.finditer(raw_text):
        _emit(accum, "ga4", m.group(0), month)
    for m in _UA_RE.finditer(raw_text):
        _emit(accum, "ua", m.group(0), month)
    for m in _GTM_RE.finditer(raw_text):
        _emit(accum, "gtm", m.group(0), month)

    for m in _HOTJAR_HJID_RE.finditer(raw_text):
        _emit(accum, "hotjar", m.group(1), month)
    for m in _HOTJAR_URL_RE.finditer(raw_text):
        _emit(accum, "hotjar", m.group(1), month)

    for rx in (_MATOMO_SITE_RE, _MATOMO_URL_RE, _MATOMO_URL_RE2):
        for m in rx.finditer(raw_text):
            _emit(accum, "matomo", m.group(1), month)

    for m in _MIXPANEL_RE.finditer(raw_text):
        _emit(accum, "mixpanel", m.group(1), month)

    for m in _SEGMENT_RE.finditer(raw_text):
        _emit(accum, "segment", m.group(1), month)

    for rx in (_YANDEX_YM_RE, _YANDEX_URL_RE):
        for m in rx.finditer(raw_text):
            _emit(accum, "yandex_metrica", m.group(1), month)

    # Plausible needs DOM scoping: data-domain on the plausible.io script tag.
    for node in tree.css("script[data-domain]"):
        src = node.attributes.get("src", "") or ""
        if "plausible.io" not in src and "plausible" not in src.lower():
            continue
        domain = (node.attributes.get("data-domain") or "").strip()
        if domain:
            _emit(accum, "plausible", domain, month)

    # Fathom uses a short uppercase site code.
    for m in _FATHOM_RE.finditer(raw_text):
        _emit(accum, "fathom", m.group(1), month)
