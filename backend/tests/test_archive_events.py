"""Le compteur qui separe les familles d'echec archive.org.

Pourquoi ce fichier existe. Avant lui, un 429, un 503, une expiration et un refus
de connexion TCP arrivaient tous au disjoncteur par le meme appel,
archive_health.record_failure(), sous une signature unique. Cote index
(services/cdx.py) c'etait pire encore: 503, expiration et refus passaient
litteralement par la meme branche `except`. On ne pouvait donc pas repondre a la
seule question qui compte avant de toucher un seuil, "est-ce qu'ils nous
freinent poliment ou est-ce qu'ils nous refusent".

Les deux familles n'ont ni la meme cause ni le meme correctif:
  * 429, eventuellement avec Retry-After, c'est un service qui fonctionne et
    nous demande de ralentir,
  * refus TCP, c'est une reputation d'IP entamee,
  * 503 repetes, c'est une degradation de leur service, mesuree le 2026-09-07
    a moins de 10 % de notre plafond depuis une IP neuve.

Et la contrainte structurante: ces compteurs doivent survivre a la purge des
scans, sinon on ne peut comparer aucun avant/apres au-dela de la retention.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db as dbmod  # noqa: E402
from services import archive_events  # noqa: E402


@pytest_asyncio.fixture()
async def base(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await dbmod.init_db(path)
    yield path
    os.unlink(path)


async def _lignes(path):
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM archive_events ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]


# --- les quatre familles restent distinctes --------------------------------

@pytest.mark.asyncio
async def test_les_quatre_familles_sont_des_genres_distincts(base):
    await archive_events.record("http_429", endpoint="cdx", http_status=429)
    await archive_events.record("http_503", endpoint="cdx", http_status=503)
    await archive_events.record("timeout", endpoint="cdx")
    await archive_events.record("conn_refused", endpoint="replay")

    genres = [r["kind"] for r in await _lignes(base)]
    assert genres == ["http_429", "http_503", "timeout", "conn_refused"]
    assert len(set(genres)) == 4


@pytest.mark.asyncio
async def test_le_point_d_acces_est_conserve(base):
    await archive_events.record("conn_refused", endpoint="cdx")
    await archive_events.record("conn_refused", endpoint="replay")
    points = [r["endpoint"] for r in await _lignes(base)]
    assert points == ["cdx", "replay"]


@pytest.mark.asyncio
async def test_l_horodatage_est_pose(base):
    await archive_events.record("http_429", endpoint="cdx")
    ligne = (await _lignes(base))[0]
    assert ligne["occurred_at"]
    # Doit se relire comme une date ISO UTC.
    datetime.fromisoformat(ligne["occurred_at"].replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_un_genre_inconnu_est_refuse(base):
    """La taxonomie est le produit de cette table. Un genre libre la dissoudrait
    en quelques mois."""
    with pytest.raises(ValueError):
        await archive_events.record("bof", endpoint="cdx")


# --- Retry-After, la seule chose qu'ils nous disent -------------------------

@pytest.mark.asyncio
async def test_retry_after_est_enregistre_quand_il_est_la(base):
    await archive_events.record("http_429", endpoint="cdx", http_status=429,
                                retry_after_raw="120")
    ligne = (await _lignes(base))[0]
    assert ligne["retry_after"] == 120
    assert ligne["retry_after_raw"] == "120"


@pytest.mark.asyncio
async def test_son_absence_est_une_donnee_pas_un_zero(base):
    """Le 503 mesure le 2026-09-07 n'en portait aucun. Zero voudrait dire
    "reessaie tout de suite", ce qui est faux et dangereux."""
    await archive_events.record("http_503", endpoint="cdx", http_status=503)
    ligne = (await _lignes(base))[0]
    assert ligne["retry_after"] is None
    assert ligne["retry_after_raw"] is None


@pytest.mark.asyncio
async def test_la_valeur_brute_est_gardee_meme_illisible(base):
    """La specification HTTP autorise aussi une date, que le parseur actuel ne
    sait pas lire. Sans la valeur brute on compterait zero Retry-After en
    croyant qu'ils n'en envoient pas."""
    await archive_events.record("http_429", endpoint="cdx", http_status=429,
                                retry_after_raw="Wed, 21 Oct 2026 07:28:00 GMT")
    ligne = (await _lignes(base))[0]
    assert ligne["retry_after"] is None
    assert ligne["retry_after_raw"] == "Wed, 21 Oct 2026 07:28:00 GMT"


# --- ce qui permet de lire une mesure --------------------------------------

@pytest.mark.asyncio
async def test_le_temps_serveur_et_le_temps_total_sont_separes(base):
    """x-tr donne leur temps de traitement. Croise avec le notre, il repond a
    "archive.org est lent" contre "nous attendons", qui est la question sous
    toutes les mesures de latence du projet."""
    await archive_events.record("timeout", endpoint="cdx",
                                server_time_ms=21250, elapsed_ms=27330)
    ligne = (await _lignes(base))[0]
    assert ligne["server_time_ms"] == 21250
    assert ligne["elapsed_ms"] == 27330


@pytest.mark.asyncio
async def test_le_debit_au_moment_de_l_incident_est_fige(base):
    """Sans lui, un incident ne peut pas etre rattache au palier qui tournait."""
    await archive_events.record("conn_refused", endpoint="replay", rate_at_event=80.0)
    assert (await _lignes(base))[0]["rate_at_event"] == 80.0


# --- la contrainte principale ----------------------------------------------

@pytest.mark.asyncio
async def test_les_evenements_survivent_a_la_purge_des_scans(base):
    """La cause d'echec vivait dans jobs.meta, purge avec le scan. Comparer un
    incident de septembre a un de juillet etait donc impossible."""
    passe = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = await dbmod.get_db()
    try:
        await conn.execute(
            "INSERT INTO jobs (url_id, domain, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("perime01", "ex.com", "completed", passe, passe),
        )
        await conn.commit()
    finally:
        await conn.close()

    await archive_events.record("conn_refused", endpoint="cdx", domain="ex.com")

    supprimes = await dbmod.delete_expired_jobs()
    assert supprimes == 1, "le scan devait etre purge, sinon le test ne prouve rien"
    assert len(await _lignes(base)) == 1, "l'evenement a disparu avec le scan"


@pytest.mark.asyncio
async def test_la_table_ne_porte_aucun_identifiant_de_scan(base):
    """Elle survit a la retention: elle ne doit donc rien porter qui trahisse le
    contenu d'un scan, exactement comme scan_activity."""
    async with aiosqlite.connect(base) as conn:
        cur = await conn.execute("PRAGMA table_info(archive_events)")
        colonnes = {r[1] for r in await cur.fetchall()}
    assert "url_id" not in colonnes
    assert "user_id" not in colonnes
    assert "client_ip" not in colonnes


