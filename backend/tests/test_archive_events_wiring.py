"""Les deux clients archive.org doivent alimenter le compteur, chacun son genre.

Le compteur lui-meme est teste dans test_archive_events.py. Ici on verifie ce
qui l'alimente, c'est-a-dire la seule chose qui le rend utile: qu'un 429 soit
enregistre comme un 429 et pas comme "un echec", et surtout que le chemin
d'index sache enfin separer ce qu'il confondait.

Rappel de l'etat d'avant, services/cdx.py:390-408: `raise_for_status()` faisait
lever une ClientResponseError sur le 503, attrapee par le meme `except` que
l'expiration et que le refus TCP, avec le meme recul. Un blocage d'IP pendant la
phase d'index etait donc indiscernable d'un archive.org lent.

CONTRAINTE DE CE LOT, verrouillee par le dernier test du fichier: on OBSERVE,
on ne corrige pas encore. Le chemin d'index n'appelle toujours ni
record_hard_block() ni report_refusal(), pour que le deploiement de l'agent
utilisateur reste la seule variable qui bouge et que la mesure avant/apres reste
lisible.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey, RequestInfo
from yarl import URL

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import archive_events, cdx, identity, scraper  # noqa: E402

# aiohttp's exceptions format themselves from these, so building them with None
# makes str(exc) raise inside aiohttp and hides the behaviour under test. They
# are cheap to build for real.
_URL = URL("https://web.archive.org/cdx/search/cdx")
_REQUEST_INFO = RequestInfo(url=_URL, method="GET", headers={}, real_url=_URL)
_CONNECTION_KEY = ConnectionKey(
    host="web.archive.org", port=443, is_ssl=True, ssl=None,
    proxy=None, proxy_auth=None, proxy_headers_hash=None,
)


def _refus_tcp() -> aiohttp.ClientConnectorError:
    """Le refus TCP tel qu'aiohttp le presente: errno 111 sous os_error."""
    return aiohttp.ClientConnectorError(
        connection_key=_CONNECTION_KEY,
        os_error=ConnectionRefusedError(111, "Connection refused"),
    )


@pytest.fixture
def journal(monkeypatch):
    """Capture les appels a archive_events.record sans toucher a la base."""
    vus: list[dict] = []

    async def _record(kind, **kw):
        assert kind in archive_events.KINDS, f"genre hors taxonomie: {kind!r}"
        assert kw.get("endpoint") in archive_events.ENDPOINTS, kw.get("endpoint")
        vus.append({"kind": kind, **kw})

    monkeypatch.setattr(archive_events, "record", _record)
    monkeypatch.setattr(cdx.archive_events, "record", _record)
    monkeypatch.setattr(scraper.archive_events, "record", _record)
    return vus


@pytest.fixture(autouse=True)
def _identite(monkeypatch):
    identity.reset_state_for_tests()
    monkeypatch.setattr(identity, "_instance_id", "abc123")
    yield
    identity.reset_state_for_tests()


def _install_session(monkeypatch, module, effet):
    """Remplace aiohttp.ClientSession de *module* par une session dont chaque
    .get() produit *effet* (une reponse simulee ou une exception levee)."""
    session = AsyncMock()

    def _get(*_a, **_kw):
        if isinstance(effet, BaseException):
            raise effet
        return effet

    session.get = MagicMock(side_effect=_get)

    class FakeCS:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(module.aiohttp, "ClientSession", FakeCS)
    return session


def _reponse(status, headers=None, body=b"[]"):
    resp = AsyncMock()
    resp.status = status
    resp.headers = headers or {}
    if status >= 400:
        def _raise():
            raise aiohttp.ClientResponseError(
                request_info=_REQUEST_INFO, history=(), status=status,
                message=f"HTTP {status}")
        resp.raise_for_status = MagicMock(side_effect=_raise)
    else:
        resp.raise_for_status = MagicMock(return_value=None)
    resp.read = AsyncMock(return_value=body)
    resp.content = MagicMock()
    resp.content.read = AsyncMock(return_value=body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.fixture(autouse=True)
def _pas_d_attente(monkeypatch):
    """Les reculs de cdx.py vont jusqu'a 30*2**n secondes. On ne les subit pas."""
    vrai = asyncio.sleep

    async def _instantane(_s):
        await vrai(0)

    monkeypatch.setattr(asyncio, "sleep", _instantane)


# --- chemin d'index ---------------------------------------------------------

@pytest.mark.asyncio
async def test_un_429_sur_l_index_est_compte_comme_un_429(journal, monkeypatch, tmp_path):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    _install_session(monkeypatch, cdx, _reponse(429, {"Retry-After": "90"}))

    with pytest.raises(RuntimeError):
        await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert journal, "aucun evenement enregistre"
    assert journal[0]["kind"] == "http_429"
    assert journal[0]["endpoint"] == "cdx"
    assert journal[0]["retry_after_raw"] == "90"


@pytest.mark.asyncio
async def test_un_503_sur_l_index_n_est_plus_confondu_avec_une_expiration(
        journal, monkeypatch, tmp_path):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    _install_session(monkeypatch, cdx, _reponse(503))

    with pytest.raises(RuntimeError):
        await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert [e["kind"] for e in journal] == ["http_503"]


@pytest.mark.asyncio
async def test_une_expiration_sur_l_index_est_comptee_comme_telle(
        journal, monkeypatch, tmp_path):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    _install_session(monkeypatch, cdx, asyncio.TimeoutError())

    with pytest.raises(RuntimeError):
        await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert [e["kind"] for e in journal] == ["timeout"]


@pytest.mark.asyncio
async def test_un_refus_tcp_sur_l_index_est_enfin_visible(
        journal, monkeypatch, tmp_path):
    """Le cas qui motivait tout: un blocage d'IP pendant la phase d'index etait
    jusqu'ici indiscernable d'un archive.org lent."""
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    refus = _refus_tcp()
    _install_session(monkeypatch, cdx, refus)

    with pytest.raises(RuntimeError):
        await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert [e["kind"] for e in journal] == ["conn_refused"]
    assert journal[0]["endpoint"] == "cdx"


@pytest.mark.asyncio
async def test_le_domaine_est_joint_a_l_evenement(journal, monkeypatch, tmp_path):
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    _install_session(monkeypatch, cdx, _reponse(503))
    with pytest.raises(RuntimeError):
        await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)
    assert journal[0]["domain"] == "ex.com"


