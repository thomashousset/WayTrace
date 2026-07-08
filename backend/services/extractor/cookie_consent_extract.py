"""Cookie-consent / CMP (consent management platform) detector.

Detects the presence of common CMPs by script URL, class hooks, or known
data-attributes. When a distinguishing account identifier is visible in the
page we capture it as ``account_id``.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from .helpers import update_entity


# Pattern table: platform -> list of compiled regexes to scan raw HTML.
_CMP_PATTERNS: dict[str, list[re.Pattern]] = {
    "iubenda": [
        re.compile(r"cdn\.iubenda\.com/cs/iubenda_cs\.js", re.IGNORECASE),
        re.compile(r"iubenda\.com/privacy-policy/(\d+)", re.IGNORECASE),
    ],
    "tarteaucitron": [
        re.compile(r"tarteaucitron(?:\.min)?\.js", re.IGNORECASE),
        re.compile(r"class=['\"][^'\"]*tarteaucitron", re.IGNORECASE),
    ],
    "onetrust": [
        re.compile(r"cdn\.cookielaw\.org", re.IGNORECASE),
        re.compile(r"otSDKStub\.js", re.IGNORECASE),
        re.compile(r"id=['\"]otnotice", re.IGNORECASE),
    ],
    "cookieyes": [
        re.compile(r"cdn-cookieyes\.com|cdn\.cookieyes\.com", re.IGNORECASE),
        re.compile(r"cky-consent-container", re.IGNORECASE),
    ],
    "cookiebot": [
        re.compile(r"consent\.cookiebot\.com", re.IGNORECASE),
        re.compile(r"data-cbid=['\"]([A-Za-z0-9-]+)['\"]", re.IGNORECASE),
    ],
    "axeptio": [
        re.compile(r"static\.axept\.io", re.IGNORECASE),
        re.compile(r"acNamespace", re.IGNORECASE),
    ],
    "didomi": [
        re.compile(r"sdk\.privacy-center\.org", re.IGNORECASE),
        re.compile(r"didomi-host", re.IGNORECASE),
    ],
    "trustarc": [
        re.compile(r"consent\.trustarc\.com", re.IGNORECASE),
        # Real TrustArc hooks are truste_overlay / truste-consent / truste_box;
        # require a separator so "trusted-reviews-badge" doesn't match.
        re.compile(r"class=['\"][^'\"]*truste[_\-]", re.IGNORECASE),
    ],
    "usercentrics": [
        # data-settings-id alone is a generic attribute many widgets use, so it
        # is not a detection signal on its own (only the usercentrics host is).
        # It is still used to read the account id once usercentrics is detected
        # (see _ACCOUNT_ID_PATTERNS).
        re.compile(r"app\.usercentrics\.eu", re.IGNORECASE),
    ],
    "termly": [
        re.compile(r"app\.termly\.io", re.IGNORECASE),
    ],
}


# Account-id capturing patterns per platform. Best-effort, may stay empty.
_ACCOUNT_ID_PATTERNS: dict[str, re.Pattern] = {
    "iubenda": re.compile(r"iubenda\.com/privacy-policy/(\d+)"),
    "cookiebot": re.compile(r"data-cbid=['\"]([A-Za-z0-9-]+)['\"]", re.IGNORECASE),
    "usercentrics": re.compile(
        r"data-settings-id=['\"]([A-Za-z0-9_\-]+)['\"]", re.IGNORECASE
    ),
    "onetrust": re.compile(
        r"data-domain-script=['\"]([A-Za-z0-9\-]+)['\"]", re.IGNORECASE
    ),
}


def _pivot_for(platform: str, account_id: str) -> str:
    if platform == "iubenda" and account_id:
        return f"https://www.iubenda.com/privacy-policy/{account_id}"
    if platform == "cookiebot" and account_id:
        return f"https://www.cookiebot.com/en/manager/?cbid={account_id}"
    if platform == "onetrust":
        return "https://app.onetrust.com/"
    if platform == "usercentrics" and account_id:
        return f"https://admin.usercentrics.com/#/settings/{account_id}"
    portals = {
        "iubenda": "https://www.iubenda.com/",
        "tarteaucitron": "https://tarteaucitron.io/",
        "cookieyes": "https://www.cookieyes.com/",
        "axeptio": "https://www.axeptio.eu/",
        "didomi": "https://www.didomi.io/",
        "trustarc": "https://www.trustarc.com/",
        "termly": "https://termly.io/",
        "cookiebot": "https://www.cookiebot.com/",
        "usercentrics": "https://usercentrics.com/",
    }
    return portals.get(platform, "")


def extract_cookie_consent(
    tree: HTMLParser, raw_text: str, month: str, accum: dict
) -> None:
    """Populate ``accum['cookie_consent']`` with detected CMP platforms."""

    for platform, patterns in _CMP_PATTERNS.items():
        if not any(rx.search(raw_text) for rx in patterns):
            continue

        account_id = ""
        id_rx = _ACCOUNT_ID_PATTERNS.get(platform)
        if id_rx is not None:
            m = id_rx.search(raw_text)
            if m:
                account_id = m.group(1)

        key = f"{platform}:{account_id}" if account_id else platform
        update_entity(
            accum["cookie_consent"],
            key,
            month,
            {
                "platform": platform,
                "account_id": account_id,
                "pivot_url": _pivot_for(platform, account_id),
            },
        )
