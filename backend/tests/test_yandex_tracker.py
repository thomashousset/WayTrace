"""Tests for Yandex Metrica tracker pattern."""
from services.extractor.patterns import TRACKER_PATTERNS


def test_yandex_metrica_ym():
    pattern = TRACKER_PATTERNS["Yandex_Metrica"]
    assert pattern.search('ym(12345678, "init",') is not None


def test_yandex_metrica_id_captured():
    pattern = TRACKER_PATTERNS["Yandex_Metrica"]
    match = pattern.search('ym(87654321, "init", {')
    assert match is not None
    assert match.group(1) == "87654321"


def test_yandex_metrica_no_match_short():
    pattern = TRACKER_PATTERNS["Yandex_Metrica"]
    assert pattern.search('ym(123, "init"') is None


def test_yandex_metrica_no_match_plain():
    pattern = TRACKER_PATTERNS["Yandex_Metrica"]
    assert pattern.search('<p>No yandex here</p>') is None
