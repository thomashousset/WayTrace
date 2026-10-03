# backend/tests/test_cdx_deadline.py
"""deadline_seconds doit borner la DUREE de l'appel, pas seulement le depart.

Observe en production le 2026-09-06 sur mail.ru: echeance annoncee a 55 s, appel
termine a 98 s. La boucle ne verifiait l'echeance qu'avant de lancer une
tentative. Une tentative partie a la cinquantieme seconde disposait ensuite de
ses 45 s pleines de request_timeout, soit 95 s au total, et la pagination
ajoutait encore la duree de sa derniere page en vol.

Ce n'est pas une curiosite: le preflight promet une reponse bornee a l'interface,
et le scan enchaine deux appels bornes a 55 et 35 s en comptant sur ces chiffres.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.cdx import _attempt_timeout  # noqa: E402


def test_loin_de_l_echeance_on_garde_le_timeout_normal():
    # 50 s restantes, timeout de 45: c'est le timeout qui borne.
    assert _attempt_timeout(deadline=100.0, request_timeout=45, now=50.0) == 45.0


def test_pres_de_l_echeance_c_est_l_echeance_qui_borne():
    # 10 s restantes, timeout de 45: la tentative ne peut pas prendre 45 s.
    assert _attempt_timeout(deadline=100.0, request_timeout=45, now=90.0) == 10.0


def test_le_cas_mail_ru_exactement():
    """La tentative qui a produit les 98 s: lancee a t+50 sur une echeance a
    t+55, elle prenait 45 s. Elle doit maintenant etre bornee a 5 s."""
    assert _attempt_timeout(deadline=55.0, request_timeout=45, now=50.0) == 5.0


def test_echeance_depassee_rend_zero():
    assert _attempt_timeout(deadline=55.0, request_timeout=45, now=55.0) == 0.0
    assert _attempt_timeout(deadline=55.0, request_timeout=45, now=80.0) == 0.0


def test_jamais_negatif():
    """Un timeout negatif passe a aiohttp echouerait de facon obscure."""
    for ecoule in (56.0, 100.0, 10_000.0):
        assert _attempt_timeout(deadline=55.0, request_timeout=45, now=ecoule) >= 0.0


def test_la_borne_totale_ne_depasse_jamais_l_echeance():
    """Propriete a garantir: quel que soit l'instant de depart, depart plus duree
    autorisee reste sous l'echeance. C'est ce qui etait faux."""
    echeance = 55.0
    for depart in [0.0, 1.0, 10.0, 30.0, 49.9, 54.0, 54.99]:
        borne = _attempt_timeout(deadline=echeance, request_timeout=45, now=depart)
        assert depart + borne <= echeance + 1e-9, (
            "depart a %.2f s, borne de %.2f s, on finit a %.2f s pour une echeance a %.0f s"
            % (depart, borne, depart + borne, echeance))
