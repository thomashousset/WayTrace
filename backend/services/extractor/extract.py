"""Per-page extraction orchestrator.

All categories are registered in ``CATEGORY_EXTRACTORS``. ``extract_page``
builds a single ``ExtractionContext`` per call and dispatches to each
registered extractor that is enabled.

To add a category:
  1. Write a function ``def _cat_foo(ctx: ExtractionContext) -> None``
     (either inline here or in a sibling ``foo_extract.py`` module).
  2. Register it in ``CATEGORY_EXTRACTORS`` below.
  3. Ensure ``finalize.ALL_CATEGORIES`` and ``finalize_accum`` know about it.
"""
from __future__ import annotations

import html as _html_unescape
import json
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from loguru import logger
from selectolax.parser import HTMLParser

from .helpers import (
    canonicalize_phone_key,
    is_email_excluded,
    normalize_phone,
    phone_display,
    strip_wayback_artifacts,
    ts_to_month,
    update_entity,
)
from .patterns import (
    API_KEY_PATTERNS,
    CF_EMAIL_RE,
    CLOUD_BUCKET_PATTERNS,
    EMAIL_JS_CONCAT_RE,
    EMAIL_OBFUSCATED_AT_RE,
    EMAIL_RE,
    PHONE_RE,
    SOCIAL_PATTERNS,
    TRACKER_PATTERNS,
)
from .adsense_extract import extract_adsense_ids
from .connstring_extract import extract_connection_strings
from .crypto_extract import extract_crypto_addresses
from .favicon_extract import extract_favicons
from .french_business_ids_extract import extract_french_business_ids
from .hidden_fields_extract import extract_hidden_fields
from .hosting_extract import detect_hosting
from .iframe_extract import extract_iframe_sources
from .internal_ips_extract import extract_internal_ips
from .js_urls_extract import extract_js_urls
from .jsonld_structured_extract import extract_jsonld_structured
from .meta_info_extract import extract_meta_info
from .outgoing_links_extract import extract_outgoing_links
from .persons_extract import extract_persons
from .technologies_extract import extract_technologies
from .verification_extract import extract_verification_tags
from .assets_extract import extract_assets
from .analytics_ids_extract import extract_analytics_ids
from .analytics_ids_extract import _ID_DENYLIST as _TRACKER_ID_DENYLIST
from .cookie_consent_extract import extract_cookie_consent
from .rss_feeds_extract import extract_rss_feeds
from .github_repos_extract import extract_github_repos
from .sitemaps_extract import extract_sitemaps
from .pgp_keys_extract import extract_pgp_keys
from .bug_bounty_extract import extract_bug_bounty_programs
from .captcha_providers_extract import extract_captcha_providers
from .status_pages_extract import extract_status_pages
from .job_boards_extract import extract_job_boards
from .auth_providers_extract import extract_auth_providers
from .http_headers_extract import extract_http_headers


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class ExtractionContext:
    """Shared state passed to every per-category extractor.

    Holds the parsed document and the accumulator so extractors don't each
    re-parse the HTML or duplicate glue code.
    """
    tree: HTMLParser
    raw_text: str
    visible_text: str
    domain: str
    month: str
    accum: dict
    html: str
    # Original page URL. needed to resolve relative hrefs (e.g. /favicon.ico).
    page_url: str = ""
    # Optional Wayback-captured response headers for the archived page
    # (keys are the real header names, lowercased. the x-archive-orig-
    # prefix has been stripped upstream). Lets subdomain mining on CSP /
    # CORS work even when http_headers hasn't been populated yet.
    response_headers: dict | None = None


# Subdomain regex cache (per-domain, lazy)
_subdomain_re_cache: dict[str, re.Pattern] = {}


def _get_subdomain_re(domain: str) -> re.Pattern:
    pat = _subdomain_re_cache.get(domain)
    if pat is None:
        pat = re.compile(
            rf"([a-zA-Z0-9]([a-zA-Z0-9\-]{{0,61}}[a-zA-Z0-9])?\.{re.escape(domain)})"
        )
        _subdomain_re_cache[domain] = pat
    return pat


# ---------------------------------------------------------------------------
# Per-category extractors
# ---------------------------------------------------------------------------


# Asset extensions we never want to enter the endpoints bucket. they
# inflate the list with WebP / woff2 / map noise that buries real paths.
_ENDPOINT_EXT_SKIP = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|bmp|ico|woff2?|ttf|otf|eot|map)(?:\?|$)",
    re.IGNORECASE,
)

# Paths that are effectively app routes or API surfaces. Always accept as
# an endpoint even if a shallow substring test would otherwise flag them
# as asset-like (e.g. /wp-json lives under /wp- prefixes).
_ENDPOINT_ALLOWLIST_SUBSTRINGS = (
    "/wp-json/", "/api/", "/rest/", "/graphql", "/v1/", "/v2/", "/v3/",
    "/oauth", "/.well-known/",
)
_ENDPOINT_ALLOWLIST_EXACT = {
    "/admin", "/login", "/logout", "/signin", "/signup", "/register",
    "/auth", "/account", "/dashboard", "/profile",
}
_ENDPOINT_ALLOWLIST_PREFIXES = (
    "/admin/", "/login/", "/auth/", "/signin/", "/signup/",
    "/account/", "/dashboard/", "/profile/",
)
# Dedicated-extractor paths or low-value noise, dropped from both buckets.
_ENDPOINT_DROP_EXACT = {
    "/favicon.ico", "/robots.txt", "/sitemap.xml", "/sitemap_index.xml",
    "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
}
# URL-encoded inline SVG placeholders that leak into href attributes.
_SVG_DATAURL_RE = re.compile(r"%3csvg", re.IGNORECASE)


def _is_endpoint_allowlisted(path: str) -> bool:
    if path in _ENDPOINT_ALLOWLIST_EXACT:
        return True
    for sub in _ENDPOINT_ALLOWLIST_SUBSTRINGS:
        if sub in path:
            return True
    for pref in _ENDPOINT_ALLOWLIST_PREFIXES:
        if path.startswith(pref):
            return True
    return False
# Attributes that declare an API / URL on arbitrary elements. htmx uses
# hx-*, Alpine/AngularJS use ng-href, Webflow wires data-href on buttons.
_ENDPOINT_DATA_ATTRS = (
    "data-url", "data-endpoint", "data-api", "data-href", "data-action",
    "data-src", "hx-get", "hx-post", "hx-put", "hx-delete", "hx-patch",
    "ng-href", "formaction",
)
# Assets whose src/href is worth harvesting for endpoint mining -
# <iframe>, <script>, <link> are covered by dedicated extractors but
# their PATHS are still valid endpoints so we re-record here.
_ENDPOINT_ASSET_SELECTORS = (
    "link[href]", "script[src]", "img[src]", "source[src]",
    "video[src]", "audio[src]", "embed[src]", "object[data]",
)
# Inline script path harvester. Matches quoted strings that look like
# absolute paths: e.g. fetch("/api/v1/users"), {url: '/admin/whatever'}.
# Bounded to 200 chars to avoid a runaway match on a base64 blob.
_INLINE_URL_RE = re.compile(
    r"""["'`](/[A-Za-z0-9_\-./]{1,200}(?:\?[^"'`\s]{0,200})?)["'`]"""
)

