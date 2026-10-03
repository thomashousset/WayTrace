"""Tests for the email_marketing extractor (martech embeds + account ids)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
    return extract_all(pages, "example.com")["email_marketing"]


def _find(items: list[dict], platform: str) -> dict | None:
    return next((it for it in items if it["platform"] == platform), None)


# ---------------- Positive ----------------

def test_mailchimp_audience_id():
    html = '<form action="https://acme.us5.list-manage.com/subscribe/post?u=abcdef0123456789abcdef01&amp;id=deadbeef">'
    e = _find(_run(html), "mailchimp")
    assert e is not None
    assert e["account_id"] == "abcdef0123456789abcdef01"


def test_hubspot_portal_id():
    html = '<script src="https://js.hs-scripts.com/1234567.js"></script>'
    e = _find(_run(html), "hubspot")
    assert e is not None
    assert e["account_id"] == "1234567"
    assert "1234567" in e["pivot_url"]


def test_marketo_munchkin_id():
    html = '<script>Munchkin.init("123-ABC-456");</script>'
    e = _find(_run(html), "marketo")
    assert e is not None
    assert e["account_id"] == "123-ABC-456"


def test_klaviyo_company_id():
    html = '<script src="https://static.klaviyo.com/onsite/js/AbC123/klaviyo.js"></script>'
    e = _find(_run(html), "klaviyo")
    assert e is not None
    assert e["account_id"] == "AbC123"


def test_pardot_account_id():
    html = '<script>piAId = "123456"; piCId = "1234"; ' \
           '(function(){var s=document.createElement("script");s.src="https://pi.pardot.com/pd.js";})();</script>'
    e = _find(_run(html), "pardot")
    assert e is not None
    assert e["account_id"] == "123456"


def test_activecampaign_subdomain():
    html = '<form action="https://acmegroup.activehosted.com/proc.php" method="POST"></form>'
    e = _find(_run(html), "activecampaign")
    assert e is not None
    assert e["account_id"] == "acmegroup"


def test_brevo_serve_id():
    html = '<script src="https://sibforms.com/serve/MUIFAbc123_XYZ"></script>'
    e = _find(_run(html), "brevo")
    assert e is not None
    assert e["account_id"] == "MUIFAbc123_XYZ"


def test_detected_without_id():
    html = '<script src="https://js.hsforms.net/forms/embed/v2.js"></script>'
    e = _find(_run(html), "hubspot")
    assert e is not None
    assert e["account_id"] == ""


# ---------------- Negative ----------------

def test_plain_word_munchkin_ignored():
    html = '<p>The Munchkin cat breed is small. Munchkin Level 9 board game.</p>'
    assert _run(html) == []


def test_bare_piaid_word_ignored():
    html = '<p>Our piAId process is documented internally.</p>'
    assert _run(html) == []


def test_activecampaign_www_not_tenant():
    html = '<a href="https://www.activehosted.com/features">features</a>'
    e = _find(_run(html), "activecampaign")
    assert e is not None
    assert e["account_id"] == ""


def test_unrelated_form_ignored():
    html = '<form action="https://example.com/subscribe?u=abcdef0123456789abcdef01"></form>'
    assert _run(html) == []


def test_mailchimp_without_u_param_no_false_id():
    # list-manage host present (real integration) but no u= param to read.
    html = '<script src="https://acme.us1.list-manage.com/generate-js/?aid=x"></script>'
    e = _find(_run(html), "mailchimp")
    assert e is not None
    assert e["account_id"] == ""


def test_prose_mentioning_marketing_ignored():
    html = '<p>We do email marketing and newsletters.</p>'
    assert _run(html) == []