@pytest.mark.asyncio
async def test_une_reponse_saine_n_ecrit_aucun_evenement(journal, monkeypatch, tmp_path):
    """Une ligne par requete noierait le signal. Seuls les incidents comptent."""
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    lignes = [["timestamp", "original", "statuscode", "mimetype", "digest"],
              ["20200101000000", "http://ex.com/", "200", "text/html", "AAA"]]
    _install_session(monkeypatch, cdx,
                     _reponse(200, body=json.dumps(lignes).encode()))

    await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert journal == []


@pytest.mark.asyncio
async def test_les_pages_de_reprise_comptent_sous_leur_propre_point_d_acces(
        journal, monkeypatch, tmp_path):
    """La pagination faisait un simple `break` sur tout non-200, sans aucun
    retour de sante: une page de reprise en 503 n'existait nulle part. Elle est
    comptee a part de la premiere page, parce que la premiere est bornee par le
    limit et les suivantes non, donc elles ne cassent pas pour les memes raisons.
    """
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    session = AsyncMock()
    premiere = _reponse(200, body=json.dumps([
        ["timestamp", "original", "statuscode", "mimetype", "digest"],
        ["20200101000000", "http://ex.com/", "200", "text/html", "AAA"],
        ["une-cle-de-reprise-suffisamment-longue"],
    ]).encode())
    reprise = _reponse(503)
    reponses = [premiere, reprise]
    session.get = MagicMock(side_effect=lambda *a, **kw: reponses.pop(0))

    class FakeCS:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cdx.aiohttp, "ClientSession", FakeCS)

    await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert [e["kind"] for e in journal] == ["http_503"]
    assert journal[0]["endpoint"] == "cdx_resume"


# --- chemin de telechargement ----------------------------------------------

@pytest.mark.asyncio
async def test_un_429_sur_une_capture_est_compte_sur_replay(journal, monkeypatch):
    monkeypatch.setattr(scraper.settings, "scrape_max_retries", 0)
    monkeypatch.setattr(scraper.settings, "max_concurrent_scrapes", 1)
    _install_session(monkeypatch, scraper, _reponse(429, {"Retry-After": "30"}))
    monkeypatch.setattr(scraper, "store", MagicMock(update_job=AsyncMock()))

    await scraper.scrape_snapshots(
        [{"timestamp": "20200101000000", "url": "http://ex.com/"}], "job1")

    assert [e["kind"] for e in journal] == ["http_429"]
    assert journal[0]["endpoint"] == "replay"
    assert journal[0]["retry_after_raw"] == "30"


@pytest.mark.asyncio
async def test_un_refus_tcp_sur_une_capture_est_compte(journal, monkeypatch):
    monkeypatch.setattr(scraper.settings, "scrape_max_retries", 0)
    monkeypatch.setattr(scraper.settings, "max_concurrent_scrapes", 1)
    refus = _refus_tcp()
    _install_session(monkeypatch, scraper, refus)
    monkeypatch.setattr(scraper, "store", MagicMock(update_job=AsyncMock()))

    await scraper.scrape_snapshots(
        [{"timestamp": "20200101000000", "url": "http://ex.com/"}], "job1")

    assert [e["kind"] for e in journal] == ["conn_refused"]
    assert journal[0]["endpoint"] == "replay"


# --- la contrainte du lot ---------------------------------------------------

@pytest.mark.asyncio
async def test_ce_lot_observe_mais_ne_change_pas_le_disjoncteur(
        journal, monkeypatch, tmp_path):
    """Garde-fou deliberé. Le chemin d'index doit compter un refus SANS declencher
    le disjoncteur dur ni faire chuter le regulateur, sinon le deploiement de
    l'agent utilisateur ne serait plus la seule variable et la mesure avant/apres
    deviendrait illisible. Le correctif de comportement est un lot separe.

    Si tu viens de faire echouer ce test en ajoutant record_hard_block() dans
    cdx.py: c'est probablement le bon changement, mais il appartient au lot
    suivant. Supprime ce test dans le meme commit, en connaissance de cause.
    """
    from services import archive_health, archive_rate
    monkeypatch.setattr(cdx, "_CACHE_DIR", tmp_path)
    durs, chutes = [], []
    monkeypatch.setattr(archive_health, "record_hard_block", lambda: durs.append(1))
    monkeypatch.setattr(cdx.archive_health, "record_hard_block", lambda: durs.append(1))
    monkeypatch.setattr(cdx.archive_rate, "report_refusal", lambda: chutes.append(1))

    refus = _refus_tcp()
    _install_session(monkeypatch, cdx, refus)
    with pytest.raises(RuntimeError):
        await cdx.fetch_cdx_snapshots("ex.com", use_cache=False, retries=0)

    assert [e["kind"] for e in journal] == ["conn_refused"], "le refus doit etre compte"
    assert durs == [], "le lot d'observation ne doit pas armer le disjoncteur dur"
    assert chutes == [], "le lot d'observation ne doit pas faire chuter le debit"
