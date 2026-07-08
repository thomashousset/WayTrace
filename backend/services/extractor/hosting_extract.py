"""Extractor for hosting provider detection."""
from __future__ import annotations

import re

# Word-boundary anchored patterns so partial substring matches ("fake-vercel"
# masquerading as Vercel, or "mycloudflarelike" sneaking through) are
# rejected. Keeping naked-keyword matches (cloudflare, netlify) so obvious
# branding in meta tags and comments still registers. the original review
# wanted substring → word-anchored, not substring → TLD-only.
_HOSTING_SIGNALS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\bcloudflare(?:\.com|insights\.com)?\b|\bworkers\.dev\b|\bpages\.dev\b|cdn-cgi/", re.I), "Cloudflare", "meta/script"),
    (re.compile(r"\bnetlify(?:\.(?:com|app))?\b", re.I), "Netlify", "meta/script"),
    (re.compile(r"__vercel|x-vercel|\bvercel\.(?:com|app)\b", re.I), "Vercel", "script/meta"),
    (re.compile(r"\.github\.io\b", re.I), "GitHub Pages", "link/meta"),
    (re.compile(r"\bAmazonS3\b|\.amazonaws\.com\b", re.I), "AWS S3", "meta/link"),
    (re.compile(r"\.execute-api\.[a-z0-9-]+\.amazonaws\.com\b|\.lambda-url\.[a-z0-9-]+\.on\.aws\b", re.I), "AWS Lambda", "url"),
    (re.compile(r"\bs\d+\.wp\.com\b|\bwordpress\.com\b", re.I), "WordPress.com", "link"),
    (re.compile(r"\bcdn\.shopify\.com\b|\b[a-z0-9-]+\.myshopify\.com\b|\bShopify\.(?:theme|shop|routes|Checkout|analytics)\b", re.I), "Shopify", "link/script"),
    (re.compile(r"\bsquarespace(?:\.com|-cdn)\b", re.I), "Squarespace", "link/script"),
    (re.compile(r"\bwix\.com\b|\bwixstatic\.com\b", re.I), "Wix", "link/script"),
    (re.compile(r"\bwpengine\.com\b", re.I), "WP Engine", "link"),
    (re.compile(r"\bwpserveur\b", re.I), "WPServeur", "script/comment"),
    (re.compile(r"\bovh(?:\.com|cloud)\b", re.I), "OVH", "meta/link"),
    (re.compile(r"\bheroku(?:app|cdn)?\.com\b", re.I), "Heroku", "meta/link"),
    (re.compile(r"\bfirebase(?:app)?\.com\b|\bfirebaseio\.com\b", re.I), "Firebase", "script/link"),
    (re.compile(r"\bweb\.app\b", re.I), "Firebase Hosting", "url"),
    (re.compile(r"\bazurewebsites\.net\b|\bazure\.com\b", re.I), "Azure", "meta/link"),
    (re.compile(r"\bgoogleusercontent\.com\b|\bappspot\.com\b", re.I), "Google Cloud", "link"),
    (re.compile(r"\b[a-z0-9-]+\.run\.app\b", re.I), "Google Cloud Run", "url"),
    (re.compile(r"\brender\.com\b", re.I), "Render", "meta"),
    (re.compile(r"\bfly\.(?:io|dev)\b", re.I), "Fly.io", "meta"),
    (re.compile(r"\bondigitalocean\.app\b", re.I), "DigitalOcean App Platform", "url"),
    (re.compile(r"\brailway\.app\b", re.I), "Railway", "url"),
    (re.compile(r"\bdeno\.dev\b", re.I), "Deno Deploy", "url"),
    # --- Global CDNs (domain-anchored. don't confuse with hosting) ---
    (re.compile(r"\b[a-z0-9]+\.cloudfront\.net\b", re.I), "AWS CloudFront", "url"),
    (re.compile(r"\.amplifyapp\.com\b", re.I), "AWS Amplify", "url"),
    (re.compile(r"\b(?:akamaihd|akamaized|edgesuite|edgekey)\.net\b", re.I), "Akamai", "url"),
    (re.compile(r"\bfastly(?:lb)?\.net\b", re.I), "Fastly", "url"),
    (re.compile(r"\bb-cdn\.net\b|\bbunnycdn\.com\b", re.I), "bunny.net", "url"),
    (re.compile(r"\bkxcdn\.com\b", re.I), "KeyCDN", "url"),
    # --- Backend-as-a-service ----------------------------------------
    (re.compile(r"\bsupabase\.co\b", re.I), "Supabase", "url"),
    (re.compile(r"\bghost\.io\b", re.I), "Ghost(Pro)", "url"),
    (re.compile(r"\bstrapiapp\.com\b", re.I), "Strapi Cloud", "url"),
]

