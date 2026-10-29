"""Real people the label/organisation filter dropped, and must keep.

Any interface word anywhere in the phrase made it a label, so "Mark Read",
"Hannah More", "See Kok Leong" and "Thomas Plus" went; "sas" as a legal
form also took "Nathalie Sas". A name followed by a role ("Sophie Martin -
Consulting", "Pierre Lefèvre, Directeur Marketing", "Claire Dubois |
Rédaction") was dropped whole, where the name in front is the finding.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _persons(html):
    res = extract_all([{"html": html, "url": "https://acme.fr/", "timestamp": "20220101000000"}], "acme.fr")
    return sorted(p["name"] for p in res["persons"])


@pytest.mark.parametrize("html, name", [
    ('<meta name="author" content="Mark Read">', "Mark Read"),
    ('<a rel="author" href="/a">Simon Read</a>', "Simon Read"),
    ('<meta name="author" content="Hannah More">', "Hannah More"),
    ('<span class="author">See Kok Leong</span>', "See Kok Leong"),
    ('<span class="author">Thomas Plus</span>', "Thomas Plus"),
    ('<meta name="author" content="Nathalie Sas">', "Nathalie Sas"),
    ('<meta name="author" content="Sophie Martin - Consulting">', "Sophie Martin"),
    ('<meta name="author" content="Pierre Lefèvre, Directeur Marketing">', "Pierre Lefèvre"),
    ('<meta name="author" content="Claire Dubois | Rédaction">', "Claire Dubois"),
    ('<script type="application/ld+json">{"author": "Paul Henri – Agence Web"}</script>', "Paul Henri"),
])
def test_people_come_out(html, name):
    assert _persons(html) == [name]


@pytest.mark.parametrize("html", [
    '<meta name="author" content="Acme SAS">',
    '<span class="author">View all posts</span>',
    '<meta name="author" content="Agence Web - Paris">',
    '<meta name="author" content="La Rédaction | Acme">',
    '<meta name="author" content="Read More, Voir le profil">',
])
def test_labels_and_organisations_stay_out(html):
    assert _persons(html) == []


def test_a_surname_first_comma_form_is_kept_whole():
    assert _persons('<meta name="author" content="Dupont, Jean">') == ["Dupont, Jean"]


def test_several_authors_are_kept_as_written():
    assert _persons('<meta name="author" content="Jean Dupont, Marie Durand">') == ["Jean Dupont, Marie Durand"]