# A path segment made only of date/time format tokens (YYYY, MM, DD, hh:mm...).
# Used to drop moment.js/dayjs format strings mistaken for endpoints.
_DATE_FORMAT_SEG_RE = re.compile(r"[YMDHhmsSAaZz][YMDHhmsSAaZz:.\-\s]*")


def _record_endpoint(ctx: ExtractionContext, raw: str) -> None:
    """Canonicalise and store a path endpoint, diverting assets into the
    dedicated ``assets`` bucket so real app routes don't drown in CSS/JS noise.
    """
    if not raw:
        return
    raw = raw.strip()
    if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "blob:")):
        return
    # SVG data-url placeholders sometimes leak past the data: prefix filter
    # because some templates URL-encode the leading chars.
    if _SVG_DATAURL_RE.search(raw):
        return
    candidate = raw
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return
    link_domain = (parsed.hostname or "").lower()
    path = (parsed.path or "/").rstrip("/") or "/"
    # Endpoints are absolute routes. A bare relative token like "museum" from
    # a legacy <a href="museum"> is not a reliable path, so drop it rather than
    # record it as the endpoint "museum".
    if not path.startswith("/"):
        return
    # Drop Wayback self-references.
    if link_domain == "web.archive.org" or path.startswith("/web/"):
        return
    # Only record when path-only or same-domain.
    if link_domain and not link_domain.endswith(ctx.domain):
        return
    if len(path) > 256:
        return
    # Hard-drop paths that have their own dedicated extractor.
    if path in _ENDPOINT_DROP_EXACT:
        return

    # Route asset-shaped paths to the `assets` bucket unless an allowlist
    # rule (e.g. /wp-json/, /api/) explicitly promotes them back to endpoints.
    from .assets_extract import classify_asset_path, is_asset_path
    allowlisted = _is_endpoint_allowlisted(path)
    if not allowlisted:
        asset_type = classify_asset_path(path)
        if asset_type is not None:
            update_entity(
                ctx.accum["assets"], path, ctx.month,
                {"path": path, "type": asset_type},
            )
            return
        if is_asset_path(path):
            update_entity(
                ctx.accum["assets"], path, ctx.month,
                {"path": path, "type": classify_asset_path(path) or "other"},
            )
            return
        # Legacy suffix filter: drop asset extensions we didn't route above
        # (e.g. .map without a known suffix hit).
        if _ENDPOINT_EXT_SKIP.search(path):
            return
    update_entity(ctx.accum["endpoints"], path, ctx.month, {"path": path})


def _cat_endpoints(ctx: ExtractionContext) -> None:
    """Harvest internal paths from every reasonable DOM source.

    The old implementation only looked at ``<a href>`` and
    ``<form action>``. Modern SPAs declare their API surface in inline
    scripts, htmx attributes, and preload tags. we now sweep all three.
    """
    # <a href> + <form action>
    for node in ctx.tree.css("a[href]"):
        _record_endpoint(ctx, node.attributes.get("href", ""))
    for node in ctx.tree.css("form[action]"):
        action = (node.attributes.get("action") or "").strip()
        if action and action != "#" and action.startswith("/"):
            _record_endpoint(ctx, action)

    # Asset selectors (link/script/img/media) are handled by extract_assets,
    # so _cat_endpoints no longer double-records them. Real endpoint paths
    # coming from those elements still surface via extract_assets diverting
    # non-asset-looking paths back to the endpoints bucket through
    # _record_endpoint, and manifest-style JSON references are caught below
    # via the meta[refresh] / anchor fallbacks.

    # meta http-equiv=refresh points at a redirect target.
    for node in ctx.tree.css('meta[http-equiv="refresh"]'):
        content = node.attributes.get("content") or ""
        m = re.search(r"url\s*=\s*([^;]+)", content, re.IGNORECASE)
        if m:
            _record_endpoint(ctx, m.group(1).strip().strip("'\""))

    # data-*/htmx attributes on arbitrary elements.
    for node in ctx.tree.css("*"):
        attrs = node.attributes or {}
        for key in _ENDPOINT_DATA_ATTRS:
            if key in attrs:
                _record_endpoint(ctx, attrs.get(key) or "")

    # Inline <script> content. SPA API surface. Cap per-script body
    # size (200 KB) so a giant minified bundle doesn't dominate regex time.
    for node in ctx.tree.css("script"):
        if node.attributes.get("src"):
            continue  # external scripts → <script src> branch above
        body = node.text() or ""
        if len(body) > 200_000:
            continue
        for m in _INLINE_URL_RE.finditer(body):
            cand = m.group(1)
            # Date/time format strings ("/YYYY/MM/DD", "/DD/MM/YYYY hh:mm") are a
            # classic false positive from moment.js/dayjs config in inline JS.
            # Skip when every path segment is only date-format tokens.
            path_part = cand.split("?", 1)[0]
            segs = [s for s in path_part.split("/") if s]
            if segs and all(_DATE_FORMAT_SEG_RE.fullmatch(s) for s in segs):
                continue
            _record_endpoint(ctx, cand)


# A narrow TLD whitelist applied only to the bracketed-obfuscation branch;
# otherwise English prose like "below", "inside", "above" matches the
# reassembler. Expand if a real email with an unlisted TLD gets missed.
_OBFUSCATED_EMAIL_TLD_WHITELIST = frozenset({
    "com", "org", "net", "io", "co", "ai", "app", "dev", "fr", "de", "es",
    "it", "uk", "us", "ca", "eu", "gov", "edu", "mil", "info", "biz", "me",
    "tv", "cc", "be", "nl", "pl", "pt", "se", "no", "fi", "dk", "ch", "at",
    "cz", "ro", "jp", "cn", "kr", "in", "br", "mx", "ar", "au", "nz",
    "tech", "cloud", "xyz", "site", "online", "store", "shop",
})


def _decode_cloudflare_email(hex_str: str) -> str | None:
    """Reverse Cloudflare's email-protection XOR obfuscation.

    The payload format: the first byte of the hex blob is the XOR key,
    remaining bytes hold the XOR'd email. Return ``None`` if the result
    isn't a plausible email so we don't emit garbage.
    """
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    key = raw[0]
    try:
        out = "".join(chr(c ^ key) for c in raw[1:])
    except ValueError:
        return None
    return out if EMAIL_RE.fullmatch(out) else None