_SKIP_TERMS = re.compile(r"wayback|archive", re.I)


# Header-only signatures. CDN / edge providers are often invisible in
# the HTML body but are stamped on the response headers that archive.org
# preserves under x-archive-orig-*. Examples: Fastly, OpenResty,
# Akamai - none of them advertise themselves in URLs the page emits.
_HEADER_HOSTING_SIGNATURES: list[tuple[str, re.Pattern[str], str, str]] = [
    # Fastly stamps x-served-by with a POP code of the form
    # cache-<2-4 letter airport code>(-|<digit>)<rest>, e.g.
    # cache-iad-kcgs7200103-IAD or cache-fra19156-FRA.
    ("x-served-by", re.compile(r"\bcache-[a-z]{2,4}(?:[-\d])", re.I), "Fastly", "header:x-served-by"),
    ("x-cache", re.compile(r"\b(?:HIT|MISS)\b", re.I), "Fastly", "header:x-cache"),
    ("x-fastly-request-id", re.compile(r"."), "Fastly", "header:x-fastly-request-id"),
    # OpenResty (nginx fork) commonly sits in front of Lua-extended apps.
    ("server", re.compile(r"\bopenresty\b", re.I), "OpenResty", "header:server"),
    # Bare Varnish (without Fastly). accepted only when via: lists varnish
    # and we haven't already seen a Fastly signal.
    ("via", re.compile(r"\bvarnish\b", re.I), "Varnish", "header:via"),
    # Akamai edge. multiple distinct headers depending on product.
    ("x-akamai-transformed", re.compile(r"."), "Akamai", "header:x-akamai-transformed"),
    ("server", re.compile(r"\bAkamaiGHost\b", re.I), "Akamai", "header:server"),
    # Cloudflare confirmation via headers (server: cloudflare).
    ("server", re.compile(r"\bcloudflare\b", re.I), "Cloudflare", "header:server"),
    ("cf-ray", re.compile(r"."), "Cloudflare", "header:cf-ray"),
    # Vercel / Netlify edges stamp their own server/x-* headers.
    ("server", re.compile(r"\bvercel\b", re.I), "Vercel", "header:server"),
    ("x-vercel-id", re.compile(r"."), "Vercel", "header:x-vercel-id"),
    ("server", re.compile(r"\bnetlify\b", re.I), "Netlify", "header:server"),
]


def detect_hosting(html: str, headers: dict | None = None) -> list[dict]:
    """Return a deduplicated list of detected hosting providers.

    Detection sources:
      * URL/domain hits in *html* (CDN hostnames, asset paths, …).
      * Optional response *headers*. many edge layers (Fastly, Varnish,
        OpenResty) are invisible in the HTML but stamped on the response.

    Each entry is a dict with keys:
      - ``provider``: the hosting provider name
      - ``signal``:   the signal type hint (e.g. ``"meta/script"``,
                      ``"header:x-served-by"``)
    """
    seen: set[str] = set()
    results: list[dict] = []

    for pattern, provider, signal in _HOSTING_SIGNALS:
        if provider in seen:
            continue
        if _SKIP_TERMS.search(provider):
            continue
        if pattern.search(html):
            seen.add(provider)
            results.append({"provider": provider, "signal": signal})

    if headers:
        norm = {k.lower(): str(v) for k, v in headers.items()}
        for header_name, pattern, provider, signal in _HEADER_HOSTING_SIGNATURES:
            if provider in seen:
                continue
            value = norm.get(header_name)
            if value and pattern.search(value):
                seen.add(provider)
                results.append({"provider": provider, "signal": signal})

    return results
