# WayTrace

[English](README.md) . **Français**

> **L'archive n'oublie jamais.**

Reconnaissance OSINT passive qui reconstruit l'historique numérique complet d'un domaine à partir de la Wayback Machine (archive.org). Saisissez un domaine. WayTrace récupère le HTML archivé sur des décennies, sélectionne les snapshots les plus révélateurs, et extrait **43 catégories** de renseignement. Chaque résultat porte des horodatages `first_seen` / `last_seen`, pour une chronologie complète de ce qui est apparu, a changé, puis disparu.

**Aucun scan actif. Aucun brute-force. Aucun trafic vers la cible. Uniquement des données publiques d'archive.org.**

[![En ligne sur waytrace.org](https://img.shields.io/badge/en%20ligne-waytrace.org-6f5bd6)](https://waytrace.org)
![Licence MIT](https://img.shields.io/badge/licence-MIT-blue)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)

---

## Essayer

- **Hébergé :** [**waytrace.org**](https://waytrace.org) - lancez un scan dans le navigateur, rien à installer.
- **Auto-hébergé :** clonez et `docker compose up` (voir [Démarrage rapide](#démarrage-rapide)). En l'hébergeant vous-même, le plafond de snapshots par scan disparaît : vous pouvez scanner un domaine en entier.

L'interface est entièrement bilingue (anglais / français), basculable depuis la barre de navigation.

---

## Sommaire

- [Fonctionnement](#fonctionnement)
- [Le scan guidé](#le-scan-guidé)
- [Sélection intelligente des snapshots](#sélection-intelligente-des-snapshots)
- [Catégories d'extraction](#catégories-dextraction)
- [Résultats et sévérité](#résultats-et-sévérité)
- [Interface des résultats](#interface-des-résultats)
- [Partage et flux public](#partage-et-flux-public)
- [Démarrage rapide](#démarrage-rapide)
- [Référence API](#référence-api)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Tests](#tests)
- [Légal et éthique](#légal-et-éthique)

---

## Fonctionnement

```
  saisie du domaine
       |
       v
+---------------------------------------------------------------------+
|  Phase 1 - Requête CDX                                               |
|  -------------------------------------------------------------------+
|  Interroge l'API CDX d'archive.org -> chaque URL HTML archivée       |
|  Filtre : text/html, statut 200, pagination (resumeKey)             |
|  Cache gzip local dans data/cdx/ pour éviter les appels redondants   |
|  Résultat : jusqu'à 50 000+ snapshots avec horodatages + empreintes  |
+--------------------------------+------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  Phase 2 - Sélection intelligente des snapshots                      |
|  -------------------------------------------------------------------+
|  Note chaque chemin d'URL selon sa valeur OSINT (HAUTE/MOYENNE/BASSE)|
|  Déduplique par empreinte CDX (écarte les doublons, garde le 1er)    |
|  Répartit les choix au prorata des années, aucune ère ne domine      |
|  Applique un plafond adaptatif selon la taille du domaine            |
+--------------------------------+------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  Phase 3 - Récupération (scraping)                                   |
|  -------------------------------------------------------------------+
|  Télécharge le HTML depuis la Wayback Machine pour chaque snapshot   |
|  Requêtes concurrentes (sémaphore) + délai adaptatif, recul sur 429  |
|  Budget temps : garde ce qui est téléchargé, ne bloque pas sur les   |
|  traînards. Retire la barre/scripts injectés par Wayback avant parse |
+--------------------------------+------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  Phase 4 - Extraction et agrégation                                  |
|  -------------------------------------------------------------------+
|  Parse avec selectolax (en C, ~10x plus rapide que BeautifulSoup)    |
|  Exécute 43 catégories d'extraction (regex + DOM + JSON-LD)         |
|  Agrège first_seen / last_seen / occurrences, marque la page source  |
|  Classe par sévérité (LEAK > PIVOT > CONTEXT > BACKGROUND)           |
+--------------------------------+------------------------------------+
                                 |
                                 v
                  Résultats OSINT structurés
                  avec métadonnées temporelles
```

---

## Le scan guidé

Chaque scan passe par une courte étape de cadrage interactive avant tout téléchargement ; aucun lancement à l'aveugle.

**Préflight (Phase 1 seule).** Une requête CDX légère, sans scraping. Elle renvoie le nombre total de snapshots, les chemins uniques, la plage de dates archivées, et un navigateur de snapshots par chemin.

**Page de cadrage.** À partir du préflight, vous réglez le scan :

- **Histogramme des snapshots** dans le temps ; cliquez deux années pour borner une plage.
- **Calendrier au mois près** pour une fenêtre `de -> à` exacte (le mois correspond à la granularité des données Wayback).
- **Densité** - Rapide (3/an), Dense (12/an, défaut) ou Max (les plus récents jusqu'au plafond).
- **Sélecteur de sous-domaines** - chaque sous-domaine trouvé dans l'archive, sélectionnable individuellement.
- **Exclusion d'URL** - puces de mots-clés avec préréglages (blog, tag, catégorie, auteur, flux, ...).
- Une **estimation en direct** du nombre de pages et du temps se met à jour au fil des réglages.

Au lancement, les snapshots sélectionnés sont envoyés directement, sans second aller-retour CDX.

---

## Sélection intelligente des snapshots

Toutes les pages archivées n'ont pas la même valeur. WayTrace note chaque chemin d'URL :

| Note | Chemins | Pourquoi |
|------|---------|----------|
| **HAUTE (3)** | `/contact`, `/about`, `/team`, `/staff`, `/people`, `/careers`, `/login`, `/admin`, `/press`, `/investors`, `/security`, `/partners`, `/privacy`, `/terms`, `/legal`, `/imprint`, `/blog` | Où surgissent emails, noms, téléphones et endpoints internes |
| **MOYENNE (2)** | Page d'accueil `/` | Suit l'évolution de la marque, du stack et de la propriété |
| **BASSE (1)** | Tout le reste | Contenu général |

**Déduplication de contenu.** CDX fournit une empreinte SHA-1 par snapshot ; les snapshots de même `chemin + empreinte` sont réduits à la première occurrence, évitant de scraper deux fois des pages identiques.

**Répartition au prorata des années.** Les choix sont distribués sur les années archivées plutôt que concentrés sur la période la plus capturée, pour représenter tout l'historique d'un domaine.

**Plafond adaptatif.** Le nombre maximum de pages évolue avec la taille du domaine. Sur le service hébergé, un plafond par scan (`HOSTED_SNAPSHOT_CEILING`, défaut 5000) borne les runs ; mettez-le à `0` sur une installation auto-hébergée pour scanner en entier.

---

## Catégories d'extraction

43 catégories, chaque résultat suivi avec `first_seen`, `last_seen` et `occurrences`.

**Personnes et contact**
`emails` . `phones` . `persons` . `social_profiles` . `pgp_keys`

**Secrets et expositions**
`api_keys` . `connection_strings` . `cloud_buckets` . `jwt_tokens` . `internal_ips` . `hidden_fields` . `directory_listings`

**Infrastructure et hébergement**
`subdomains` . `hosting` . `http_headers` . `status_pages` . `favicons` . `sitemaps_and_robots`

**Technique et tracking**
`technologies` . `analytics_trackers` . `analytics_ids` . `adsense_ids` . `verification_tags` . `captcha_providers` . `cookie_consent` . `auth_providers`

**Identifiants et corrélation**
`crypto_addresses` . `french_business_ids` . `github_repos` . `organizations` . `bug_bounty_programs` . `job_boards`

**Structure et contenu**
`endpoints` . `js_urls` . `iframe_sources` . `outgoing_links` . `linked_documents` . `rss_feeds` . `assets` . `html_comments` . `meta_info` . `html_titles` . `addresses`

Quelques-unes à signaler :

- **emails** - formes brutes et obscurcies, liens `mailto:` ; le bruit comme `noreply`, `example`, noms de fichiers d'assets et specifiers de modules JS est filtré.
- **api_keys** - AWS, Google, Stripe, SendGrid, webhooks Slack, jetons GitHub, plus des motifs modernes à faible faux-positif (Supabase, DigitalOcean, Shopify, Linear, npm). Toujours traités comme une fuite.
- **cloud_buckets** - URLs S3, GCS, Azure Blob, DigitalOcean Spaces, souvent du stockage public mal configuré.
- **connection_strings** - MySQL, Postgres, Mongo, Redis, AMQP, MSSQL et plus ; identifiants masqués en sortie.
- **subdomains** - hôtes dev / staging / api / interne encore référencés depuis d'anciennes pages bien après leur extinction.
- **favicons** - icône par snapshot avec empreintes MD5/SHA-256, un vecteur de corrélation inter-domaines.
- **analytics_trackers** - GA/GA4, GTM, Meta Pixel, Hotjar, Mixpanel et plus ; un même ID sur plusieurs domaines les relie à un seul propriétaire.

Chaque résultat enregistre aussi la **page source** dont il provient, pour pivoter ensemble les entités co-occurrentes (un email et un téléphone sur la même page archivée).

---

## Résultats et sévérité

WayTrace classe chaque résultat en quatre niveaux et fait remonter les importants automatiquement :

| Niveau | Sens | Exemples |
|--------|------|----------|
| **LEAK** | Exposition sensible non destinée à la publication | clés API actives, buckets cloud exposés, chaînes de connexion avec identifiants, JWT, IP internes, listings de répertoires |
| **PIVOT** | Une piste à creuser | boîtes nommées, sous-domaines, endpoints admin/auth, clés API publiques, personnes, dépôts GitHub, identifiants d'entreprise |
| **CONTEXT** | Contexte utile | stack technique, trackers analytics, hébergement/CDN, organisations, en-têtes HTTP |
| **BACKGROUND** | Listé par exhaustivité, jamais mis en avant | balises meta, titres, assets, liens sortants, commentaires |

LEAK et PIVOT sont promus en haut des résultats ; CONTEXT et BACKGROUND restent à un clic.

---

## Interface des résultats

Les résultats s'ouvrent sur une page unique avec un bloc de renseignement à onglets :

- **Activité** - une voie chronologique par catégorie sur un axe d'années partagé ; cliquez une voie pour déplier un gantt par valeur et voir quand chaque entité était active.
- **Pivots** - un graphe radial reliant le domaine à ses emails, sous-domaines, personnes, organisations, réseaux sociaux, GitHub, trackers, favicons et hébergement.
- **Sous-domaines** - classés par occurrences avec leur période d'activité.
- **Tech & infra** - stack, hébergement/CDN et en-têtes HTTP avec premières/dernières dates vues.

Sur tous les onglets : une **recherche globale** (filtre tous les onglets à la fois), des **colonnes triables**, une **copie de colonne en un clic** (p. ex. tous les emails), et un **export** en JSON, CSV (onglet courant) ou toutes les catégories d'un coup. Toute l'interface est bilingue (FR / EN).

---

## Partage et flux public

Un scan terminé est adressé par un `url_id` de 24 caractères (un jeton de capacité). Vous pouvez le garder privé ou le publier dans le flux public. Les scans partagés sont consultables par toute personne ayant le lien et exportables en JSON, CSV ou rapport HTML autonome. L'instance hébergée sur [waytrace.org](https://waytrace.org) fonctionne en mode authentifié pour lancer des scans ; une installation auto-hébergée peut tourner totalement ouverte.

---

## Démarrage rapide

### Docker (recommandé)

```bash
git clone https://github.com/HXLLO/WayTrace.git
cd WayTrace
cp .env.example .env
docker compose up -d
```

Ouvrez **http://localhost:8000**.

### Docker (développement, rechargement à chaud)

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up
```

### Manuel

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn main:app --reload
```

Ouvrez **http://localhost:8000**.

---

## Référence API

Docs Swagger interactives sur **http://localhost:8000/docs**.

### POST /api/scan/preflight

Requête CDX légère ; renvoie les stats du domaine sans scraping.

```bash
curl -X POST http://localhost:8000/api/scan/preflight \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.com"}'
```

```json
{
  "domain": "example.com",
  "total_snapshots": 47404,
  "html_snapshots": 12861,
  "unique_paths": 971,
  "date_range": { "first": "2003-08", "last": "2026-01" },
  "path_groups": [
    { "path": "/", "score": 2, "count": 412, "snapshots": [ ... ] },
    { "path": "/contact", "score": 3, "count": 89, "snapshots": [ ... ] }
  ]
}
```

### POST /api/scan

Crée un scan. Renvoie immédiatement un `job_id` ; suivez par polling ou flux. `config` est optionnel.

```bash
curl -X POST http://localhost:8000/api/scan \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "config": {
      "depth": "standard",
      "date_from": "2018-01",
      "date_to": null,
      "categories": ["emails", "subdomains", "api_keys", "phones"],
      "exclude_keywords": ["tag", "category"]
    }
  }'
```

Passez `selected_snapshots` (depuis les `path_groups` du préflight) pour scraper exactement les pages choisies :

```json
{
  "domain": "example.com",
  "selected_snapshots": [
    { "timestamp": "20210615120000", "url": "https://example.com/contact" }
  ]
}
```

### GET /api/jobs/{job_id}

Suit le statut et récupère les résultats à la fin.

```json
{
  "id": "3f8a2c1d-...",
  "status": "completed",
  "progress": 100,
  "meta": {
    "domain": "example.com",
    "total_snapshots_found": 12861,
    "snapshots_analyzed": 312,
    "pages_scraped": 298,
    "date_first_seen": "2003-08",
    "date_last_seen": "2026-01"
  },
  "results": {
    "highlights": [ { "severity": "LEAK", "category": "api_keys", "...": "..." } ],
    "emails": [ { "value": "ceo@example.com", "first_seen": "2009-03", "last_seen": "2021-11", "occurrences": 14 } ],
    "subdomains": [ "..." ]
  }
}
```

Progression du statut : `queued` -> `running` -> `completed` | `failed`.

### GET /api/jobs/{job_id}/stream

Server-Sent Events pour la progression en temps réel (préféré au polling). Événements : `progress`, `complete`, `error`, `expired` ; battement toutes les 15 s.

### Scans partagés

`GET /api/s/{url_id}` (consulter), `POST /api/s/{url_id}/publish` (basculer public), et `GET /api/s/{url_id}/export.{json,csv,html}` (télécharger). `GET /api/feed` liste les scans publiés.

### GET /api/health

```json
{ "status": "ok", "uptime_seconds": 3842, "active_jobs": 1 }
```

---

## Configuration

Tous les réglages sont dans `.env` (copié depuis `.env.example`). Les valeurs par défaut sont polies envers archive.org ; augmenter la concurrence ou baisser les délais entraîne un rate-limit.

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MAX_CONCURRENT_SCRAPES` | `8` | Requêtes Wayback parallèles (1-50) |
| `ARCHIVE_REQUEST_TIMEOUT` | `60` | Délai par requête (s) |
| `ARCHIVE_RETRY_COUNT` | `3` | Réessais sur erreurs transitoires CDX/Wayback |
| `SCRAPE_DELAY_MIN` | `0.25` | Délai min entre requêtes (s) |
| `SCRAPE_DELAY_MAX` | `0.75` | Délai max entre requêtes (s) |
| `SCRAPE_MAX_RETRIES` | `3` | Réessais par page scrapée |
| `JOB_TTL_SECONDS` | `7200` | Expiration du job (2 heures) |
| `MAX_ACTIVE_JOBS` | `10` | Scans concurrents max |
| `SCAN_TIMEOUT_SECONDS` | `3600` | Délai dur par scan (60 min) |
| `HOSTED_SNAPSHOT_CEILING` | `5000` | Plafond de snapshots par scan ; `0` le désactive pour des scans complets auto-hébergés |
| `CORS_ORIGINS` | `localhost:5173,3000` | Origines autorisées (séparées par virgules) |
| `DATABASE_URL` | `/data/waytrace.db` | Chemin SQLite (à surcharger hors Docker) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Un limiteur de débit adaptatif (`RATE_LIMIT_*`) augmente le délai sur 429 et récupère sur une série de succès ; voir `.env.example` pour l'ensemble.

---

## Architecture

```
backend/
  main.py                 App FastAPI, middleware, lifespan (nettoyage TTL)
  config.py               Réglages Pydantic depuis .env
  models.py               Schémas requête/réponse (Pydantic v2)
  db.py                   SQLite (aiosqlite) - état de crawl, jobs, résultats
  store.py                Index de jobs en mémoire + file équitable (progression)
  routers/
    scan.py               POST /scan, POST /scan/preflight, GET /jobs/{id}, SSE
    public.py             Scans partagés (/api/s/{url_id}), publication, exports, flux
    health.py             GET /health, GET /stats
  services/
    cdx.py                Client CDX, HTML uniquement, paginé, cache gzip
    filters.py            Sélection des snapshots, notation des chemins, dédup, densité
    scraper.py            Téléchargeur Wayback concurrent, sémaphore, recul, budget
    extractor/            Un module par catégorie (43 au total) + finalize/highlights

frontend/
  index.html              Fichier unique, JS vanilla, thème sombre, sans build,
                          bilingue FR/EN, résultats à onglets, recherche, export
tests/                    ~1200 tests : extraction, sélection, API, régressions
```

**Stack :** Python 3.12+, FastAPI, aiohttp, selectolax, Pydantic v2, aiosqlite, loguru.

**Notes de conception :**

- **selectolax** plutôt que BeautifulSoup - en C, ~10x plus rapide en parsing volumineux.
- **Tout en asynchrone** - aiohttp pour toutes les E/S réseau, aucun appel bloquant.
- **Filtrage CDX côté serveur** - demande seulement `text/html` + `status:200`, jamais des milliers d'assets.
- **Limitation adaptative** - `asyncio.Semaphore` + délai gigué ; recule sur 429, récupère sur succès.
- **Budget temps de scraping** - un archive.org lent ne bloque jamais un scan ; les pages téléchargées sont conservées et analysées même si des traînards sont abandonnés.
- **Provenance par résultat** - chaque entité est marquée de sa page source pour les pivots de co-occurrence.

---

## Tests

```bash
cd backend
python -m pytest tests/ -q                      # suite complète
python -m pytest tests/test_extractor.py -q     # motifs d'extraction de base
python -m pytest tests/test_filters.py -q       # sélection des snapshots
python -m pytest tests/test_api.py -q           # endpoints API
```

Chaque catégorie d'extraction fournit des tests dédiés positifs et faux-positifs (au moins cinq de chaque), en plus des tests de validation API, de cycle de vie des jobs, d'algorithme de sélection et d'intégration de bout en bout.

---

## Légal et éthique

WayTrace interroge **uniquement des archives publiques** de la Wayback Machine (archive.org). Il n'effectue aucun scan actif, scan de ports, brute-force, énumération DNS, ni aucune action intrusive contre les systèmes cibles.

- Destiné à la recherche légitime en sécurité, aux investigations OSINT, à la due diligence et à l'intelligence concurrentielle.
- Ne l'utilisez pas pour du harcèlement, du pistage ou toute activité illégale.
- Vous êtes seul responsable de l'usage des données extraites.
- Respectez les [conditions d'archive.org](https://archive.org/about/terms.php) ; n'inondez pas de requêtes et ne tentez pas de contourner les limites.

Signalements d'abus et demandes de retrait : [legal@waytrace.org](mailto:legal@waytrace.org).

---

## Licence

[MIT](LICENSE)
