"""Cryptocurrency address extraction with strict checksum validation.

Naive regex on hex strings yields a flood of false positives. MD5 hashes,
UUIDs, commit SHAs, wp-rest nonces all happen to match the legacy Base58
character class. Every candidate produced by the patterns in
``patterns.py`` MUST pass through a chain-specific validator before being
emitted, and every result carries ``validated`` + ``validation_method``
metadata so downstream consumers can filter low-confidence entries.

Validators implemented inline (no new deps):
  - Base58Check  (BIP-13)            : BTC P2PKH/P2SH, LTC, DOGE, TRX
  - bech32 / bech32m  (BIP-173/350)  : BTC SegWit (bc1...), LTC (ltc1...)
  - EIP-55 mixed-case checksum       : Ethereum (0x...)
  - length + context keyword         : Solana (no on-chain checksum)
  - XRP base58 + ripple alphabet     : XRP

Context filtering: candidates that occur inside <script>, <style>, <svg>,
inside ``data-*`` attributes, or within ~60 chars of identifiers like
``sha256``, ``md5``, ``nonce``, ``hash``, ``uuid``, ``id=``, ``commit``,
``etag`` are rejected.
"""
from __future__ import annotations

import hashlib
import re

from loguru import logger
from selectolax.parser import HTMLParser

from .patterns import (
    BTC_LEGACY_RE,
    BTC_BECH32_RE,
    DOGE_RE,
    ETH_RE,
    LTC_BECH32_RE,
    LTC_LEGACY_RE,
    SOL_RE,
    TRX_RE,
    XMR_RE,
    XRP_RE,
)

# ---------------------------------------------------------------------------
# Base58Check
# ---------------------------------------------------------------------------

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def _base58_decode(s: str) -> bytes | None:
    n = 0
    for c in s:
        v = _B58_INDEX.get(c)
        if v is None:
            return None
        n = n * 58 + v
    # leading "1" -> leading 0x00 byte
    pad = len(s) - len(s.lstrip("1"))
    raw = bytearray()
    while n > 0:
        raw.append(n & 0xFF)
        n >>= 8
    raw.reverse()
    return b"\x00" * pad + bytes(raw)


def _is_base58check(addr: str, expected_lengths: tuple[int, ...] = (25,),
                    version_bytes: tuple[int, ...] | None = None) -> bool:
    """Validate a Base58Check string.

    ``expected_lengths`` constrains the decoded byte length (legacy BTC = 25:
    1 version + 20 hash + 4 checksum). ``version_bytes`` optionally
    constrains the leading version byte(s).
    """
    raw = _base58_decode(addr)
    if raw is None or len(raw) not in expected_lengths:
        return False
    payload, checksum = raw[:-4], raw[-4:]
    digest = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if digest != checksum:
        return False
    if version_bytes is not None and raw[0] not in version_bytes:
        return False
    return True


# ---------------------------------------------------------------------------
# bech32 / bech32m  (BIP-173 / BIP-350)
# ---------------------------------------------------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (b >> i) & 1:
                chk ^= gen[i]
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _bech32_verify(hrp: str, data: list[int]) -> int | None:
    """Return the spec const (1 = bech32, 0x2BC830A3 = bech32m) or None."""
    pm = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if pm == _BECH32_CONST:
        return _BECH32_CONST
    if pm == _BECH32M_CONST:
        return _BECH32M_CONST
    return None


