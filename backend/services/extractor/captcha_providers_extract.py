"""Detect CAPTCHA providers and their site keys.

Looks for reCAPTCHA, Cloudflare Turnstile and hCaptcha. Site keys are
public anyway, but storing them lets us cross-correlate sites that share
an account.
"""
from __future__ import annotations

import re

from .helpers import update_entity
from .patterns import (
    ARKOSE_PUBKEY_RE,
    FRIENDLY_CAPTCHA_SITEKEY_RE,
    GEETEST_ID_RE,
    HCAPTCHA_UUID_RE,
    RECAPTCHA_SITEKEY_RE,
    TURNSTILE_SITEKEY_RE,
)


# Accept "hcaptcha" or "h-captcha" (the CSS class form).
_HCAPTCHA_KEYWORD_RE = re.compile(r"h[-_]?captcha", re.IGNORECASE)
_HCAPTCHA_WINDOW = 80

# Context keywords for the bare-token providers (GeeTest 32-hex id, Friendly
# Captcha sitekey). Same precaution as hCaptcha: a bare token is only accepted
# when its provider name appears within the preceding window.
_GEETEST_KEYWORD_RE = re.compile(r"geetest|initgeetest|\bgt\b", re.IGNORECASE)
_FRIENDLY_KEYWORD_RE = re.compile(r"friendly\s*captcha|friendlycaptcha|frc-captcha", re.IGNORECASE)
_CTX_WINDOW = 80


def _emit(accum: dict, provider: str, sitekey: str, month: str) -> None:
    key = f"{provider}:{sitekey}"
    update_entity(
        accum["captcha_providers"],
        key,
        month,
        {
            "provider": provider,
            "sitekey": sitekey,
            "pivot_url": "",
        },
    )


# Script URLs that confirm a provider is loaded even when the site key
# is set later from JavaScript.
_PROVIDER_SCRIPT_PATTERNS = (
    (re.compile(r"https?://(?:www\.)?google\.com/recaptcha/(?:api|enterprise)\.js", re.IGNORECASE), "recaptcha"),
    (re.compile(r"https?://(?:js|api)\.hcaptcha\.com/1/api\.js", re.IGNORECASE), "hcaptcha"),
    (re.compile(r"https?://challenges\.cloudflare\.com/turnstile/v0/api\.js", re.IGNORECASE), "turnstile"),
    (re.compile(r"https?://(?:www\.)?google\.com/recaptcha/enterprise/", re.IGNORECASE), "recaptcha-enterprise"),
    (re.compile(r"\b(?:[a-z0-9.\-]+\.)?arkoselabs\.com/|\bfuncaptcha\.com/", re.IGNORECASE), "arkose"),
    (re.compile(r"\b(?:static|api|gcaptcha4)\.geetest\.com/", re.IGNORECASE), "geetest"),
    (re.compile(r"\b[a-z0-9.\-]+\.(?:token|captcha)\.awswaf\.com/", re.IGNORECASE), "aws_waf"),
    (re.compile(r"\bfriendlycaptcha\.com/|\bfriendly-challenge\b", re.IGNORECASE), "friendly_captcha"),
)


def extract_captcha_providers(
    raw_text: str, month: str, accum: dict
) -> None:
    """Populate ``accum['captcha_providers']`` with detected providers."""
    for m in RECAPTCHA_SITEKEY_RE.finditer(raw_text):
        _emit(accum, "recaptcha", m.group(0), month)

    for m in TURNSTILE_SITEKEY_RE.finditer(raw_text):
        _emit(accum, "turnstile", m.group(0), month)

    # hCaptcha keys are bare UUIDs, so require an "hcaptcha" hint within
    # the preceding 80 characters to avoid matching unrelated UUIDs.
    for m in HCAPTCHA_UUID_RE.finditer(raw_text):
        start = max(0, m.start() - _HCAPTCHA_WINDOW)
        if _HCAPTCHA_KEYWORD_RE.search(raw_text[start:m.start()]):
            _emit(accum, "hcaptcha", m.group(0), month)

    # Arkose Labs / FunCaptcha: public key sits inside the script URL, so
    # it's unambiguous and needs no context window.
    for m in ARKOSE_PUBKEY_RE.finditer(raw_text):
        _emit(accum, "arkose", m.group(1), month)

    # GeeTest id (32 hex) and Friendly Captcha sitekey (FC...) are bare
    # tokens; accept only with the provider keyword in the preceding window.
    for m in GEETEST_ID_RE.finditer(raw_text):
        start = max(0, m.start() - _CTX_WINDOW)
        if _GEETEST_KEYWORD_RE.search(raw_text[start:m.start()]):
            _emit(accum, "geetest", m.group(0), month)

    for m in FRIENDLY_CAPTCHA_SITEKEY_RE.finditer(raw_text):
        start = max(0, m.start() - _CTX_WINDOW)
        if _FRIENDLY_KEYWORD_RE.search(raw_text[start:m.start()]):
            _emit(accum, "friendly_captcha", m.group(0), month)

    # If we already have a keyed entry for a provider, skip the script
    # fallback. Otherwise record a sitekey-less entry so the provider
    # itself is still surfaced.
    seen_providers = {f.get("provider") for f in accum["captcha_providers"].values()}
    for pattern, provider in _PROVIDER_SCRIPT_PATTERNS:
        if pattern.search(raw_text):
            if provider in seen_providers:
                continue
            key = f"{provider}:_script_only"
            update_entity(
                accum["captcha_providers"],
                key,
                month,
                {
                    "provider": provider,
                    "sitekey": "",
                    "pivot_url": "",
                },
            )
            seen_providers.add(provider)
