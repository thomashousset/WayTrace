"""Le bandeau d'accueil annonce la fraicheur du dernier scan, pas un compteur.

Pourquoi ce changement. La bande affichait "N scans cette semaine", avec un
plancher de 52 ajoute cote frontend pour qu'elle ne s'ouvre jamais a zero. Un
nombre qu'on gonfle n'est pas une mesure, et un total hebdomadaire ne dit pas si
le service vient de servir quelqu'un. "Dernier scan il y a 5 minutes" est
verifiable, ne peut pas etre flatte, et repond a la seule question que se pose
un visiteur devant un outil qu'il ne connait pas: est-ce que ca tourne encore.

L'API rend un HORODATAGE et non un age. Le point d'acces est mis en cache une
minute cote serveur (il est sollicite par chaque onglet ouvert); un age calcule
au serveur serait donc faux d'une minute, alors qu'un horodatage reste juste et
laisse le navigateur calculer l'age a la seconde.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db as dbmod  # noqa: E402
from routers import health  # noqa: E402


@pytest_asyncio.fixture()
async def client(tmp_path):
    await dbmod.init_db(str(tmp_path / "t.db"))
    health._last_scan_cache.update({"value": None, "ts": 0.0})
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    health._last_scan_cache.update({"value": None, "ts": 0.0})


async def _insert_scan(url_id: str, created_at: str) -> None:
    conn = await dbmod.get_db()
    try:
        await conn.execute(
            "INSERT INTO jobs (url_id, domain, status, created_at, expires_at) "
            "VALUES (?,?,?,?,?)",
            (url_id, "ex.com", "completed", created_at, created_at),
        )
        await conn.commit()
    finally:
        await conn.close()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


@pytest.mark.asyncio
async def test_l_horodatage_du_dernier_scan_est_expose(client):
    quand = datetime.now(timezone.utc) - timedelta(minutes=5)
    await _insert_scan("a" * 24, _iso(quand))

    d = (await client.get("/api/service-status")).json()["service"]

    assert d["last_scan_at"] == _iso(quand)


@pytest.mark.asyncio
async def test_c_est_le_scan_le_plus_recent_qui_gagne(client):
    vieux = datetime.now(timezone.utc) - timedelta(days=3)
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    await _insert_scan("a" * 24, _iso(vieux))
    await _insert_scan("b" * 24, _iso(recent))

    d = (await client.get("/api/service-status")).json()["service"]

    assert d["last_scan_at"] == _iso(recent)


@pytest.mark.asyncio
async def test_une_base_sans_scan_rend_null_pas_une_date_inventee(client):
    """Une instance fraiche n'a rien a annoncer. Rendre une date bidon serait
    exactement le defaut qu'on corrige."""
    d = (await client.get("/api/service-status")).json()["service"]

    assert d["last_scan_at"] is None


@pytest.mark.asyncio
async def test_un_scan_hors_retention_compte_encore_s_il_est_le_dernier(client):
    """La fraicheur n'a pas de fenetre: si le dernier scan date de 40 jours, la
    reponse honnete est "il y a 40 jours", pas "aucun"."""
    vieux = datetime.now(timezone.utc) - timedelta(days=40)
    await _insert_scan("a" * 24, _iso(vieux))

    d = (await client.get("/api/service-status")).json()["service"]

    assert d["last_scan_at"] == _iso(vieux)


@pytest.mark.asyncio
async def test_le_compteur_hebdomadaire_a_disparu(client):
    """Il portait un plancher de 52 ajoute a l'affichage. On ne le remplace pas,
    on le retire."""
    d = (await client.get("/api/service-status")).json()["service"]

    assert "scans_7d" not in d


@pytest.mark.asyncio
async def test_une_panne_de_base_ne_fait_pas_tomber_le_point_d_acces(client, monkeypatch):
    """service_status ne doit jamais rendre 500: chaque sous-partie degrade
    independamment. C'est la regle du point d'acces, pas une precaution."""
    async def _casse(*_a, **_k):
        raise RuntimeError("disk is on fire")
    monkeypatch.setattr(dbmod, "last_scan_created_at", _casse)
    health._last_scan_cache.update({"value": None, "ts": 0.0})

    r = await client.get("/api/service-status")

    assert r.status_code == 200
    assert r.json()["service"]["last_scan_at"] is None