def _is_segwit_bech32(addr: str, expected_hrps: tuple[str, ...]) -> bool:
    """Validate a SegWit bech32/bech32m address (witness v0..v16)."""
    if any(ord(c) < 33 or ord(c) > 126 for c in addr):
        return False
    if addr.lower() != addr and addr.upper() != addr:
        return False
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        return False
    hrp = addr[:pos]
    if hrp not in expected_hrps:
        return False
    data: list[int] = []
    for c in addr[pos + 1:]:
        if c not in _BECH32_CHARSET:
            return False
        data.append(_BECH32_CHARSET.index(c))
    spec = _bech32_verify(hrp, data)
    if spec is None:
        return False
    # Witness program length / version sanity (BIP-141, BIP-350)
    if len(data) < 6:
        return False
    witver = data[0]
    if witver > 16:
        return False
    # Convert 5-bit groups to 8-bit bytes (BIP-173 reference)
    program = _convertbits(data[1:-6], 5, 8, False)
    if program is None:
        return False
    if len(program) < 2 or len(program) > 40:
        return False
    if witver == 0 and len(program) not in (20, 32):
        return False
    # v0 MUST use bech32; v1+ MUST use bech32m
    if witver == 0 and spec != _BECH32_CONST:
        return False
    if witver != 0 and spec != _BECH32M_CONST:
        return False
    return True


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int] | None:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


# ---------------------------------------------------------------------------
# EIP-55 (Ethereum mixed-case checksum)
# ---------------------------------------------------------------------------

_ETH_SENTINELS = {
    "0x" + "0" * 40,                             # zero address
    "0x" + "f" * 40,                             # all-Fs sentinel
    "0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead",
}


def _is_eth_valid(addr: str) -> tuple[bool, str]:
    """Return (valid, method)."""
    a = addr[2:]
    low = addr.lower()
    if low in _ETH_SENTINELS:
        return False, "sentinel"
    # Reject obvious "0xdead...beef"-style novelty addresses
    if low.startswith("0xdead") and low.endswith("beef"):
        return False, "sentinel"
    # All-lowercase or all-uppercase: no checksum claimed
    if a == a.lower() or a == a.upper():
        return True, "length"
    # Mixed case implies EIP-55. verify
    try:
        # keccak-256 from hashlib via sha3_256? hashlib has sha3_256 (FIPS),
        # which is NOT keccak. Use a tiny pure-Python keccak fallback.
        h = _keccak256(a.lower().encode("ascii")).hex()
    except Exception:
        return False, "eip55"
    for i, c in enumerate(a):
        if c.isalpha():
            want_upper = int(h[i], 16) >= 8
            if want_upper and c != c.upper():
                return False, "eip55"
            if not want_upper and c != c.lower():
                return False, "eip55"
    return True, "eip55"


# Minimal Keccak-256 (NIST.FIPS.202 Keccak-f[1600], rate=1088, capacity=512,
# pad 0x01. the original Keccak padding used by Ethereum, *not* SHA-3's 0x06).
def _keccak256(data: bytes) -> bytes:
    R = 1088 // 8  # 136
    state = bytearray(200)
    # absorb
    offset = 0
    while len(data) - offset >= R:
        for i in range(R):
            state[i] ^= data[offset + i]
        _keccak_f(state)
        offset += R
    # pad
    rem = len(data) - offset
    block = bytearray(R)
    block[:rem] = data[offset:]
    block[rem] ^= 0x01
    block[R - 1] ^= 0x80
    for i in range(R):
        state[i] ^= block[i]
    _keccak_f(state)
    return bytes(state[:32])


