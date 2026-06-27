"""Tests for the french_business_ids extractor."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{
        "html": html,
        "url": "https://example.com/",
        "timestamp": "20220601120000",
    }]
    return extract_all(pages, "example.com")["french_business_ids"]


def _types(items: list[dict]) -> set[str]:
    return {it["type"] for it in items}


# All SIREN/SIRET values below pass the INSEE Luhn checksum and are not
# placeholder-shaped (>2 distinct digits, no short-period tiling).
#   552081317, 542051180, 732829320, 444608442  -> valid SIREN
#   54205118000066, 73282932000074              -> valid SIRET


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_siren_with_french_separators():
    # French group separators qualify without a *near* keyword, but the page
    # must contain some French legal vocabulary (real SIRENs always do).
    html = "<footer>Mentions légales - raison sociale</footer><p>552 081 317</p>"
    items = _run(html)
    assert any(it["type"] == "siren" and it["value"] == "552081317" for it in items)


def test_ignores_separated_run_on_non_french_page():
    # A 3/3/3-grouped Luhn-passing number on an English page (a reference /
    # order number) is not a SIREN: no French legal term anywhere.
    html = "<p>Reference number 325 437 259 ships worldwide.</p>"
    items = _run(html)
    assert all(it["type"] not in ("siren", "siret") for it in items)


def test_detects_siren_bare_with_french_context_keyword():
    # Bare contiguous run only qualifies with an FR keyword in the near-window.
    html = "<p>SIREN 542051180</p>"
    items = _run(html)
    assert any(it["type"] == "siren" and it["value"] == "542051180" for it in items)


def test_detects_siret_with_french_separators():
    html = "<p>Siège social - SIRET 542 051 180 00066</p>"
    items = _run(html)
    siret = next(it for it in items if it["type"] == "siret")
    assert siret["value"] == "54205118000066"
    assert siret["validated"] is True


def test_detects_siret_bare_with_context():
    html = "<p>Notre SIRET : 73282932000074</p>"
    items = _run(html)
    assert any(it["type"] == "siret" and it["value"] == "73282932000074" for it in items)
    # A captured 14-digit SIRET must not also surface as a 9-digit SIREN.
    assert not any(it["type"] == "siren" for it in items)


def test_detects_tva_fr_number():
    html = "<p>TVA FR40552081317</p>"
    items = _run(html)
    tva = next(it for it in items if it["type"] == "tva_fr")
    assert tva["value"] == "FR40552081317"


def test_detects_rcs_with_city_and_siren():
    html = "<p>RCS Paris 552 081 317</p>"
    items = _run(html)
    rcs = next(it for it in items if it["type"] == "rcs")
    assert rcs["city"] == "Paris"
    assert rcs["siren"] == "552081317"
    assert rcs["validated"] is True


def test_detects_qualiopi_certification():
    html = "<p>Certifié Qualiopi 22FOR01234</p>"
    items = _run(html)
    assert any(it["type"] == "qualiopi" and it["value"] == "22FOR01234" for it in items)


def test_detects_rncp_bare_token():
    html = "<p>Titre RNCP34826 reconnu par l'État</p>"
    items = _run(html)
    rncp = next(it for it in items if it["type"] == "rncp")
    assert rncp["value"] == "RNCP34826"
    assert rncp["pivot_url"] == "https://www.francecompetences.fr/recherche/rncp/34826/"


def test_detects_rncp_registry_url_validated():
    html = '<a href="https://www.francecompetences.fr/recherche/rncp/34826/">fiche</a>'
    items = _run(html)
    rncp = next(it for it in items if it["type"] == "rncp")
    assert rncp["value"] == "RNCP34826"
    assert rncp["validated"] is True


# ---------------------------------------------------------------------------
# False positives
# ---------------------------------------------------------------------------


def test_ignores_bare_luhn_passer_without_separator_or_context():
    # 552081317 passes Luhn but has no FR separator and no nearby keyword.
    html = "<p>Reference number 552081317 was processed.</p>"
    assert _run(html) == []


def test_ignores_random_nine_digits_failing_luhn():
    # 123456780 does not satisfy the Luhn checksum even with a keyword present.
    html = "<p>SIREN 123456780</p>"
    assert _run(html) == []


def test_ignores_french_phone_number():
    # 10-digit FR phone in 2-digit groups: never a 9/14-digit identifier.
    html = "<p>Appelez-nous au 01 23 45 67 89 du lundi au vendredi.</p>"
    assert _run(html) == []


def test_ignores_bare_fourteen_digit_token_without_context():
    # Valid-Luhn 14-digit run but no separator and no nearby FR keyword.
    html = "<p>transaction 54205118000066 completed</p>"
    assert _run(html) == []


def test_ignores_placeholder_repeated_digit_siren():
    # 111111111 is a placeholder (<=2 distinct digits), rejected despite keyword.
    html = "<p>SIREN 111111111</p>"
    assert _run(html) == []


def test_ignores_periodic_placeholder_siren():
    # 243243243 passes Luhn + distinct-digit test but tiles a 3-digit motif.
    html = "<p>SIREN 243243243</p>"
    assert _run(html) == []


def test_ignores_separated_run_that_fails_luhn():
    # 123 456 789 has FR separators but fails the Luhn checksum.
    html = "<p>123 456 789</p>"
    assert _run(html) == []


def test_ignores_long_numeric_id_in_english_prose():
    # An 11-digit order id is not a 9- or 14-digit identifier; no detection.
    html = "<p>Your order 12345678901 has shipped via courier.</p>"
    assert _run(html) == []