# --- agregation -------------------------------------------------------------

@pytest.mark.asyncio
async def test_le_comptage_regroupe_par_genre_et_point_d_acces(base):
    for _ in range(3):
        await archive_events.record("http_429", endpoint="cdx", http_status=429)
    await archive_events.record("http_429", endpoint="replay", http_status=429)
    await archive_events.record("conn_refused", endpoint="cdx")

    comptes = await archive_events.counts(hours=24)
    assert comptes[("cdx", "http_429")] == 3
    assert comptes[("replay", "http_429")] == 1
    assert comptes[("cdx", "conn_refused")] == 1


@pytest.mark.asyncio
async def test_le_comptage_ignore_ce_qui_precede_la_fenetre(base):
    vieux = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = await dbmod.get_db()
    try:
        await conn.execute(
            "INSERT INTO archive_events (occurred_at, endpoint, kind) VALUES (?,?,?)",
            (vieux, "cdx", "http_429"),
        )
        await conn.commit()
    finally:
        await conn.close()
    await archive_events.record("http_429", endpoint="cdx", http_status=429)

    assert (await archive_events.counts(hours=24))[("cdx", "http_429")] == 1
    assert (await archive_events.counts(hours=72))[("cdx", "http_429")] == 2


# --- classification ---------------------------------------------------------

def test_un_refus_tcp_se_distingue_d_une_expiration():
    import aiohttp
    refus = aiohttp.ClientConnectorError(
        connection_key=None, os_error=ConnectionRefusedError(111, "refused"))
    assert archive_events.classify_exception(refus) == "conn_refused"
    assert archive_events.classify_exception(asyncio.TimeoutError()) == "timeout"


def test_une_coupure_en_vol_n_est_pas_un_refus():
    """Un serveur qui coupe en cours de reponse n'a pas refuse la connexion.
    Les confondre gonflerait le compteur qui declenche le retour arriere."""
    import aiohttp
    assert archive_events.classify_exception(
        aiohttp.ServerDisconnectedError()) == "conn_reset"


def test_les_statuts_http_se_classent_par_famille():
    assert archive_events.classify_status(429) == "http_429"
    assert archive_events.classify_status(503) == "http_503"
    assert archive_events.classify_status(500) == "http_5xx"
    assert archive_events.classify_status(502) == "http_5xx"
    assert archive_events.classify_status(404) is None   # rien d'anormal


# --- robustesse sur le chemin critique --------------------------------------

@pytest.mark.asyncio
async def test_une_base_indisponible_ne_fait_pas_echouer_un_scan(monkeypatch, base):
    """record() est appele depuis la boucle de scrape. Une panne d'ecriture doit
    couter un compteur, jamais un scan."""
    async def _casse(*_a, **_k):
        raise RuntimeError("disk is on fire")
    monkeypatch.setattr(dbmod, "get_db", _casse)
    await archive_events.record("http_429", endpoint="cdx", http_status=429)


# --- retention propre a la table --------------------------------------------

@pytest.mark.asyncio
async def test_la_purge_garde_la_fenetre_de_comparaison(base):
    """90 jours, pas 14: comparer un palier de septembre a un de juillet est
    toute la raison d'etre de cette table."""
    recent = (datetime.now(timezone.utc) - timedelta(days=80)).strftime("%Y-%m-%dT%H:%M:%S")
    vieux = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = await dbmod.get_db()
    try:
        await conn.executemany(
            "INSERT INTO archive_events (occurred_at, endpoint, kind) VALUES (?,?,?)",
            [(recent, "cdx", "http_429"), (vieux, "cdx", "conn_refused")],
        )
        await conn.commit()
    finally:
        await conn.close()

    supprimes = await archive_events.purge(older_than_days=90)

    assert supprimes == 1
    restants = await _lignes(base)
    assert [r["kind"] for r in restants] == ["http_429"]


@pytest.mark.asyncio
async def test_la_purge_ne_fait_pas_echouer_la_boucle_de_menage(monkeypatch, base):
    async def _casse(*_a, **_k):
        raise RuntimeError("disk is on fire")
    monkeypatch.setattr(dbmod, "get_db", _casse)
    assert await archive_events.purge() == 0


# --- lecture par l'exploitation ----------------------------------------------
