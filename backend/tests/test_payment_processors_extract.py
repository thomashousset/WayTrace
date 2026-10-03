"""Tests for the payment_processors extractor (checkout stacks + merchant ids)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
    return extract_all(pages, "example.com")["payment_processors"]


def _find(items: list[dict], processor: str) -> dict | None:
    return next((it for it in items if it["processor"] == processor), None)


# ---------------- Positive ----------------

def test_shopify_store_number():
    html = '<img src="https://cdn.shopify.com/s/files/1/0234/5678/products/tee.jpg">'
    e = _find(_run(html), "shopify")
    assert e is not None
    assert e["merchant_id"] == "0234/5678"


def test_shopify_myshopify_handle():
    html = '<link href="https://acme-store.myshopify.com/cart">'
    e = _find(_run(html), "shopify")
    assert e is not None
    assert e["merchant_id"] == "acme-store"
    assert e["pivot_url"] == "https://acme-store.myshopify.com/"


def test_paypal_hosted_button_id():
    html = '<form action="https://www.paypal.com/cgi-bin/webscr">' \
           '<input type="hidden" name="hosted_button_id" value="ABCD1234EFGH5">'
    e = _find(_run(html), "paypal")
    assert e is not None
    assert e["merchant_id"] == "ABCD1234EFGH5"


def test_paddle_vendor_id():
    html = '<script>Paddle.Setup({ vendor: 12345 });</script>' \
           '<script src="https://cdn.paddle.com/paddle/paddle.js"></script>'
    e = _find(_run(html), "paddle")
    assert e is not None
    assert e["merchant_id"] == "12345"


def test_gumroad_creator_subdomain():
    html = '<a href="https://janedoe.gumroad.com/l/course">Buy</a>'
    e = _find(_run(html), "gumroad")
    assert e is not None
    assert e["merchant_id"] == "janedoe"


def test_snipcart_public_key():
    html = '<div id="snipcart" data-api-key="NmM4ZjE2YjAtYWJjZC00ZWZnLWhpams">' \
           '</div><script src="https://cdn.snipcart.com/themes/v3/snipcart.js"></script>'
    e = _find(_run(html), "snipcart")
    assert e is not None
    assert e["merchant_id"] == "NmM4ZjE2YjAtYWJjZC00ZWZnLWhpams"


def test_lemonsqueezy_store():
    html = '<a href="https://acmeapps.lemonsqueezy.com/checkout">buy</a>'
    e = _find(_run(html), "lemonsqueezy")
    assert e is not None
    assert e["merchant_id"] == "acmeapps"


def test_square_application_id():
    html = '<script>const appId = "sq0idp-AbCdEf012345GhIjKlMnop";</script>'
    e = _find(_run(html), "square")
    assert e is not None
    assert e["merchant_id"] == "sq0idp-AbCdEf012345GhIjKlMnop"


# ---------------- Negative ----------------

def test_shopify_cdn_asset_without_store_path_no_false_id():
    html = '<script src="https://cdn.shopify.com/shopifycloud/checkout.js"></script>'
    e = _find(_run(html), "shopify")
    assert e is not None
    assert e["merchant_id"] == ""


def test_bare_hosted_button_word_ignored():
    html = '<p>Click the hosted_button below.</p>'
    assert _run(html) == []


def test_prose_paypal_mention_ignored():
    html = '<p>We accept PayPal and credit cards at checkout.</p>'
    assert _run(html) == []


def test_gumroad_www_not_merchant():
    html = '<a href="https://www.gumroad.com/pricing">pricing</a>'
    e = _find(_run(html), "gumroad")
    assert e is not None
    assert e["merchant_id"] == ""


def test_word_square_ignored():
    html = '<p>The town square hosts a market. Times Square is busy.</p>'
    assert _run(html) == []


def test_paddle_word_in_prose_ignored():
    html = '<p>Grab a paddle and hit the water. We paddle every weekend.</p>'
    assert _run(html) == []