# JSON-encoded HTML (e.g. window.__INITIAL_STATE__) escapes `<` and `>`
# as `\u003c` / `\u003e`. When the email regex matches inside that text
# the local-part can pick up a stray `u003e` prefix from a preceding
# `</a>` escape. Reject local-parts that start with that pattern.
_EMAIL_JSON_ESCAPE_LEAK_RE = re.compile(r"^u00[0-9a-f]{2}", re.IGNORECASE)


def _cat_emails(ctx: ExtractionContext) -> None:
    """Extract emails from raw HTML + three obfuscation variants commonly
    seen on WP/Drupal/Cloudflare pages.

    Branches:
      1. Plain EMAIL_RE on raw_text (covers mailto:, inline, JSON-LD).
      2. html.unescape the raw_text to catch ``foo&#64;bar.com`` and
         ``&#x40;`` numeric entities, re-run EMAIL_RE.
      3. CF_EMAIL_RE hits ``<a data-cfemail="…">``. XOR-decode.
      4. EMAIL_OBFUSCATED_AT_RE matches ``foo [at] bar [dot] com``-style
         textual dodges; reassemble and re-validate with EMAIL_RE so
         prose never emits noise.
      5. EMAIL_JS_CONCAT_RE catches ``"foo" + "@" + "bar.com"``.
    """
    seen: set[str] = set()

    def _emit(email: str) -> None:
        email = email.lower()
        local = email.split("@", 1)[0]
        if _EMAIL_JSON_ESCAPE_LEAK_RE.match(local):
            return
        if email in seen:
            return
        seen.add(email)
        if not is_email_excluded(email):
            update_entity(ctx.accum["emails"], email, ctx.month, {"value": email})

    # 1 + 2: plain + entity-decoded pass
    for txt in (ctx.raw_text, _html_unescape.unescape(ctx.raw_text)):
        for m in EMAIL_RE.finditer(txt):
            _emit(m.group())
    # 3: Cloudflare
    for m in CF_EMAIL_RE.finditer(ctx.raw_text):
        decoded = _decode_cloudflare_email(m.group(1))
        if decoded:
            _emit(decoded)
    # 4: [at]/[dot] textual obfuscation. scan visible_text only to reduce
    # FPs on raw HTML attributes. Additionally require the reassembled
    # TLD to be in a short public-suffix list so English prose like
    # "click [at] the [dot] below" doesn't register as click@the.below.
    for m in EMAIL_OBFUSCATED_AT_RE.finditer(ctx.visible_text):
        tld = m.group(3).lower()
        if tld not in _OBFUSCATED_EMAIL_TLD_WHITELIST:
            continue
        candidate = f"{m.group(1)}@{m.group(2)}.{tld}"
        if EMAIL_RE.fullmatch(candidate):
            _emit(candidate)
    # 5: JS concatenation
    for m in EMAIL_JS_CONCAT_RE.finditer(ctx.raw_text):
        _emit(f"{m.group(1)}@{m.group(2)}")


# Raw regex rejections for the visible-text phone regex. Each pattern
# matches a specific false-positive family (dates, IPs, GPS, salaries,
# version numbers, CSS pixels, year-prefixed IDs).
_PHONE_REJECT_PATTERNS = [
    re.compile(r"^\d{4}[-/.]\d{2}[-/.]\d{2}$"),       # YYYY-MM-DD dates (whole)
    re.compile(r"^(19|20)\d{6}$"),                     # YYYYMMDD digit-only dates
    re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}"),         # IP / semver prefix
    re.compile(r"^\d+\.\d+\.\d+"),                      # version numbers
    re.compile(r"^\d{7,}$"),                            # long bare digit sequences
    re.compile(r"^\d+px"),                              # CSS pixel values
    re.compile(r"\d+\.\d{3,}"),                         # GPS decimal degrees
    re.compile(r"\d[\s,]*000"),                         # salary/price ranges
    re.compile(r"^20\d{2}[-/]\d{3,}$"),                # year-prefixed IDs
]

# Keywords that, when present within a small window before a phone-like
# match, turn it from a coincidence into a signal. Branch 3 of PHONE_RE
# (two-digit groups joined by separators) is noisy on technical pages -
# port ranges, RFC numbers, dash-joined identifiers. so we demand a
# near-by keyword to accept it. International-prefix matches (leading "+")
# and paren-area-code matches already carry enough signal on their own.
# French additions (joindre, appeler, ligne, accueil, standard, num[ée]ro)
# catch natural prose around French contact pages where the literal
# "téléphone" rarely appears within the preceding window.
_PHONE_CONTEXT_KEYWORDS = re.compile(
    r"(?:phone|tel(?:ephone)?|t[ée]l(?:[ée]phone)?|fax|call|mobile|"
    r"whatsapp|viber|contact|hotline|numero|num[ée]ro|"
    r"joindre|appeler|ligne|accueil|standard|"
    r"nous\s+(?:joindre|appeler|contacter)|\bau\s*:?\s*$)",
    re.IGNORECASE,
)
_PHONE_CONTEXT_WINDOW = 80

# Tags whose textual content is never visible prose. Stripping them
# before the phone scan removes the dominant FP families: SVG <path d=…>
# coords, CSS rgba decimals, JS coordinate literals, parking-page blobs.
_PHONE_NOISE_TAGS = ("script", "style", "svg", "noscript", "template")

# data-* attributes commonly used to wire phone numbers into JS widgets
# (click-to-call buttons, masked dial-out, analytics).
_PHONE_DATA_ATTRS = ("data-phone", "data-tel", "data-telephone", "data-mobile")

# Keys inside JSON blobs (any <script>, not just JSON-LD) that carry
# phone numbers. Schema.org uses "telephone"; many CRMs use "phone" /
# "phoneNumber"; widgets nest under "tel" / "mobile".
_PHONE_JSON_KEYS = ("telephone", "phone", "phonenumber", "tel", "mobile")

# Match a JSON-style key/value pair like "telephone":"+33 1 …".
# The value is captured raw so the phone validators can normalise it;
# bounded to 6-40 chars so we don't drag in unrelated long strings.
_PHONE_JSON_KV_RE = re.compile(
    r"""["']({keys})["']\s*:\s*["']([^"']{{6,40}})["']""".format(
        keys="|".join(_PHONE_JSON_KEYS),
    ),
    re.IGNORECASE,
)

# A French phone, post-normalization. Accepts +33/0033/0 prefix, second
# digit 1-7 or 9 (skips 08 premium and the impossible 00), 10 total
# national digits. Used as a strong-signal validator when no keyword is
# nearby.
_FR_PHONE_DIGITS_RE = re.compile(r"^(?:\+?33|0)[1-79]\d{8}$")


