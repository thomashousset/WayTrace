"""PGP / GPG public-key material extractor.

Surfaces handled:
  1. Armored key blocks (PUBLIC and PRIVATE).
  2. Human-printed fingerprints (40 hex: contiguous, grouped in 4s or 8s).
  3. Key IDs (0x + 8 or 16 hex), only when PGP context is nearby.
  4. Keyserver references (keys.openpgp.org, /pks/lookup, ...).
  5. keybase.io/<username> references.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from .helpers import update_entity

_PGP_BLOCK_RE = re.compile(
    r"-----BEGIN PGP (PUBLIC|PRIVATE) KEY BLOCK-----(.*?)-----END PGP \1 KEY BLOCK-----",
    re.DOTALL,
)

# A fingerprint is 40 hex: as one run, or grouped 10x4 / 5x8 with any run of
# whitespace between groups (covers the double-space gpg prints in the middle).
_WS = "[ \t ]+"
_FP_CONTIGUOUS_RE = re.compile(r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{40}(?![A-Fa-f0-9])")
_FP_GROUPED4_RE = re.compile(
    r"(?<![A-Fa-f0-9])(?:[A-Fa-f0-9]{4}" + _WS + r"){9}[A-Fa-f0-9]{4}(?![A-Fa-f0-9])"
)
_FP_GROUPED8_RE = re.compile(
    r"(?<![A-Fa-f0-9])(?:[A-Fa-f0-9]{8}" + _WS + r"){4}[A-Fa-f0-9]{8}(?![A-Fa-f0-9])"
)

# Key IDs: long (64-bit, 16 hex) and short (32-bit, 8 hex). Gated by context.
_KEYID_RE = re.compile(r"0x([A-Fa-f0-9]{16}|[A-Fa-f0-9]{8})(?![A-Fa-f0-9])")

# Keyserver references: known hosts, or any /pks/lookup endpoint.
_KEYSERVER_RE = re.compile(
    r"https?://(?:[a-z0-9.\-]*\.)?"
    r"(?:keys\.openpgp\.org|pgp\.mit\.edu|keyserver\.ubuntu\.com|keys\.gnupg\.net|"
    r"pgp\.surf(?:net)?\.nl|sks-keyservers\.net|pgpkeys\.eu|keys\.mailvelope\.com)"
    r"[^\s\"'<>]*"
    r"|https?://[^\s\"'<>]*/pks/lookup[^\s\"'<>]*",
    re.IGNORECASE,
)

_KEYBASE_RE = re.compile(r"keybase\.io/([a-z0-9_]{2,40})", re.IGNORECASE)

# Placeholder fingerprints (all-zero, all-F, repeated patterns) get dropped.
_PLACEHOLDER_HEX = {"0" * 40, "f" * 40, "deadbeef" * 5}

# Reserved keybase path slugs that are not user accounts.
_KEYBASE_RESERVED = {
    "docs", "blog", "legal", "privacy", "about", "download",
    "popular-teams", "pricing", "_", "team", "kb",
}

# Context windows (chars) scanned around a match for keyword gating.
_CTX = 48
_POS_KW = (
    "pgp", "gpg", "openpgp", "gnupg", "fingerprint", "keyid", "key id",
    "public key", "keyserver", "keybase",
)
# A contiguous 40-hex run is also the shape of a SHA-1 / git object id; skip
# it when the surrounding text screams "hash" rather than "key".
_NEG_KW = (
    "sha", "commit", "git ", " git", "checksum", "integrity", "digest",
    "etag", "subresource", "sri-", "content-hash", "blob",
)


def _clean_fp(raw: str) -> str:
    return re.sub(r"\s+", "", raw).upper()


def _pivot_fingerprint(fp: str) -> str:
    return f"https://keys.openpgp.org/search?q=0x{fp[:16]}"


def _window(low: str, start: int, end: int) -> str:
    return low[max(0, start - _CTX):end + _CTX]


def _is_real_fp(fp: str) -> bool:
    if fp.lower() in _PLACEHOLDER_HEX:
        return False
    # Reject clearly patterned strings (e.g. ABABAB...) with too few symbols.
    return len(set(fp)) >= 4


def extract_pgp_keys(tree: HTMLParser, raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['pgp_keys']`` with detected PGP material."""
    low = raw_text.lower()
    store = accum["pgp_keys"]

    # 1. Armored key blocks (public + private).
    for m in _PGP_BLOCK_RE.finditer(raw_text):
        armor = m.group(1).lower()  # "public" / "private"
        compact = re.sub(r"\s+", "", m.group(2))
        if len(compact) < 20:
            continue
        identifier = compact[:16]
        update_entity(
            store, f"block:{armor}:{identifier}", month,
            {
                "kind": "block",
                "armor": armor,
                "identifier": identifier,
                "pivot_url": "",
            },
        )

    # 2. Fingerprints (contiguous + grouped). Dedupe per page by normalized fp.
    seen_fp: set[str] = set()
    for rx in (_FP_GROUPED4_RE, _FP_GROUPED8_RE, _FP_CONTIGUOUS_RE):
        contiguous = rx is _FP_CONTIGUOUS_RE
        for m in rx.finditer(raw_text):
            fp = _clean_fp(m.group(0))
            if fp in seen_fp or not _is_real_fp(fp):
                continue
            # Contiguous 40-hex runs are also the shape of SHA-1 / git object
            # ids / asset (SRI) hashes, which vastly outnumber real PGP
            # fingerprints in archived HTML. Drop on explicit hash context AND
            # require explicit PGP context to keep one (grouped fingerprints,
            # handled by the other regexes, stay context-free since only PGP
            # fingerprints are ever printed in 4- or 8-char groups).
            if contiguous:
                win = _window(low, m.start(), m.end())
                if any(k in win for k in _NEG_KW):
                    continue
                if not any(k in win for k in _POS_KW):
                    continue
            seen_fp.add(fp)
            update_entity(
                store, f"fingerprint:{fp}", month,
                {
                    "kind": "fingerprint",
                    "identifier": fp[:16],
                    "pivot_url": _pivot_fingerprint(fp),
                },
            )

    # 3. Key IDs (0x...), only with PGP context nearby (else far too noisy).
    for m in _KEYID_RE.finditer(raw_text):
        if not any(k in _window(low, m.start(), m.end()) for k in _POS_KW):
            continue
        kid = m.group(1).upper()
        if kid.lower() in {"0" * len(kid), "f" * len(kid)} or len(set(kid)) < 3:
            continue
        update_entity(
            store, f"keyid:0x{kid}", month,
            {
                "kind": "keyid",
                "identifier": f"0x{kid}",
                "pivot_url": f"https://keys.openpgp.org/search?q=0x{kid}",
            },
        )

    # 4. Keyserver references.
    for m in _KEYSERVER_RE.finditer(raw_text):
        url = m.group(0).rstrip(".,);\"'")
        update_entity(
            store, f"keyserver:{url.lower()}", month,
            {"kind": "keyserver", "identifier": url, "pivot_url": url},
        )

    # 5. Keybase username references.
    for m in _KEYBASE_RE.finditer(raw_text):
        username = m.group(1).lower()
        if username in _KEYBASE_RESERVED:
            continue
        update_entity(
            store, f"keybase:{username}", month,
            {
                "kind": "keybase",
                "identifier": username,
                "pivot_url": f"https://keybase.io/{username}",
            },
        )
