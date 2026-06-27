"""HTTP response header extraction from archived Wayback pages.

Chantier 5: processes the `x-archive-orig-*` headers Wayback preserves from
the original upstream response. Produces findings for tech-fingerprint-useful
headers like Server, X-Powered-By, Strict-Transport-Security, CSP, Via, etc.
Cookie names are captured but not cookie values.
"""
from __future__ import annotations


# Headers worth storing as findings. Each entry:
#   (header_name, finding_type_label, truncate_len)
_TRACKED_HEADERS: list[tuple[str, str, int]] = [
    ("server",                       "server",        200),
    ("x-powered-by",                 "x_powered_by",  200),
    ("x-aspnet-version",             "aspnet_version", 80),
    ("x-aspnetmvc-version",          "aspnetmvc_version", 80),
    ("x-generator",                  "generator_hdr", 200),
    ("strict-transport-security",    "hsts",          200),
    ("content-security-policy",      "csp",           500),
    ("content-security-policy-report-only", "csp_ro", 500),
    ("x-frame-options",              "x_frame",        80),
    ("x-content-type-options",       "x_content_type", 80),
    ("x-xss-protection",             "x_xss",         120),
    ("referrer-policy",              "referrer",      120),
    ("permissions-policy",           "permissions",   500),
    ("feature-policy",               "feature",       500),
    ("via",                          "via",           300),
    ("cf-ray",                       "cf_ray",        120),
    ("cf-cache-status",              "cf_cache",       80),
    ("x-served-by",                  "x_served_by",   200),
    ("x-cache",                      "x_cache",        80),
    ("x-amz-cf-id",                  "aws_cf_id",     200),
    ("x-akamai-transformed",         "akamai",        200),
    ("x-github-request-id",          "github_req",    200),
    ("x-vercel-id",                  "vercel_id",     200),
    ("x-vercel-cache",               "vercel_cache",   80),
    ("x-nf-request-id",              "netlify_id",    200),
    ("x-drupal-cache",               "drupal_cache",   80),
    ("x-drupal-dynamic-cache",       "drupal_dynamic", 80),
    ("x-backend-server",             "backend",       200),
    ("x-pingback",                   "pingback",      300),
]


def extract_http_headers(headers: dict[str, str] | None) -> list[dict]:
    """Return a list of finding dicts extracted from a response headers dict.

    The dict is expected to already have x-archive-orig- prefixes stripped, so
    keys are real header names like ``server``, ``x-powered-by``, etc. Values
    may be arbitrary strings.

    Each finding dict has keys:
      - ``type``:   short label (e.g. ``"server"`` or ``"csp"``)
      - ``header``: the actual HTTP header name (e.g. ``"Server"``)
      - ``value``:  the (truncated) header value
    """
    if not headers:
        return []

    # Normalize to lowercase-key dict for case-insensitive lookup
    lower: dict[str, str] = {}
    for k, v in headers.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        lk = k.lower()
        if lk and v:
            lower[lk] = v

    results: list[dict] = []

    # Tracked direct headers
    for header_name, label, trunc in _TRACKED_HEADERS:
        val = lower.get(header_name)
        if not val:
            continue
        val = val.strip()
        if not val:
            continue
        results.append({
            "type": label,
            "header": header_name,
            "value": val[:trunc],
        })

    # Cookies. capture cookie NAMES only, never values
    set_cookie = lower.get("set-cookie")
    if set_cookie:
        # aiohttp joins multiple Set-Cookie into a comma-separated list, but
        # cookies can themselves contain commas (in Expires), so we split on
        # name= boundaries heuristically instead.
        cookie_names = _parse_cookie_names(set_cookie)
        for name in cookie_names:
            results.append({
                "type": "cookie_name",
                "header": "set-cookie",
                "value": name,
            })

    return results


def _parse_cookie_names(raw: str) -> list[str]:
    """Extract distinct cookie names from a Set-Cookie header string.

    Handles the multi-cookie case where aiohttp joins values with commas. We
    look for `<name>=` patterns at the start or after a `, <token>=` boundary.
    """
    names: set[str] = set()
    # Split on comma only when followed by a name-like token and an =
    # e.g. "foo=bar; Path=/, session=abc; HttpOnly" → ["foo=bar; Path=/", "session=abc; HttpOnly"]
    parts: list[str] = []
    buf = raw
    while buf:
        # Find the next ", name=" separator
        idx = -1
        search_from = 0
        while True:
            pos = buf.find(",", search_from)
            if pos == -1:
                break
            rest = buf[pos + 1:].lstrip()
            eq = rest.find("=")
            semi = rest.find(";")
            # Accept as a separator only if what follows looks like "token="
            if 0 < eq < 64 and (semi == -1 or eq < semi):
                token = rest[:eq]
                if _is_valid_cookie_name(token):
                    idx = pos
                    break
            search_from = pos + 1
        if idx == -1:
            parts.append(buf.strip())
            break
        parts.append(buf[:idx].strip())
        buf = buf[idx + 1:].lstrip()

    for part in parts:
        eq = part.find("=")
        if eq <= 0:
            continue
        name = part[:eq].strip()
        if _is_valid_cookie_name(name):
            names.add(name)

    return sorted(names)


def _is_valid_cookie_name(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    # RFC 6265: cookie names are tokens (ALPHA / DIGIT / "!#$%&'*+-.^_`|~")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'*+-.^_`|~")
    return all(c in allowed for c in name)