def _has_phone_context(text: str, match_start: int) -> bool:
    window_start = max(0, match_start - _PHONE_CONTEXT_WINDOW)
    return bool(_PHONE_CONTEXT_KEYWORDS.search(text[window_start:match_start]))


def _is_valid_french_phone(digits_only: str) -> bool:
    """True for plausibly-real French national/international numbers.

    Strict gate: 10 national digits starting 0[1-79], or +33 then [1-79]
    plus eight digits. Rejects 0[08]xxx (00 = malformed; 08 = premium /
    often noise), and everything outside the 10-digit French dial plan.
    """
    return bool(_FR_PHONE_DIGITS_RE.fullmatch(digits_only))


def _strip_noise_for_phones(html: str) -> str:
    """Return visible text with script/style/svg/noscript/template removed.

    A fresh parse is used so we do not mutate the shared ``ctx.tree`` -
    other extractors still need scripts and SVG content. The cost is one
    extra parse per page; selectolax is C-based and the overhead is
    negligible compared to the FP reduction (709 SVG-coord matches were
    observed on a single 210-page corpus before this filter).
    """
    local = HTMLParser(html)
    for tag in _PHONE_NOISE_TAGS:
        for node in local.css(tag):
            node.decompose()
    return local.text(separator=" ")


def _emit_phone(ctx: ExtractionContext, raw: str) -> None:
    """Validate then accumulate a phone candidate from any source."""
    raw = (raw or "").strip()
    if not raw:
        return
    normalized = normalize_phone(raw)
    digits_only = re.sub(r"[^\d]", "", normalized)
    if len(digits_only) < 7 or len(digits_only) > 15:
        return
    key = canonicalize_phone_key(digits_only)
    update_entity(
        ctx.accum["phones"], key, ctx.month,
        {
            "raw": raw, "normalized": normalized,
            "value": phone_display(key, normalized),
        },
    )


def _cat_phones(ctx: ExtractionContext) -> None:
    # Visible-text scan: strip JS/CSS/SVG so coords/rgba/parking-page
    # blobs never reach the regex. ctx.visible_text comes from the shared
    # tree.text() which walks every text node, including noise tags.
    text = _strip_noise_for_phones(ctx.html)
    for match in PHONE_RE.finditer(text):
        raw = match.group().strip()
        normalized = normalize_phone(raw)
        digits_only = re.sub(r"[^\d]", "", normalized)
        if len(digits_only) < 7 or len(digits_only) > 15:
            continue
        if any(p.match(raw) or p.search(raw) for p in _PHONE_REJECT_PATTERNS):
            continue
        if "." in raw:
            # any string containing a decimal point (prices, floats, CSS)
            continue
        # Accept unconditionally if the match carries its own signal
        # (international prefix, parenthesised area code, or a strict
        # French-shape match); otherwise require a phone-ish keyword in
        # the preceding window.
        has_signal = (
            raw.startswith("+")
            or "(" in raw
            or _is_valid_french_phone(digits_only)
        )
        if not has_signal and not _has_phone_context(text, match.start()):
            continue
        key = canonicalize_phone_key(digits_only)
        update_entity(
            ctx.accum["phones"], key, ctx.month,
            {
                "raw": raw, "normalized": normalized,
                "value": phone_display(key, normalized),
            },
        )

    # Phones from <a href="tel:..."> always count. the scheme itself is
    # explicit intent.
    for node in ctx.tree.css('a[href^="tel:"]'):
        href = node.attributes.get("href", "")
        _emit_phone(ctx, href[4:])

    # data-phone / data-tel / data-telephone / data-mobile. explicit
    # intent on any element (commonly buttons / spans wired to click-to-
    # call JS handlers).
    for attr in _PHONE_DATA_ATTRS:
        for node in ctx.tree.css(f"[{attr}]"):
            value = node.attributes.get(attr) or ""
            _emit_phone(ctx, value)

    # JSON-blob phone keys inside ANY <script> (not just JSON-LD): CRM
    # widgets, hydration payloads, wp-block-data, inline window.* config
    # blobs that carry "telephone":"…" / "phone":"…". The key whitelist
    # keeps this from harvesting arbitrary strings.
    for node in ctx.tree.css("script"):
        body = node.text() or ""
        if not body:
            continue
        # Cheap pre-check on potentially large script bodies before the
        # full regex pass.
        lower = body.lower()
        if not any(k in lower for k in _PHONE_JSON_KEYS):
            continue
        for m in _PHONE_JSON_KV_RE.finditer(body):
            _emit_phone(ctx, m.group(2))


def _record_subdomain(ctx: ExtractionContext, raw_sub: str, source: str) -> None:
    """Normalise and store a subdomain candidate with a provenance label.

    Called for *structured* sources (DOM attrs, headers, snapshot URLs).
    The hex-prefix trim in the raw-text branch is deliberately NOT applied
    here because ``api`` / ``cdn`` / ``dev`` / ``fr`` would all be
    mis-classified as hex prefixes and truncated.
    """
    sub = (raw_sub or "").lower().strip().rstrip(".")
    if not sub or sub == ctx.domain or sub == f"www.{ctx.domain}":
        return
    if not sub.endswith("." + ctx.domain):
        return
    # Reject any host containing a non-LDH (letter / digit / hyphen / dot)
    # character. guards against IDN homographs (xn--… is allowed because
    # it is plain ASCII; raw unicode is not) and stray punctuation captured
    # by upstream parsers.
    if not re.fullmatch(r"[a-z0-9.-]+", sub):
        return
    label = sub.split(".", 1)[0]
    if not label or label.startswith("-") or label.endswith("-"):
        return
    # Canonical key strips a leading "www." so we don't store both
    # ``www.foo.bar`` and ``foo.bar`` as separate subdomains. Keep the
    # original sub in the metadata so the user can see if the www variant
    # was actually observed.
    canonical = sub[4:] if sub.startswith("www.") and sub != f"www.{ctx.domain}" else sub
    if canonical == ctx.domain:
        return
    update_entity(
        ctx.accum["subdomains"], canonical, ctx.month,
        {"value": canonical, "source": source},
    )


def _record_subdomain_from_url(
    ctx: ExtractionContext, raw_url: str, source: str
) -> None:
    """Extract the hostname from a URL string and record it as a subdomain.

    Handles scheme-relative ``//host/path`` URLs and silently ignores
    malformed inputs. Used by the DOM-attr, JSON-LD and snapshot-URL
    mining branches.
    """
    if not raw_url:
        return
    candidate = raw_url.strip()
    if not candidate or candidate.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "blob:")):
        return
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    try:
        host = urlparse(candidate).hostname or ""
    except ValueError:
        return
    if host:
        _record_subdomain(ctx, host, source)


