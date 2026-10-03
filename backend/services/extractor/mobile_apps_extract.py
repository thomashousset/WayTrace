"""Site-to-mobile-app linkage.

Websites advertise their companion iOS / Android apps through smart-app-banner
meta tags (``apple-itunes-app``), App Links / Open Graph properties
(``al:ios:app_store_id``, ``al:android:package``), Twitter app cards, and plain
store links. Each is a precise, low-false-positive pivot: it maps the domain to
a concrete App Store id or Play package that carries its own reviews, developer
name and version history.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from .helpers import update_entity


# App Store numeric id from an apps.apple.com / itunes link.
_APPLE_LINK_RE = re.compile(
    r"(?:apps|itunes)\.apple\.com/[^\s\"']*?/id(\d{6,12})\b", re.IGNORECASE
)
# Play Store package id from a store link.
_PLAY_LINK_RE = re.compile(
    r"play\.google\.com/store/apps/details\?[^\"'\s]*?\bid=([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)+)"
)
# apple-itunes-app smart banner: content="app-id=284882215".
_ITUNES_META_RE = re.compile(r"app-id=(\d{6,12})", re.IGNORECASE)
# A valid Android package: at least two dot-separated java identifiers.
_PACKAGE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)+$")


def _emit(accum: dict, store: str, app_id: str, month: str) -> None:
    if store == "ios":
        pivot = f"https://apps.apple.com/app/id{app_id}"
    else:
        pivot = f"https://play.google.com/store/apps/details?id={app_id}"
    update_entity(
        accum["mobile_apps"],
        f"{store}:{app_id}",
        month,
        {"store": store, "app_id": app_id, "pivot_url": pivot},
    )


def extract_mobile_apps(
    tree: HTMLParser, raw_text: str, month: str, accum: dict
) -> None:
    """Populate ``accum['mobile_apps']`` with linked iOS / Android apps."""
    # --- meta tags (most authoritative) ---
    for node in tree.css("meta"):
        name = (node.attributes.get("name") or "").lower()
        prop = (node.attributes.get("property") or "").lower()
        # App Links use property=, Twitter cards and apple-itunes-app use name=.
        tag = prop or name
        content = (node.attributes.get("content") or "").strip()
        if not content:
            continue

        if tag == "apple-itunes-app":
            m = _ITUNES_META_RE.search(content)
            if m:
                _emit(accum, "ios", m.group(1), month)
        elif tag in ("al:ios:app_store_id", "twitter:app:id:iphone",
                     "twitter:app:id:ipad"):
            if content.isdigit() and 6 <= len(content) <= 12:
                _emit(accum, "ios", content, month)
        elif tag in ("al:android:package", "twitter:app:id:googleplay"):
            if _PACKAGE_RE.match(content):
                _emit(accum, "android", content, month)

    # --- plain store links anywhere in the markup ---
    for m in _APPLE_LINK_RE.finditer(raw_text):
        _emit(accum, "ios", m.group(1), month)
    for m in _PLAY_LINK_RE.finditer(raw_text):
        _emit(accum, "android", m.group(1), month)
