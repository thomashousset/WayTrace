"""Extractor for domain verification meta tags."""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

_VERIFICATION_TAGS: dict[str, str] = {
    "google-site-verification": "google",
    "msvalidate.01": "bing",
    "facebook-domain-verification": "facebook",
    "p:domain_verify": "pinterest",
    "yandex-verification": "yandex",
    "baidu-site-verification": "baidu",
    "norton-safeweb-site-verification": "norton",
    "alexaverifyid": "alexa",
    # 2024+ additions: many sites publish ownership claims for these too,
    # and the verification ID often pivots back to a SaaS account that
    # gives away the operator's identity.
    "linkedin-site-verification": "linkedin",
    "shopify-checkout-api-token": "shopify",
    "wot-verification": "wot",
    "ahrefs-site-verification": "ahrefs",
    "semrush-verification": "semrush",
    "zoominfo-verification": "zoominfo",
    "mailru-domain": "mailru",
    "naver-site-verification": "naver",
}

_PLACEHOLDER_RE = re.compile(
    r"^[Xx]+$|^your[_-]|^insert[_-]|^replace[_-]|^example",
    re.IGNORECASE,
)


def extract_verification_tags(html: str, tree: HTMLParser | None = None) -> list[dict]:
    """Parse HTML and return domain verification meta tag entries.

    *tree* can be supplied to reuse an already-parsed document.

    Each entry is a dict with keys:
      - ``service``:         the service name (e.g. ``"google"``)
      - ``verification_id``: the raw verification token/code
    """
    if tree is None:
        if not html:
            return []
        tree = HTMLParser(html)
    seen: set[str] = set()
    results: list[dict] = []

    for node in tree.css("meta"):
        attrs = node.attributes
        name = (attrs.get("name") or "").strip().lower()
        if name not in _VERIFICATION_TAGS:
            continue

        content = (attrs.get("content") or "").strip()
        if not content:
            continue
        if len(content) < 6:
            continue
        if _PLACEHOLDER_RE.match(content):
            continue

        dedup_key = f"{name}:{content}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        results.append({
            "service": _VERIFICATION_TAGS[name],
            "verification_id": content,
        })

    return results
