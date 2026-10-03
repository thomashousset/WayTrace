# backend/tests/test_client_side_cdx_filter.py
"""Le filtrage type de contenu + code de statut passe du serveur CDX a chez nous.

Pourquoi ce fichier existe: la requete CDX portait `filter=statuscode:200` et
`filter=mimetype:text/html`. Mesure du 2026-09-06: ces deux filtres obligent
archive.org a parcourir tout l'index du domaine sans jamais pouvoir s'arreter
sur la limite, et font passer une requete de 8,7 s a plus de 50 s, au dela du
delai que le scan s'accorde. Ils sont retires de la requete et appliques ici.

Le risque du changement est precis: `filter_snapshots` ne verifiait que le type
de contenu, jamais le code de statut, parce que le serveur s'en chargeait. Sans
ces tests, des captures en 404 ou en 301 entreraient dans les rapports.
"""
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import ScanConfig  # noqa: E402
from services.filters import filter_snapshots  # noqa: E402


def _snap(ts, url="https://exemple.fr/", status="200", mimetype="text/html", digest=None):
    return {"timestamp": ts, "url": url, "status": status,
            "mimetype": mimetype, "digest": digest or ts}


# ---------------------------------------------------------------------------
# 1. Le code de statut est filtre chez nous, ce que le serveur faisait avant
# ---------------------------------------------------------------------------

def test_les_captures_non_200_sont_ecartees():
    snaps = [
        _snap("20200101120000", "https://exemple.fr/ok"),
        _snap("20200201120000", "https://exemple.fr/introuvable", status="404"),
        _snap("20200301120000", "https://exemple.fr/deplace", status="301"),
        _snap("20200401120000", "https://exemple.fr/erreur", status="500"),
        _snap("20200501120000", "https://exemple.fr/interdit", status="403"),
    ]
    retenues = filter_snapshots(snaps)["selected"]
    urls = {s["url"] for s in retenues}
    assert urls == {"https://exemple.fr/ok"}, (
        "seule la capture en 200 doit survivre, or on a garde %s" % sorted(urls))


def test_une_redirection_en_html_ne_passe_pas():
    # Le cas concret redoute: archive.org sert des redirections avec le type
    # text/html. Sans verification du statut elles ressemblent a des pages.
    snaps = [_snap("20210101120000", "https://exemple.fr/a", status="301"),
             _snap("20210201120000", "https://exemple.fr/b", status="302")]
    assert filter_snapshots(snaps)["selected"] == []


def test_le_type_de_contenu_reste_filtre():
    snaps = [
        _snap("20200101120000", "https://exemple.fr/page"),
        _snap("20200201120000", "https://exemple.fr/image.png", mimetype="image/png"),
        _snap("20200301120000", "https://exemple.fr/data.json", mimetype="application/json"),
        _snap("20200401120000", "https://exemple.fr/style.css", mimetype="text/css"),
    ]
    urls = {s["url"] for s in filter_snapshots(snaps)["selected"]}
    assert urls == {"https://exemple.fr/page"}


def test_les_deux_criteres_se_cumulent():
    # HTML mais 404, et 200 mais pas HTML: aucune des deux ne doit passer.
    snaps = [
        _snap("20200101120000", "https://exemple.fr/html-404", status="404"),
        _snap("20200201120000", "https://exemple.fr/png-200", mimetype="image/png"),
        _snap("20200301120000", "https://exemple.fr/bonne"),
    ]
    urls = {s["url"] for s in filter_snapshots(snaps)["selected"]}
    assert urls == {"https://exemple.fr/bonne"}


def test_un_statut_absent_ne_fait_pas_tout_tomber():
    # Defensif: si CDX omet la colonne, on ne veut pas jeter la capture en
    # silence. parse_cdx_rows garantit la cle, mais un appelant de test ou une
    # variante de CDX peut la laisser vide.
    snaps = [{"timestamp": "20200101120000", "url": "https://exemple.fr/x",
              "mimetype": "text/html", "digest": "d1"}]
    assert len(filter_snapshots(snaps)["selected"]) == 1


def test_le_comptage_total_reste_celui_de_l_entree():
    # total_snapshots_found decrit ce qu'archive.org a rendu, pas ce qu'on garde.
    snaps = [_snap("20200101120000", "https://exemple.fr/ok"),
             _snap("20200201120000", "https://exemple.fr/ko", status="404")]
    res = filter_snapshots(snaps)
    assert res["total_snapshots_found"] == 2
    assert res["snapshots_selected"] == 1


# ---------------------------------------------------------------------------
# 2. Determinisme: le filtrage cote client fait arriver les lignes dans un
#    ordre different, la selection ne doit pas en dependre
# ---------------------------------------------------------------------------

def _jeu_realiste():
    """403 chemins, 26 annees, 6 mois: assez pour que le plafond morde et pour
    qu'il y ait beaucoup d'egalites de timestamp entre chemins, ce qui est le
    cas ou un tri non total rend la sortie dependante de l'ordre d'entree."""
    chemins = ["/p%03d" % i for i in range(400)] + ["/", "/contact", "/admin"]
    snaps = []
    for i, p in enumerate(chemins):
        for annee in range(2000, 2026):
            for mois in (1, 3, 5, 7, 9, 11):
                ts = "%04d%02d15120000" % (annee, mois)
                snaps.append(_snap(ts, "https://exemple.fr%s" % p,
                                   digest="D%03d%04d%02d" % (i, annee, mois)))
    return snaps


def test_selection_identique_quel_que_soit_l_ordre_d_entree():
    snaps = _jeu_realiste()
    cfg = ScanConfig(cap=200, depth="standard")

    reference = [(s["timestamp"], s["url"])
                 for s in filter_snapshots(list(snaps), cfg)["selected"]]
    assert len(reference) == 200, "le plafond doit mordre, sinon le test ne prouve rien"

    for graine in range(8):
        melange = list(snaps)
        random.Random(graine).shuffle(melange)
        obtenu = [(s["timestamp"], s["url"])
                  for s in filter_snapshots(melange, cfg)["selected"]]
        assert obtenu == reference, (
            "melange %d: la selection change avec l'ordre d'entree" % graine)


def test_deux_appels_identiques_donnent_le_meme_resultat():
    snaps = _jeu_realiste()
    cfg = ScanConfig(cap=200, depth="standard")
    a = [(s["timestamp"], s["url"]) for s in filter_snapshots(list(snaps), cfg)["selected"]]
    b = [(s["timestamp"], s["url"]) for s in filter_snapshots(list(snaps), cfg)["selected"]]
    assert a == b


def test_le_melange_ne_change_rien_meme_avec_des_captures_a_ecarter():
    """Le cas reel du changement: les lignes non 200 arrivent intercalees, a des
    positions differentes selon la reponse d'archive.org."""
    snaps = _jeu_realiste()
    parasites = [_snap("%04d0715120000" % a, "https://exemple.fr/bruit%d" % a,
                       status="404", digest="N%d" % a) for a in range(2000, 2026)]
    cfg = ScanConfig(cap=200, depth="standard")

    reference = [(s["timestamp"], s["url"])
                 for s in filter_snapshots(list(snaps), cfg)["selected"]]
    for graine in range(4):
        melange = list(snaps) + parasites
        random.Random(graine).shuffle(melange)
        obtenu = [(s["timestamp"], s["url"])
                  for s in filter_snapshots(melange, cfg)["selected"]]
        assert obtenu == reference, (
            "melange %d: les captures ecartees influencent la selection" % graine)
