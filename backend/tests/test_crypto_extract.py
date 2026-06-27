"""Tests for cryptocurrency address extraction."""
from __future__ import annotations

import pytest

from services.extractor.crypto_extract import extract_crypto_addresses


# ---------------------------------------------------------------------------
# BTC legacy
# ---------------------------------------------------------------------------

def test_btc_legacy_p2pkh():
    html = "Donate to 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa please"
    results = extract_crypto_addresses(html)
    assert len(results) == 1
    assert results[0]["type"] == "btc"
    assert results[0]["address"] == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def test_btc_legacy_p2sh():
    html = "Send to 3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"
    results = extract_crypto_addresses(html)
    assert len(results) == 1
    assert results[0]["type"] == "btc"
    assert results[0]["address"] == "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"


# ---------------------------------------------------------------------------
# BTC bech32
# ---------------------------------------------------------------------------

def test_btc_bech32():
    html = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    results = extract_crypto_addresses(html)
    assert len(results) == 1
    assert results[0]["type"] == "btc"
    assert results[0]["address"] == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


# ---------------------------------------------------------------------------
# ETH
# ---------------------------------------------------------------------------

def test_eth_address():
    # Properly EIP-55-encoded version of the Binance hot wallet address.
    addr = "0x742d35CC6634c0532925a3B844bc9e7595F2Bd28"
    html = f"ETH wallet: {addr}"
    results = extract_crypto_addresses(html)
    assert len(results) == 1
    assert results[0]["type"] == "eth"
    assert results[0]["address"] == addr
    assert results[0]["validation_method"] == "eip55"


def test_eth_all_lowercase_accepted():
    # All-lowercase form: no checksum claimed, accepted as length-only valid
    addr = "0x742d35cc6634c0532925a3b844bc9e7595f2bd28"
    html = f"ETH: {addr}"
    results = extract_crypto_addresses(html)
    assert len(results) == 1
    assert results[0]["address"] == addr


# ---------------------------------------------------------------------------
# XMR
# ---------------------------------------------------------------------------

def test_xmr_address():
    html = "XMR: 4AdUndXHHZ6cfufTMvppY6JwXNouMBzSkbLYfpAV5Usx3skQNBjjEhPAgiLaGECdRaoLLbcp2anbdRAS2TMAByNX9u8hSGF"
    results = extract_crypto_addresses(html)
    assert len(results) == 1
    assert results[0]["type"] == "xmr"
    assert results[0]["address"] == "4AdUndXHHZ6cfufTMvppY6JwXNouMBzSkbLYfpAV5Usx3skQNBjjEhPAgiLaGECdRaoLLbcp2anbdRAS2TMAByNX9u8hSGF"


# ---------------------------------------------------------------------------
# Multiple addresses on same page
# ---------------------------------------------------------------------------

def test_multiple_addresses():
    html = (
        "BTC: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa "
        "ETH: 0x742d35CC6634c0532925a3B844bc9e7595F2Bd28"
    )
    results = extract_crypto_addresses(html)
    types = {r["type"] for r in results}
    assert "btc" in types
    assert "eth" in types
    assert len(results) == 2


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_dedup_same_address():
    addr = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    html = f"{addr} and again {addr}"
    results = extract_crypto_addresses(html)
    assert len(results) == 1
    assert results[0]["address"] == addr


# ---------------------------------------------------------------------------
# Address in href attribute
# ---------------------------------------------------------------------------

def test_btc_in_url():
    html = '<a href="bitcoin:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa">Pay</a>'
    results = extract_crypto_addresses(html)
    assert any(r["address"] == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa" for r in results)


# ---------------------------------------------------------------------------
# False-positive tests
# ---------------------------------------------------------------------------

def test_skip_short_hex():
    # Only 16 hex chars after 0x - not 40, should not match ETH
    html = "value=0x742d35Cc6634C053"
    results = extract_crypto_addresses(html)
    assert results == []


def test_skip_css_hex_color():
    # CSS color starts with # not 0x - should not match
    html = "color: #742d35Cc6634C0532925a3b844Bc9e7595f2bD28;"
    results = extract_crypto_addresses(html)
    assert results == []


def test_no_match_plain_text():
    html = "Nothing cryptographic here. Just regular text about finance."
    results = extract_crypto_addresses(html)
    assert results == []


def test_skip_too_short_btc():
    # Only 17 chars total (1 + 16) - below the 26-char minimum (1 + 25)
    html = "address: 1A1zP1eP5QGefi2DM end"
    results = extract_crypto_addresses(html)
    assert results == []
