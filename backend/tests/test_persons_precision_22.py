"""Persons: UI labels and organisation names are not people.

The author/byline selectors and <meta name="author"> reported "Read More",
"Saint Germain" and "Agence Web Paris" as named persons (PIVOT), the list a
journalist reads first.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _persons(html):
    res = extract_all([{"html": html, "url": "https://acme.fr/", "timestamp": "20220101000000"}], "acme.fr")
    return sorted(p["name"] for p in res["persons"])


@pytest.mark.parametrize("html", [
    '<a rel="author" href="/a">Read More</a>',
    '<span class="author">Saint Germain</span>',
    '<meta name="author" content="Agence Web Paris">',
    '<a class="author-link">Voir le profil</a>',
    '<span class="byline">Studio Graphique</span>',
    '<meta name="author" content="Acme SARL">',
    '<span class="author">Lire la suite</span>',
    '<span class="author">La Rédaction</span>',
])
def test_labels_and_organisations_are_dropped(html):
    assert _persons(html) == []


@pytest.mark.parametrize("html, name", [
    ('<a rel="author" href="/a">Jean Dupont</a>', "Jean Dupont"),
    ('<span class="author">By Marie Curie</span>', "Marie Curie"),
    ('<meta name="author" content="Yves Saint Laurent">', "Yves Saint Laurent"),
    ('<span class="author">Antoine de Saint-Exupéry</span>', "Antoine de Saint-Exupéry"),
    ('<span class="byline">Paris Hilton</span>', "Paris Hilton"),
    ('<span class="author">Le Thi Sa</span>', "Le Thi Sa"),
])
def test_real_people_are_kept(html, name):
    assert _persons(html) == [name]
