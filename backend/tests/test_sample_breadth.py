# backend/tests/test_sample_breadth.py
"""Un rapport ne doit jamais s'annoncer complet quand il ne couvre que quelques adresses.

Le cas qui a motive ce fichier, mesure en production le 2026-09-06. Preflight
sur mail.ru, index de 55 655 pages: 20 298 captures rendues, mais seulement
**8 chemins distincts**, parce que la fenetre de 37 500 lignes ne couvre qu'une
tranche alphabetique etroite d'un index enorme. _compute_cap voit 8 chemins,
tombe dans la branche "petit site" et rend un plafond de 100. Le scan aurait
donc traite 100 pages sur 100 selectionnees, et la ligne de couverture aurait
annonce 100 %, sur un domaine dont on n'a vu que huit adresses.

L'indicateur de couverture compare le traite au selectionne. C'est utile quand
le budget de telechargement coupe, et c'est une tautologie quand la selection
elle-meme est minuscule. La largeur de l'echantillon est la donnee manquante, et
elle est deja calculee.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import ScanConfig  # noqa: E402
from services.filters import (  # noqa: E402
    NARROW_SAMPLE_PATHS_PER_1000,
    filter_snapshots,
    is_narrow_sample,
)


def _snap(ts, url, digest=None):
    return {"timestamp": ts, "url": url, "status": "200",
            "mimetype": "text/html", "digest": digest or (url + ts)}


# ---------------------------------------------------------------------------
# La regle, sur les trois cas reellement mesures
# ---------------------------------------------------------------------------

def test_le_cas_mail_ru_est_signale():
    """8 chemins pour 20 298 captures, soit 0,4 chemin pour 1000. C'est le cas
    a ne plus jamais afficher comme complet."""
    assert is_narrow_sample(unique_paths=8, capture_count=20_298) is True


def test_security_nl_n_est_pas_signale():
    """26 175 chemins pour 36 894 captures, 710 pour 1000. Echantillon large."""
    assert is_narrow_sample(unique_paths=26_175, capture_count=36_894) is False


def test_ehesp_fr_n_est_pas_signale():
    """3 546 chemins pour 27 737 captures, 128 pour 1000."""
    assert is_narrow_sample(unique_paths=3_546, capture_count=27_737) is False


def test_un_petit_site_legitime_n_est_pas_signale():
    """Un site qui a vraiment cinq pages n'est pas un echantillon etroit, il est
    petit. C'est le rapport qui distingue les deux, pas le nombre absolu."""
    assert is_narrow_sample(unique_paths=5, capture_count=10) is False
    assert is_narrow_sample(unique_paths=1, capture_count=1) is False


def test_le_seuil_laisse_de_la_marge_des_deux_cotes():
    """Le seuil doit rester loin des deux cotes: 25 fois au dessus de mail.ru,
    un ordre de grandeur sous le cas sain le plus bas."""
    mail_ru = 1000.0 * 8 / 20_298
    ehesp = 1000.0 * 3_546 / 27_737
    assert mail_ru < NARROW_SAMPLE_PATHS_PER_1000 < ehesp
    assert NARROW_SAMPLE_PATHS_PER_1000 / mail_ru > 20
    assert ehesp / NARROW_SAMPLE_PATHS_PER_1000 > 10


def test_aucune_capture_ne_divise_pas_par_zero():
    assert is_narrow_sample(unique_paths=0, capture_count=0) is False


# ---------------------------------------------------------------------------
# La donnee remonte bien dans le resultat du filtrage
# ---------------------------------------------------------------------------

def test_filter_snapshots_expose_le_nombre_d_adresses():
    snaps = [_snap("20200%d15120000" % i, "https://exemple.fr/p%d" % (i % 3))
             for i in range(1, 10)]
    res = filter_snapshots(snaps)
    assert res["unique_paths"] == 3


def test_un_echantillon_etroit_est_marque_dans_le_resultat():
    """Reproduction du profil mail.ru en miniature: beaucoup de captures, tres
    peu d'adresses distinctes."""
    snaps = []
    for i in range(4_000):
        snaps.append(_snap("2020%04d120000" % (i % 9999),
                           "https://exemple.fr/p%d" % (i % 2),
                           digest="d%d" % i))
    res = filter_snapshots(snaps, ScanConfig(cap=100, depth="standard"))
    assert res["unique_paths"] == 2
    assert res["narrow_sample"] is True, (
        "2 adresses pour 4000 captures doit etre signale comme etroit")


def test_un_echantillon_large_n_est_pas_marque():
    snaps = []
    for p in range(500):
        for a in range(4):
            snaps.append(_snap("20%02d0615120000" % (10 + a),
                               "https://exemple.fr/p%03d" % p,
                               digest="d%d-%d" % (p, a)))
    res = filter_snapshots(snaps, ScanConfig(cap=100, depth="standard"))
    assert res["unique_paths"] == 500
    assert res["narrow_sample"] is False


def test_le_piege_des_cent_pour_cent_est_desamorce():
    """Le cas mail.ru reproduit a l'echelle, avec le plafond qu'il declenche.

    8 adresses distinctes font tomber _compute_cap dans la branche "petit site",
    donc la selection est minuscule et sera integralement traitee: traite egale
    selectionne, ce que la ligne de couverture lisait comme 100 %. Le drapeau
    doit etre leve dans ce cas precis, c'est lui qui empeche l'affichage."""
    snaps = []
    for i in range(20_298):
        snaps.append(_snap("2%03d%02d15120000" % (100 + i % 900, 1 + i % 12),
                           "https://exemple.fr/p%d" % (i % 8),
                           digest="d%d" % i))
    res = filter_snapshots(snaps)

    assert res["unique_paths"] == 8
    # La selection sera traitee en entier, donc traite / selectionne vaut 1.
    assert res["snapshots_selected"] > 0
    assert res["narrow_sample"] is True, (
        "8 adresses pour 20 298 captures: sans ce drapeau le rapport s'annonce "
        "complet alors qu'il n'a vu que huit adresses d'un index de 55 655 pages")


def test_le_marquage_ne_depend_pas_du_plafond():
    """Le plafond change combien on telecharge, pas la largeur de ce qu'on a vu.
    Un echantillon etroit le reste, quel que soit le plafond."""
    snaps = [_snap("2020%04d120000" % i, "https://exemple.fr/p%d" % (i % 2),
                   digest="d%d" % i) for i in range(4_000)]
    for plafond in (10, 100, 1_000):
        res = filter_snapshots(snaps, ScanConfig(cap=plafond, depth="standard"))
        assert res["narrow_sample"] is True, "plafond %d" % plafond