# DOM attribute selectors swept for same-owner subdomain hostnames. We
# intentionally include <a href> here even though _cat_endpoints already
# touches it. the goal is hostname extraction, not path bucketing, and
# the two registries are independent.
_SUBDOMAIN_HOST_SELECTORS = (
    ("a[href]", "href"),
    ("img[src]", "src"),
    ("script[src]", "src"),
    ("link[href]", "href"),
    ("iframe[src]", "src"),
    ("source[src]", "src"),
    ("video[src]", "src"),
    ("audio[src]", "src"),
    ("embed[src]", "src"),
    ("object[data]", "data"),
    ("form[action]", "action"),
)
# srcset is multi-URL. handled separately because it needs comma-splitting
# and the URL is the first whitespace-separated token of each entry.
_SUBDOMAIN_SRCSET_SELECTORS = ("img[srcset]", "source[srcset]")
# JSON-LD keys whose values are URLs we should mine for subdomains. Lower-
# cased to match keys case-insensitively.
_JSONLD_URL_KEYS = {"url", "sameas", "@id", "logo", "image", "thumbnailurl", "contenturl"}


# Headers whose values commonly list additional same-owner subdomains:
# CSP enumerates approved script/connect/img origins, CORS lists allowed
# origins, Permissions-Policy delegates features to specific origins,
# Link: rel=preload/preconnect references first-party CDN hosts, Via
# may mention proxy/front-door subdomains.
_HEADER_SUBDOMAIN_SOURCES = (
    "content-security-policy",
    "content-security-policy-report-only",
    "access-control-allow-origin",
    "permissions-policy",
    "feature-policy",
    "link",
    "via",
)


def _cat_subdomains(ctx: ExtractionContext) -> None:
    """Harvest subdomains from five sources:

    1. The page text via the per-domain regex (original behaviour;
       includes inline JS strings like ``"url":"https://api.x.com/…"``).
    2. DOM attribute hostnames. ``<a href>``, ``<img src>``,
       ``<script src>``, ``<link href>``, ``<iframe src>``, srcset, etc.
       The regex anchors on the apex domain so this branch never picks
       up foreign hosts.
    3. JSON-LD ``url`` / ``sameAs`` / ``@id`` / ``logo`` / ``image``
       fields. many sites declare canonical URLs on additional same-
       owner hosts here.
    4. ``<link rel="dns-prefetch|preconnect|preload|prerender">`` -
       site-author-declared same-owner hosts, high confidence.
    5. HTTP response headers exposed by Wayback (x-archive-orig-*):
       Content-Security-Policy, CORS, Permissions-Policy, Link, Via.

    The CDX snapshot URLs themselves are mined separately at finalize time
    (see ``finalize._mine_subdomains_from_snapshot_urls``) because pages
    whose scrape failed (html=None) never reach this function.
    """
    subdomain_re = _get_subdomain_re(ctx.domain)
    raw_text = ctx.raw_text

    # 1. Text-based extraction (preserves original FP guards). Inline JS
    # strings like '"url":"https://api.x.com/v2"' fall out of this branch
    # because the regex matches anywhere in the document body.
    for match in subdomain_re.finditer(raw_text):
        sub = match.group(0).lower()
        if sub == ctx.domain or sub == f"www.{ctx.domain}":
            continue
        idx = match.start()
        # Skip URL-percent-encoded fragments. The regex starts on the
        # alnum char immediately AFTER the '%' (e.g. matches "2Fwww.acme.io"
        # inside "%2Fwww.acme.io"), so the previous character is the '%'
        # itself. The original guard checked two chars back, which never
        # fired in real captures.
        if idx >= 1 and raw_text[idx - 1] == "%":
            continue
        # Legacy hex-prefix trim. handles backslash-x escape leftovers
        # like "\\x32Fwww.acme.io" where the regex caught a tail. Apply
        # only when the preceding character looks like an encoding artefact
        # (backslash or another hex digit). Otherwise plain labels like
        # 'api', 'cdn', 'dev', 'fr' would all get amputated to 'pi', 'dn',
        # 'v', 'r'.
        prev = raw_text[idx - 1] if idx >= 1 else ""
        if prev == "\\" or (prev and prev.lower() in "0123456789abcdef"):
            if re.match(r"^[0-9a-f]{1,2}[a-z]", sub):
                clean = re.sub(r"^[0-9a-f]{1,2}", "", sub)
                if clean and clean[0] != ".":
                    sub = clean
                else:
                    continue
        # Re-check apex after possible trim.
        if sub == ctx.domain or sub == f"www.{ctx.domain}":
            continue
        # Same canonicalization rule as _record_subdomain: strip a www.
        # prefix unless it would collapse to the apex.
        canonical = sub[4:] if sub.startswith("www.") and sub != f"www.{ctx.domain}" else sub
        if canonical == ctx.domain:
            continue
        update_entity(
            ctx.accum["subdomains"], canonical, ctx.month,
            {"value": canonical, "source": "html"},
        )

    # 2. DOM attribute hostnames. the cheapest, highest-signal source.
    for sel, attr in _SUBDOMAIN_HOST_SELECTORS:
        for node in ctx.tree.css(sel):
            _record_subdomain_from_url(ctx, node.attributes.get(attr) or "", "dom")
    # srcset values are comma-separated "<url> <descriptor>" pairs.
    for sel in _SUBDOMAIN_SRCSET_SELECTORS:
        for node in ctx.tree.css(sel):
            val = node.attributes.get("srcset") or ""
            for token in val.split(","):
                _record_subdomain_from_url(
                    ctx, token.strip().split(" ", 1)[0], "srcset"
                )

    # 3. JSON-LD structured data. walk the parsed object graph and pull
    # URL-bearing fields. selectolax already gave us the script bodies for
    # free; json.loads failures fall back to a regex sweep so half-broken
    # blobs still contribute.
    for node in ctx.tree.css('script[type="application/ld+json"]'):
        body = node.text() or ""
        if not body.strip():
            continue
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            for m in subdomain_re.finditer(body):
                _record_subdomain(ctx, m.group(0), "jsonld")
            continue
        _walk_jsonld_for_subdomains(ctx, data)

    # 4. DNS preload hints. site-author declarations, high signal.
    # Iterate all link tags and inspect rel value ourselves. selectolax
    # has limited CSS-attribute-selector coverage so we do it in Python
    # for robustness.
    _DNS_HINT_RELS = {"dns-prefetch", "preconnect", "preload", "prerender"}
    for node in ctx.tree.css("link[rel]"):
        rel = (node.attributes.get("rel") or "").strip().lower()
        if not any(tok in _DNS_HINT_RELS for tok in rel.split()):
            continue
        _record_subdomain_from_url(
            ctx, node.attributes.get("href") or "", "dns-hint"
        )

    # 5. HTTP-header re-mining. Prefer the raw response_headers passed
    # through the context (populated by the analyze pipeline). it's
    # available before _cat_http_headers runs, which matters because the
    # registry order is 'subdomains' first.
    for header_name, value in (ctx.response_headers or {}).items():
        if header_name.lower() not in _HEADER_SUBDOMAIN_SOURCES:
            continue
        for m in subdomain_re.finditer(str(value)):
            _record_subdomain(ctx, m.group(0), f"header:{header_name.lower()}")


