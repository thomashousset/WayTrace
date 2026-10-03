"""Media / content / search SaaS account ids embedded in asset URLs.

Many sites serve images, video, content and search through hosted SaaS whose
URL carries the *account* identifier: Cloudinary cloud name, imgix source,
Contentful space, Sanity project, Algolia app id, Cloudflare Images account
hash, Wistia account, BunnyCDN pull zone. Unlike a generic CDN hostname, that
id is account-scoped, so the same value on another domain points to the same
operator.
"""
from __future__ import annotations

import re

from .helpers import update_entity


# vendor -> account-id capturing regex (each already vendor-specific, so a match
# is both the detection and the id).
_ACCOUNT_PATTERNS: dict[str, re.Pattern] = {
    # Cloudinary cloud name.
    "cloudinary": re.compile(
        r"res\.cloudinary\.com/([a-z0-9][a-z0-9_-]{1,40})/", re.IGNORECASE
    ),
    # imgix source subdomain.
    "imgix": re.compile(r"\b([a-z0-9][a-z0-9-]{1,40})\.imgix\.net\b", re.IGNORECASE),
    # Contentful space id (from the image or delivery host).
    "contentful": re.compile(
        r"images\.ctfassets\.net/([a-z0-9]{6,})/"
        r"|cdn\.contentful\.com/spaces/([a-z0-9]{6,})",
        re.IGNORECASE,
    ),
    # Sanity project id.
    "sanity": re.compile(
        r"cdn\.sanity\.io/(?:images|files)/([a-z0-9]{6,})/", re.IGNORECASE
    ),
    # Algolia application id (10 uppercase alnum) from the API host or config.
    "algolia": re.compile(
        r"\b([A-Z0-9]{10})-dsn\.algolia\.net\b"
        r"|\b([A-Z0-9]{10})\.algolia\.net\b",
    ),
    # Cloudflare Images account hash.
    "cloudflare_images": re.compile(
        r"imagedelivery\.net/([A-Za-z0-9_-]{20,})/"
    ),
    # Wistia account subdomain (medias hashed-id URLs are not account-scoped).
    "wistia": re.compile(r"\b([a-z0-9][a-z0-9-]{1,40})\.wistia\.com\b", re.IGNORECASE),
    # BunnyCDN pull-zone name.
    "bunny": re.compile(r"\b([a-z0-9][a-z0-9-]{1,40})\.b-cdn\.net\b", re.IGNORECASE),
}

# Subdomains that are vendor infra rather than a customer account.
_NON_ACCOUNT = {
    "www", "cdn", "static", "assets", "img", "images", "media",
    "fast", "embed", "api", "app", "js", "res", "support", "docs",
    "demo", "blog", "home", "help",
}

_LABELS = {
    "cloudinary": "Cloudinary",
    "imgix": "imgix",
    "contentful": "Contentful",
    "sanity": "Sanity",
    "algolia": "Algolia",
    "cloudflare_images": "Cloudflare Images",
    "wistia": "Wistia",
    "bunny": "BunnyCDN",
}


def _pivot_for(vendor: str, account_id: str) -> str:
    if vendor == "cloudinary":
        return f"https://res.cloudinary.com/{account_id}/"
    if vendor == "imgix":
        return f"https://{account_id}.imgix.net/"
    if vendor == "wistia":
        return f"https://{account_id}.wistia.com/"
    if vendor == "bunny":
        return f"https://{account_id}.b-cdn.net/"
    portals = {
        "contentful": "https://www.contentful.com/",
        "sanity": "https://www.sanity.io/",
        "algolia": "https://www.algolia.com/",
        "cloudflare_images": "https://www.cloudflare.com/products/cloudflare-images/",
    }
    return portals.get(vendor, "")


def extract_cdn_accounts(raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['cdn_accounts']`` with SaaS media/content/search ids."""
    for vendor, rx in _ACCOUNT_PATTERNS.items():
        for m in rx.finditer(raw_text):
            account_id = next((g for g in m.groups() if g), "")
            if not account_id:
                continue
            # Subdomain-shaped ids: drop vendor-infra names.
            if vendor in ("imgix", "wistia", "bunny") and account_id.lower() in _NON_ACCOUNT:
                continue
            update_entity(
                accum["cdn_accounts"],
                f"{vendor}:{account_id}",
                month,
                {
                    "vendor": vendor,
                    "label": _LABELS.get(vendor, vendor),
                    "account_id": account_id,
                    "pivot_url": _pivot_for(vendor, account_id),
                },
            )