_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_KECCAK_R = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _rot(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF


def _keccak_f(state: bytearray) -> None:
    A = [[0] * 5 for _ in range(5)]
    for x in range(5):
        for y in range(5):
            i = 8 * (x + 5 * y)
            A[x][y] = int.from_bytes(state[i:i + 8], "little")
    for r in range(24):
        # theta
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rot(C[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                A[x][y] ^= D[x]
        # rho + pi
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = _rot(A[x][y], _KECCAK_R[x][y])
        # chi
        for x in range(5):
            for y in range(5):
                A[x][y] = B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y])
        # iota
        A[0][0] ^= _KECCAK_RC[r]
    for x in range(5):
        for y in range(5):
            i = 8 * (x + 5 * y)
            state[i:i + 8] = (A[x][y] & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


# ---------------------------------------------------------------------------
# XRP. uses ripple's modified base58 alphabet
# ---------------------------------------------------------------------------

_XRP_ALPHABET = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz"
_XRP_INDEX = {c: i for i, c in enumerate(_XRP_ALPHABET)}


def _is_xrp_valid(addr: str) -> bool:
    n = 0
    for c in addr:
        v = _XRP_INDEX.get(c)
        if v is None:
            return False
        n = n * 58 + v
    pad = len(addr) - len(addr.lstrip("r"))
    raw = bytearray()
    while n > 0:
        raw.append(n & 0xFF)
        n >>= 8
    raw.reverse()
    decoded = b"\x00" * pad + bytes(raw)
    if len(decoded) != 25 or decoded[0] != 0x00:
        return False
    payload, checksum = decoded[:-4], decoded[-4:]
    digest = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return digest == checksum


# ---------------------------------------------------------------------------
# Context filtering
# ---------------------------------------------------------------------------

_CONTEXT_BLOCKLIST = (
    "sha256", "sha-256", "sha1", "sha-1", "sha512",
    "md5", "nonce", "hash", "digest", "uuid", "guid",
    "etag", "commit", "checksum", "id=", "data-",
    "sessionid", "csrf", "xsrf", "_token",
)

# Solana / SPL hints that must occur near a candidate to accept it
_SOL_HINTS = ("solana", "sol ", "spl ", "spl-", "phantom", "solscan", "@solana")

# Monero has no cheap on-chain checksum (full validation needs Keccak), and the
# regex alone (95-char base58 starting 4/8) also matches unrelated base58 blobs.
# Require a nearby keyword, exactly like Solana, so a random base58 string is
# not emitted as a fake XMR address.
_XMR_HINTS = ("monero", "xmr", "wownero", "getmonero")


def _has_blocklisted_context(text: str, idx: int, length: int, window: int = 60) -> bool:
    lo = max(0, idx - window)
    hi = min(len(text), idx + length + window)
    snippet = text[lo:hi].lower()
    return any(kw in snippet for kw in _CONTEXT_BLOCKLIST)


def _has_keyword_nearby(text: str, idx: int, length: int,
                        keywords: tuple[str, ...], window: int = 60) -> bool:
    lo = max(0, idx - window)
    hi = min(len(text), idx + length + window)
    snippet = text[lo:hi].lower()
    return any(kw in snippet for kw in keywords)


def _strip_noise_tags(html: str) -> str:
    """Drop <script>, <style>, <svg>, and ``data-*`` attribute values from
    the HTML before scanning. Done with selectolax to be cheap and safe."""
    try:
        tree = HTMLParser(html)
    except Exception:
        return html
    for tag in ("script", "style", "svg", "noscript", "template"):
        for node in tree.css(tag):
            try:
                node.decompose()
            except Exception:
                pass
    # selectolax doesn't expose a clean attribute-strip primitive; the
    # remaining text() output already drops attribute values, but we want
    # tag attributes gone too. Easiest: serialize back via .html and run a
    # cheap regex to wipe data-* attributes.
    try:
        out = tree.html or ""
    except (UnicodeDecodeError, ValueError) as exc:
        logger.debug("crypto strip noise: tree.html failed ({}), falling back", exc)
        out = html
    out = re.sub(r"\sdata-[a-zA-Z0-9_\-]+\s*=\s*\"[^\"]*\"", " ", out)
    out = re.sub(r"\sdata-[a-zA-Z0-9_\-]+\s*=\s*\'[^\']*\'", " ", out)
    return out


# ---------------------------------------------------------------------------
# Per-chain candidate validators
# ---------------------------------------------------------------------------


def _validate_btc_legacy(addr: str) -> tuple[bool, str]:
    return (_is_base58check(addr, expected_lengths=(25,),
                            version_bytes=(0x00, 0x05)),  # P2PKH=0x00, P2SH=0x05
            "base58check")


def _validate_ltc_legacy(addr: str) -> tuple[bool, str]:
    # LTC P2PKH=0x30 ('L'), P2SH=0x32 ('M') or legacy 0x05 ('3')
    return (_is_base58check(addr, expected_lengths=(25,),
                            version_bytes=(0x30, 0x32, 0x05)),
            "base58check")


def _validate_doge(addr: str) -> tuple[bool, str]:
    # DOGE P2PKH=0x1E
    return (_is_base58check(addr, expected_lengths=(25,),
                            version_bytes=(0x1E,)),
            "base58check")


def _validate_trx(addr: str) -> tuple[bool, str]:
    # Tron mainnet uses Base58Check with version 0x41, total 25 bytes
    return (_is_base58check(addr, expected_lengths=(25,),
                            version_bytes=(0x41,)),
            "base58check")


def _validate_btc_bech32(addr: str) -> tuple[bool, str]:
    return _is_segwit_bech32(addr, ("bc",)), "bech32"


def _validate_ltc_bech32(addr: str) -> tuple[bool, str]:
    return _is_segwit_bech32(addr, ("ltc",)), "bech32"


def _validate_xmr(addr: str) -> tuple[bool, str]:
    # Monero: full validation requires Keccak. accept length+prefix shape
    # only (the regex already enforces both).
    return True, "length"


def _validate_xrp(addr: str) -> tuple[bool, str]:
    return _is_xrp_valid(addr), "base58check"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# (regex, type_label, validator, requires_context_keywords)
_PIPELINE: list[tuple[re.Pattern, str, callable, tuple[str, ...] | None]] = [
    (BTC_LEGACY_RE, "btc", _validate_btc_legacy, None),
    (BTC_BECH32_RE, "btc", _validate_btc_bech32, None),
    (LTC_LEGACY_RE, "ltc", _validate_ltc_legacy, None),
    (LTC_BECH32_RE, "ltc", _validate_ltc_bech32, None),
    (DOGE_RE, "doge", _validate_doge, None),
    (TRX_RE, "trx", _validate_trx, None),
    (XMR_RE, "xmr", _validate_xmr, _XMR_HINTS),
    (XRP_RE, "xrp", _validate_xrp, None),
    # Solana has no on-chain checksum: require contextual keyword
    (SOL_RE, "sol", lambda _addr: (True, "length+context"), _SOL_HINTS),
]


def extract_crypto_addresses(html: str) -> list[dict]:
    """Extract validated crypto addresses from HTML.

    Returns a list of dicts:
        { "type": "btc"|"eth"|...,
          "address": "<addr>",
          "validated": True,
          "validation_method": "base58check"|"bech32"|"eip55"|"length"|"length+context" }
    """
    cleaned = _strip_noise_tags(html)
    results: list[dict] = []
    seen: set[str] = set()

    # ETH gets its own loop because it has its own validator with sentinel
    # filtering and EIP-55 mixed-case logic.
    for m in ETH_RE.finditer(cleaned):
        addr = m.group(0)
        if addr in seen:
            continue
        if _has_blocklisted_context(cleaned, m.start(), len(addr)):
            continue
        ok, method = _is_eth_valid(addr)
        if not ok:
            continue
        seen.add(addr)
        results.append({
            "type": "eth",
            "address": addr,
            "validated": True,
            "validation_method": method,
        })

    for pattern, label, validator, ctx_keywords in _PIPELINE:
        for m in pattern.finditer(cleaned):
            addr = m.group(0)
            if addr in seen:
                continue
            if _has_blocklisted_context(cleaned, m.start(), len(addr)):
                continue
            if ctx_keywords is not None and not _has_keyword_nearby(
                cleaned, m.start(), len(addr), ctx_keywords
            ):
                continue
            try:
                ok, method = validator(addr)
            except Exception as exc:
                logger.debug("crypto validator error for {}: {}", label, exc)
                continue
            if not ok:
                continue
            # Solana: ensure it doesn't collide with an already-validated BTC/etc.
            seen.add(addr)
            results.append({
                "type": label,
                "address": addr,
                "validated": True,
                "validation_method": method,
            })

    return results
