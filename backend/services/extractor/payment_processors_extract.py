"""E-commerce / payment integration detector, with the merchant identifier.

Stripe publishable keys already land in ``api_keys``; this category covers the
*other* checkout stacks and, crucially, the merchant / store id embedded in the
markup (Shopify store number, PayPal hosted-button id, Paddle vendor id,
Gumroad creator, Snipcart public key...). The merchant id is the pivot: the same
id on another domain means the same seller.
"""
from __future__ import annotations

import re

from .helpers import update_entity


_VENDOR_PATTERNS: dict[str, list[re.Pattern]] = {
    "shopify": [
        re.compile(r"cdn\.shopify\.com", re.IGNORECASE),
        re.compile(r"\b[a-z0-9-]+\.myshopify\.com\b", re.IGNORECASE),
    ],
    "paypal": [
        re.compile(r"paypal\.com/cgi-bin/webscr", re.IGNORECASE),
        re.compile(r"paypal\.com/donate", re.IGNORECASE),
        re.compile(r"\bhosted_button_id\b", re.IGNORECASE),
    ],
    "paddle": [
        re.compile(r"cdn\.paddle\.com", re.IGNORECASE),
        re.compile(r"\bPaddle\.Setup\b"),
    ],
    "gumroad": [
        re.compile(r"\bgumroad\.com\b", re.IGNORECASE),
        re.compile(r"\bgum\.co\b", re.IGNORECASE),
    ],
    "snipcart": [
        re.compile(r"cdn\.snipcart\.com", re.IGNORECASE),
    ],
    "lemonsqueezy": [
        re.compile(r"\.lemonsqueezy\.com", re.IGNORECASE),
    ],
    "chargebee": [
        re.compile(r"\.chargebee\.com", re.IGNORECASE),
    ],
    "square": [
        re.compile(r"\bsq0idp-[A-Za-z0-9_-]{22}\b"),
    ],
}


_ID_PATTERNS: dict[str, re.Pattern] = {
    # Shopify store number: the two path digits of the CDN files path, or the
    # myshopify handle.
    "shopify": re.compile(
        r"cdn\.shopify\.com/s/files/1/(\d+)/(\d+)/"
        r"|\b([a-z0-9][a-z0-9-]*)\.myshopify\.com\b",
        re.IGNORECASE,
    ),
    # PayPal hosted-button id (10-17 uppercase alnum). Tolerate the
    # name="hosted_button_id" value="..." form and the JSON hosted_button_id:"..." form.
    "paypal": re.compile(
        r"hosted_button_id[\"'\s:=]*(?:value\s*=\s*)?[\"']?([A-Z0-9]{10,17})\b"
    ),
    # Paddle vendor id (numeric).
    "paddle": re.compile(r"Paddle\.Setup\(\s*\{[^}]*\bvendor\s*:\s*(\d{3,})"),
    # Gumroad creator subdomain or product slug.
    "gumroad": re.compile(
        r"\b([a-z0-9][a-z0-9-]*)\.gumroad\.com\b"
        r"|gumroad\.com/l/([a-zA-Z0-9_-]+)",
        re.IGNORECASE,
    ),
    # Snipcart public API key on the store div.
    "snipcart": re.compile(r"data-api-key=[\"']([A-Za-z0-9=_-]{20,})[\"']"),
    # Lemon Squeezy store subdomain.
    "lemonsqueezy": re.compile(r"\b([a-z0-9][a-z0-9-]*)\.lemonsqueezy\.com\b", re.IGNORECASE),
    # Chargebee site name.
    "chargebee": re.compile(r"\b([a-z0-9][a-z0-9-]*)\.chargebee\.com\b", re.IGNORECASE),
    # Square application id.
    "square": re.compile(r"\b(sq0idp-[A-Za-z0-9_-]{22})\b"),
}

# Subdomains that belong to the vendor, not a merchant.
_NON_MERCHANT = {
    "www", "cdn", "assets", "static", "app", "api", "js", "checkout",
    "store", "my", "gumroad",
}

_LABELS = {
    "shopify": "Shopify",
    "paypal": "PayPal",
    "paddle": "Paddle",
    "gumroad": "Gumroad",
    "snipcart": "Snipcart",
    "lemonsqueezy": "Lemon Squeezy",
    "chargebee": "Chargebee",
    "square": "Square",
}

_PORTALS = {
    "shopify": "https://www.shopify.com/",
    "paypal": "https://www.paypal.com/",
    "paddle": "https://www.paddle.com/",
    "gumroad": "https://gumroad.com/",
    "snipcart": "https://snipcart.com/",
    "lemonsqueezy": "https://www.lemonsqueezy.com/",
    "chargebee": "https://www.chargebee.com/",
    "square": "https://squareup.com/",
}


def _pivot_for(processor: str, merchant_id: str) -> str:
    if not merchant_id:
        return _PORTALS.get(processor, "")
    if processor == "shopify" and "/" not in merchant_id and not merchant_id.isdigit():
        return f"https://{merchant_id}.myshopify.com/"
    if processor == "lemonsqueezy":
        return f"https://{merchant_id}.lemonsqueezy.com/"
    if processor == "chargebee":
        return f"https://{merchant_id}.chargebee.com/"
    if processor == "gumroad" and "/" not in merchant_id:
        return f"https://{merchant_id}.gumroad.com/"
    return _PORTALS.get(processor, "")


def _capture_id(processor: str, raw_text: str) -> str:
    rx = _ID_PATTERNS.get(processor)
    if rx is None:
        return ""
    m = rx.search(raw_text)
    if not m:
        return ""
    groups = [g for g in m.groups() if g]
    if processor == "shopify" and len(groups) == 2 and groups[0].isdigit():
        # cdn.shopify.com/s/files/1/<a>/<b>/ -> "<a>/<b>" store number
        return f"{groups[0]}/{groups[1]}"
    merchant = groups[0] if groups else ""
    if merchant.lower() in _NON_MERCHANT:
        return ""
    return merchant


def extract_payment_processors(raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['payment_processors']`` with detected checkout stacks."""
    for processor, patterns in _VENDOR_PATTERNS.items():
        if not any(rx.search(raw_text) for rx in patterns):
            continue
        merchant_id = _capture_id(processor, raw_text)
        key = f"{processor}:{merchant_id}" if merchant_id else processor
        update_entity(
            accum["payment_processors"],
            key,
            month,
            {
                "processor": processor,
                "label": _LABELS.get(processor, processor),
                "merchant_id": merchant_id,
                "pivot_url": _pivot_for(processor, merchant_id),
            },
        )