def _walk_jsonld_for_subdomains(ctx: ExtractionContext, obj) -> None:
    """Recursively walk a JSON-LD-shaped object and feed URL-like values
    into the subdomain recorder. Handles dict/list/string mix.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = (k or "").lower() if isinstance(k, str) else ""
            if isinstance(v, str):
                if key in _JSONLD_URL_KEYS:
                    _record_subdomain_from_url(ctx, v, "jsonld")
            elif isinstance(v, (list, tuple)):
                for item in v:
                    if isinstance(item, str):
                        if key in _JSONLD_URL_KEYS:
                            _record_subdomain_from_url(ctx, item, "jsonld")
                    else:
                        _walk_jsonld_for_subdomains(ctx, item)
            elif isinstance(v, dict):
                _walk_jsonld_for_subdomains(ctx, v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _walk_jsonld_for_subdomains(ctx, item)


def _is_placeholder_id(tid: str) -> bool:
    """True for documentation/boilerplate IDs (GTM-XXXXXX, G-XXXXXXXXXX, ...).

    The sibling analytics_ids extractor denies these via _ID_DENYLIST; the
    tracker extractor covers the same identifiers and must agree, otherwise the
    same placeholder is dropped in one module and leaks into the other.
    """
    if tid in _TRACKER_ID_DENYLIST:
        return True
    body = tid.split("-", 1)[-1].replace("-", "")
    return bool(body) and set(body.upper()) == {"X"}


def _cat_analytics_trackers(ctx: ExtractionContext) -> None:
    for tracker_type, pattern in TRACKER_PATTERNS.items():
        for match in pattern.finditer(ctx.raw_text):
            tid = match.group(1) if match.lastindex else match.group()
            if _is_placeholder_id(tid):
                continue
            key = f"{tracker_type}:{tid}"
            update_entity(
                ctx.accum["analytics_trackers"], key, ctx.month,
                {"type": tracker_type, "id": tid},
            )


_SOCIAL_EXCLUDE_HANDLES = {
    "share", "intent", "sharer", "dialog", "plugins", "widgets",
    "platform", "about", "help", "settings", "privacy", "terms",
    "login", "signup", "search", "explore", "notifications",
    "linkedin", "facebook", "twitter", "instagram", "youtube",
    "necolas", "nicgirault",  # normalize.css and similar lib authors
}

# Platform-specific first-path segments that are UI routes, not user
# handles (github.com/features, instagram.com/reel/<id>, facebook.com/tr -
# the Meta Pixel beacon, twitter.com/home, …). Kept per-platform on
# purpose: a Twitter account literally named "features" is still a real
# handle, so these must not leak into the global exclude set.
_SOCIAL_RESERVED_BY_PLATFORM = {
    "github": {
        "features", "pricing", "marketplace", "topics", "sponsors",
        "enterprise", "codespaces", "collections", "trending", "readme",
        "security", "customer-stories", "team", "pulls", "issues", "new",
        "orgs", "organizations", "apps", "join", "pricing", "contact",
    },
    "instagram": {"reel", "reels", "stories", "tv", "accounts", "directory"},
    "facebook": {
        "tr", "pages", "groups", "events", "watch", "gaming",
        "profile.php", "marketplace", "bookmarks",
    },
    "twitter": {"home", "hashtag", "messages", "compose", "tos", "privacy"},
    "x": {"home", "hashtag", "messages", "compose"},
    "pinterest": {"pin", "search", "categories", "ideas", "today", "business"},
}


# LinkedIn falls back to an opaque "ACoA…" member identifier when a
# profile slug isn't available (e.g. when the URL was generated from a
# tracking link). These IDs aren't usable handles and pollute the
# social_profiles list. drop them.
_LINKEDIN_ANON_HANDLE_RE = re.compile(r"^ACoA[A-Za-z0-9_\-]{20,}$")


def _cat_social_profiles(ctx: ExtractionContext) -> None:
    for platform, pattern in SOCIAL_PATTERNS.items():
        for match in pattern.finditer(ctx.raw_text):
            handle = match.group(1).rstrip("/")
            if not handle or len(handle) < 2:
                continue
            if handle.lower() in _SOCIAL_EXCLUDE_HANDLES:
                continue
            reserved = _SOCIAL_RESERVED_BY_PLATFORM.get(platform)
            if reserved and handle.lower() in reserved:
                continue
            if platform == "linkedin" and _LINKEDIN_ANON_HANDLE_RE.match(handle):
                continue
            key = f"{platform}:{handle.lower()}"
            url_map = {
                "twitter": f"https://twitter.com/{handle}",
                "x": f"https://x.com/{handle}",
                "facebook": f"https://facebook.com/{handle}",
                "instagram": f"https://instagram.com/{handle}",
                "telegram": f"https://t.me/{handle}",
                "youtube": f"https://youtube.com/{handle}",
                "github": f"https://github.com/{handle}",
                "tiktok": f"https://tiktok.com/@{handle}",
                "snapchat": f"https://snapchat.com/add/{handle}",
                "discord": f"https://discord.gg/{handle}",
                "pinterest": f"https://pinterest.com/{handle}",
            }
            if platform == "linkedin":
                segment = "company" if "/company/" in match.group(0) else "in"
                url = f"https://linkedin.com/{segment}/{handle}"
            else:
                url = url_map.get(platform, "")
            update_entity(
                ctx.accum["social_profiles"], key, ctx.month,
                {"platform": platform, "handle": handle, "url": url},
            )


def _cat_persons(ctx: ExtractionContext) -> None:
    extract_persons(ctx.tree, ctx.raw_text, ctx.month, ctx.accum, domain=ctx.domain)


def _cat_technologies(ctx: ExtractionContext) -> None:
    extract_technologies(ctx.tree, ctx.raw_text, ctx.month, ctx.accum)


def _cat_cloud_buckets(ctx: ExtractionContext) -> None:
    for pattern in CLOUD_BUCKET_PATTERNS:
        for match in pattern.finditer(ctx.raw_text):
            bucket = match.group(0).lower()
            update_entity(ctx.accum["cloud_buckets"], bucket, ctx.month, {"value": bucket})


# Some "API keys" are designed to be public (frontend SDK keys, OAuth
# client IDs paired with a server-side secret). They still pivot. they
# tell you which Stripe account / Google project the operator owns -
# but treating them as a LEAK is misleading. Distinguish them so the
# highlights view can stratify severity correctly.
def _api_key_tier(key_type: str, value: str) -> str:
    """Return ``"public"`` for keys safe to embed in a webpage, else
    ``"secret"``. Public keys are PIVOT-grade; secrets are LEAK-grade."""
    if key_type in ("Google_OAuth_Client", "Mapbox", "Sentry_DSN"):
        # Mapbox pk.* and Sentry DSNs are designed to ship to the
        # browser; the OSINT value is in the org/project they reveal,
        # not in the key itself. Tag as public so highlights demote
        # them from LEAK to PIVOT.
        return "public"
    if key_type == "Stripe":
        # pk_test_/pk_live_ are publishable keys. sk_*/rk_* stay secret.
        if value.startswith("pk_"):
            return "public"
        return "secret"
    return "secret"


# Some key shapes are structurally identical to common non-secret strings
# (a Twilio AC/SK SID is just "AC"/"SK" + 32 hex - the same shape as an md5
# hash or a framework react-id). They land in LEAK, where a false positive
# is actively misleading, so require a vendor keyword in the surrounding
# window. Real leaked SIDs sit next to the vendor name or the config key;
# bare AC<32hex> with no context is almost always a hash.
_API_KEY_CONTEXT_REQUIRED = {
    "Twilio": re.compile(r"twilio|account[_\-]?sid|auth[_\-]?token", re.IGNORECASE),
}
_API_KEY_CONTEXT_WINDOW = 60


def _cat_api_keys(ctx: ExtractionContext) -> None:
    for key_type, pattern in API_KEY_PATTERNS.items():
        context_re = _API_KEY_CONTEXT_REQUIRED.get(key_type)
        for match in pattern.finditer(ctx.raw_text):
            if context_re is not None:
                lo = max(0, match.start() - _API_KEY_CONTEXT_WINDOW)
                hi = min(len(ctx.raw_text), match.end() + _API_KEY_CONTEXT_WINDOW)
                if not context_re.search(ctx.raw_text[lo:hi]):
                    continue
            secret = match.group(0)
            tier = _api_key_tier(key_type, secret)
            update_entity(
                ctx.accum["api_keys"], secret, ctx.month,
                {"type": key_type, "value": secret, "tier": tier},
            )


_DOC_EXTENSIONS = re.compile(
    r'\.(?:pdf|doc|docx|xls|xlsx|csv|ppt|pptx|txt|rtf|odt|ods|epub)$',
    re.IGNORECASE,
)


def _cat_linked_documents(ctx: ExtractionContext) -> None:
    seen: set[str] = set()
    for node in ctx.tree.css("a[href]"):
        # Some malformed pages return None from the attribute getter even
        # though `[href]` was the selector. guard with `or ""`.
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        # Test the PATH only. A doc extension living in a query value
        # (/download?file=report.docx) or before a fragment
        # (/report.pdf#page=3) must be judged on the real path, not the whole
        # href, otherwise viewers/proxies produce false positives.
        path_only = href.split("#", 1)[0].split("?", 1)[0]
        if _DOC_EXTENSIONS.search(path_only):
            if path_only in seen:
                continue
            seen.add(path_only)
            ext = re.search(r'\.(\w+)$', path_only)
            ext_str = ext.group(1).lower() if ext else "unknown"
            update_entity(
                ctx.accum["linked_documents"], path_only, ctx.month,
                {"url": href, "extension": ext_str},
            )


_COMMENT_EXCLUDE = {"wayback", "end ", "begin ", "[if ", "[endif", "<!-", "<!["}

# Substring denylist: HTML comments matching any of these are boilerplate
# CMS / analytics noise (Webflow editor, GTM scaffolding, Yoast SEO, etc.)
# rather than something a human author wrote.
_COMMENT_NOISE_SUBSTRINGS = (
    "google tag manager",
    "[attributes by finsweet]",
    "finsweet cookie consent",
    "last published:",            # Webflow editor watermark
    "powered by webflow",
    "powered by squarespace",
    "powered by wix",
    "this site is optimized with the yoast",
    "begin yoast", "end yoast",
    "facebook pixel code", "end facebook pixel code",
    "hotjar tracking code",
    "google analytics",
    "begin gtm", "end gtm",
    "matomo",
    "powered by shopify",
)


def _cat_html_comments(ctx: ExtractionContext) -> None:
    for match in re.finditer(r'<!--(.*?)-->', ctx.raw_text, re.DOTALL):
        comment = match.group(1).strip()
        if len(comment) < 10 or len(comment) > 500:
            continue
        lower = comment.lower()
        if any(lower.startswith(exc) for exc in _COMMENT_EXCLUDE):
            continue
        if "wayback" in lower:
            continue
        if any(noise in lower for noise in _COMMENT_NOISE_SUBSTRINGS):
            continue
        key = comment[:80].lower()
        update_entity(
            ctx.accum["html_comments"], key, ctx.month,
            {"comment": comment[:300]},
        )


def _cat_meta_info(ctx: ExtractionContext) -> None:
    extract_meta_info(ctx.tree, ctx.month, ctx.accum)


def _cat_hidden_fields(ctx: ExtractionContext) -> None:
    # Pass ctx.tree so the sub-extractor doesn't re-parse the HTML. On a
    # 500-page scan this removes ~5 redundant HTMLParser allocations per
    # page × 500 pages = 2500 saved parses.
    for field in extract_hidden_fields(ctx.html, tree=ctx.tree):
        key = f"{field['name']}:{field['value'][:40]}"
        update_entity(ctx.accum["hidden_fields"], key, ctx.month, field)


def _cat_internal_ips(ctx: ExtractionContext) -> None:
    for ip_info in extract_internal_ips(ctx.raw_text):
        update_entity(ctx.accum["internal_ips"], ip_info["ip"], ctx.month, ip_info)


def _cat_adsense_ids(ctx: ExtractionContext) -> None:
    for ad in extract_adsense_ids(ctx.raw_text):
        key = f"{ad['type']}:{ad['id']}"
        update_entity(ctx.accum["adsense_ids"], key, ctx.month, ad)


def _cat_verification_tags(ctx: ExtractionContext) -> None:
    for tag in extract_verification_tags(ctx.html, tree=ctx.tree):
        key = f"{tag['service']}:{tag['verification_id']}"
        update_entity(ctx.accum["verification_tags"], key, ctx.month, tag)


def _cat_iframe_sources(ctx: ExtractionContext) -> None:
    for iframe in extract_iframe_sources(ctx.html, tree=ctx.tree):
        update_entity(ctx.accum["iframe_sources"], iframe["url"], ctx.month, iframe)


def _cat_js_urls(ctx: ExtractionContext) -> None:
    for js_url in extract_js_urls(ctx.html, tree=ctx.tree):
        update_entity(ctx.accum["js_urls"], js_url["url"], ctx.month, js_url)


def _cat_connection_strings(ctx: ExtractionContext) -> None:
    for conn in extract_connection_strings(ctx.raw_text):
        update_entity(ctx.accum["connection_strings"], conn["value"], ctx.month, conn)


def _cat_crypto_addresses(ctx: ExtractionContext) -> None:
    for addr in extract_crypto_addresses(ctx.raw_text):
        update_entity(ctx.accum["crypto_addresses"], addr["address"], ctx.month, addr)


def _cat_favicons(ctx: ExtractionContext) -> None:
    for fav in extract_favicons(ctx.html, tree=ctx.tree, page_url=ctx.page_url):
        update_entity(ctx.accum["favicons"], fav["url"], ctx.month, fav)


def _cat_outgoing_links(ctx: ExtractionContext) -> None:
    for link in extract_outgoing_links(ctx.html, ctx.domain, tree=ctx.tree):
        update_entity(ctx.accum["outgoing_links"], link["url"], ctx.month, link)


def _cat_hosting(ctx: ExtractionContext) -> None:
    for host in detect_hosting(ctx.raw_text, headers=ctx.response_headers):
        update_entity(ctx.accum["hosting"], host["provider"], ctx.month, host)


def _cat_french_business_ids(ctx: ExtractionContext) -> None:
    for entity in extract_french_business_ids(ctx.raw_text):
        update_entity(
            ctx.accum["french_business_ids"], entity["value"], ctx.month, entity,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _cat_http_headers(ctx: ExtractionContext) -> None:
    """Populate http_headers from the original response headers Wayback kept
    (x-archive-orig-*, already prefix-stripped by the scraper)."""
    for item in extract_http_headers(ctx.response_headers):
        key = f"{item['header']}:{item['value']}"
        update_entity(
            ctx.accum["http_headers"], key, ctx.month,
            {"type": item["type"], "header": item["header"], "value": item["value"]},
        )


CATEGORY_EXTRACTORS: dict[str, Callable[[ExtractionContext], None]] = {
    "http_headers": _cat_http_headers,
    "endpoints": _cat_endpoints,
    "emails": _cat_emails,
    "phones": _cat_phones,
    "subdomains": _cat_subdomains,
    "analytics_trackers": _cat_analytics_trackers,
    "social_profiles": _cat_social_profiles,
    "persons": _cat_persons,
    "technologies": _cat_technologies,
    "cloud_buckets": _cat_cloud_buckets,
    "api_keys": _cat_api_keys,
    "linked_documents": _cat_linked_documents,
    "html_comments": _cat_html_comments,
    "meta_info": _cat_meta_info,
    "hidden_fields": _cat_hidden_fields,
    "internal_ips": _cat_internal_ips,
    "adsense_ids": _cat_adsense_ids,
    "verification_tags": _cat_verification_tags,
    "iframe_sources": _cat_iframe_sources,
    "js_urls": _cat_js_urls,
    "connection_strings": _cat_connection_strings,
    "crypto_addresses": _cat_crypto_addresses,
    "favicons": _cat_favicons,
    "outgoing_links": _cat_outgoing_links,
    "hosting": _cat_hosting,
    "french_business_ids": _cat_french_business_ids,
    "assets": lambda ctx: extract_assets(ctx.tree, ctx.month, ctx.accum),
    "analytics_ids": lambda ctx: extract_analytics_ids(ctx.tree, ctx.raw_text, ctx.month, ctx.accum),
    "cookie_consent": lambda ctx: extract_cookie_consent(ctx.tree, ctx.raw_text, ctx.month, ctx.accum),
    "rss_feeds": lambda ctx: extract_rss_feeds(ctx.tree, ctx.raw_text, ctx.page_url, ctx.month, ctx.accum),
    "github_repos": lambda ctx: extract_github_repos(ctx.tree, ctx.raw_text, ctx.month, ctx.accum),
    "sitemaps_and_robots": lambda ctx: extract_sitemaps(ctx.tree, ctx.raw_text, ctx.page_url, ctx.month, ctx.accum),
    "pgp_keys": lambda ctx: extract_pgp_keys(ctx.tree, ctx.raw_text, ctx.month, ctx.accum),
    "bug_bounty_programs": lambda ctx: extract_bug_bounty_programs(ctx.raw_text, ctx.month, ctx.accum),
    "captcha_providers": lambda ctx: extract_captcha_providers(ctx.raw_text, ctx.month, ctx.accum),
    "status_pages": lambda ctx: extract_status_pages(ctx.raw_text, ctx.month, ctx.accum),
    "job_boards": lambda ctx: extract_job_boards(ctx.raw_text, ctx.month, ctx.accum),
    "auth_providers": lambda ctx: extract_auth_providers(ctx.raw_text, ctx.month, ctx.accum),
}


# JSON-LD structured data fans out across multiple buckets (persons, phones,
# organizations, addresses), so it runs if any of those is requested.
_JSONLD_STRUCTURED_CATEGORIES = {"persons", "phones", "organizations", "addresses"}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_page(
    html: str, page_url: str, timestamp: str, domain: str, accum: dict,
    categories: set[str] | None = None,
    response_headers: dict | None = None,
) -> None:
    html = strip_wayback_artifacts(html)
    tree = HTMLParser(html)
    ctx = ExtractionContext(
        tree=tree,
        raw_text=html,
        visible_text=tree.text(separator=" "),
        domain=domain,
        month=ts_to_month(timestamp),
        accum=accum,
        html=html,
        page_url=page_url,
        response_headers=response_headers,
    )

    # Per-category isolation: one extractor's crash must not lose the page.
    # Without this, a NoneType in any single category lets extract_page_safe
    # discard EVERY category's findings for the page (lost emails, phones,
    # subdomains... all wiped by one stray .strip() on None).
    for cat, fn in CATEGORY_EXTRACTORS.items():
        if categories is not None and cat not in categories:
            continue
        try:
            fn(ctx)
        except Exception as exc:
            logger.warning(
                "Category {} failed on {}: {}: {}",
                cat, page_url, type(exc).__name__, exc,
            )

    if categories is None or categories & _JSONLD_STRUCTURED_CATEGORIES:
        try:
            extract_jsonld_structured(ctx.tree, ctx.raw_text, ctx.month, ctx.accum, ctx.domain)
        except Exception as exc:
            logger.warning(
                "JSON-LD structured failed on {}: {}: {}",
                page_url, type(exc).__name__, exc,
            )
