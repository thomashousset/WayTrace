"""La phase d'index doit s'identifier, comme le fait deja le telechargement.

Ce que ce fichier verrouille, et pourquoi il existe: services/cdx.py construisait
sa session sans en-tetes, donc chaque requete d'index partait en annoncant
l'agent par defaut d'aiohttp, "Python/3.x aiohttp/3.y". Le scraper, lui, posait
un agent correct depuis toujours. La moitie de notre trafic etait donc anonyme,
alors que la documentation d'archive.org en fait sa seule exigence ecrite:

    All automated requests to archive.org must include a descriptive User-Agent
    header that identifies the tool or bot name and the version number.
    (archive.org/developers/bots.html, lu le 2026-09-07)

tests/test_identity.py ne testait que le constructeur de chaine, ce qui est
exactement pourquoi le trou a survecu deux mois. Ces tests-ci portent sur la
requete reellement emise.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import cdx, identity  # noqa: E402


def _reponse_cdx(rows):
    resp = AsyncMock()
    resp.status = 200
    resp.headers = {}
    resp.raise_for_status = MagicMock(return_value=None)
    resp.read = AsyncMock(return_value=json.dumps(rows).encode("utf-8"))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class _SessionsCreees:
    """Capture les kwargs de chaque aiohttp.ClientSession construite."""

    def __init__(self, reponse):
        self.kwargs = []
        self._reponse = reponse

    def patch(self, monkeypatch):
        sessions = self

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda *a, **kw: sessions._reponse)

        class FakeCS:
            def __init__(self, *_a, **kw):
                sessions.kwargs.append(kw)

            async def __aenter__(self):
                return session

            async def __aexit__(self, *_a):
                return False

        monkeypatch.setattr(cdx.aiohttp, "ClientSession", FakeCS)
        return session


@pytest.fixture(autouse=True)
def _identite_deterministe(monkeypatch):
    """L'identifiant d'instance vient de la base; on le fige pour les tests."""
    identity.reset_state_for_tests()
    monkeypatch.setattr(identity, "_instance_id", "abc123")
    yield
    identity.reset_state_for_tests()


@pytest.mark.asyncio
async def test_la_requete_d_index_porte_un_agent_waytrace(monkeypatch, tmp_path):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    lignes = [["timestamp", "original", "statuscode", "mimetype", "digest"],
              ["20200101000000", "http://ex.com/", "200", "text/html", "AAA"]]
    sessions = _SessionsCreees(_reponse_cdx(lignes))
    sessions.patch(monkeypatch)

    await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert sessions.kwargs, "aucune session construite"
    entetes = sessions.kwargs[0].get("headers") or {}
    assert "User-Agent" in entetes, (
        "la session CDX part sans en-tetes: aiohttp posera son agent par defaut"
    )
    assert entetes["User-Agent"].startswith("WayTrace/")


@pytest.mark.asyncio
async def test_l_agent_de_l_index_est_celui_du_telechargement(monkeypatch, tmp_path):
    """Une seule identite pour tout le trafic archive.org. Deux agents
    differents se liraient chez eux comme deux clients, ce qui defait
    l'attribution que identity.py cherche a donner."""
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    lignes = [["20200101000000", "http://ex.com/", "200", "text/html", "AAA"]]
    sessions = _SessionsCreees(_reponse_cdx(lignes))
    sessions.patch(monkeypatch)

    await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    attendu = await identity.current_user_agent()
    assert sessions.kwargs[0]["headers"]["User-Agent"] == attendu


@pytest.mark.asyncio
async def test_la_sonde_de_taille_s_identifie_aussi(monkeypatch):
    """cdx_size_probe ouvre sa propre session. Elle doit s'identifier comme le
    reste, sinon une seule requete anonyme suffit a nous trahir."""
    resp = AsyncMock()
    resp.status = 200
    resp.headers = {}
    resp.text = AsyncMock(return_value="12")
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    sessions = _SessionsCreees(resp)
    sessions.patch(monkeypatch)

    await cdx.cdx_size_probe("ex.com")

    entetes = sessions.kwargs[0].get("headers") or {}
    assert entetes.get("User-Agent", "").startswith("WayTrace/")


@pytest.mark.asyncio
async def test_l_agent_nomme_l_outil_et_sa_version(monkeypatch, tmp_path):
    """L'exigence d'archive.org porte sur le contenu, pas sur la presence d'un
    en-tete quelconque: nom de l'outil et numero de version."""
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    lignes = [["20200101000000", "http://ex.com/", "200", "text/html", "AAA"]]
    sessions = _SessionsCreees(_reponse_cdx(lignes))
    sessions.patch(monkeypatch)

    await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    agent = sessions.kwargs[0]["headers"]["User-Agent"]
    nom, _, reste = agent.partition("/")
    assert nom == "WayTrace"
    assert reste and reste[0].isdigit(), f"pas de version lisible dans {agent!r}"
    assert "aiohttp" not in agent.lower()
