# WayTrace

[English](README.md) · **Français**

> **Internet n'oublie jamais.**

Reconnaissance OSINT qui reconstruit l'historique numérique complet d'un domaine à partir de la Wayback Machine (archive.org). Saisissez un domaine. WayTrace récupère le HTML archivé sur des décennies, sélectionne les snapshots les plus révélateurs, et extrait **43 catégories** de renseignement. Chaque résultat porte des horodatages `first_seen` / `last_seen`, pour une chronologie complète de ce qui est apparu, a changé, puis disparu. Vous pouvez même faire une recherche plein-texte dans le contenu des pages archivées.

**Aucun scan actif. Aucun brute-force. Aucun trafic vers la cible. Uniquement des données publiques d'archive.org.**

[![En ligne sur waytrace.org](https://img.shields.io/badge/en%20ligne-waytrace.org-6f5bd6)](https://waytrace.org)
[![tests](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml)
![Licence MIT](https://img.shields.io/badge/licence-MIT-blue)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)

---

## Essayer

- **Hébergé :** [**waytrace.org**](https://waytrace.org) - lancez un scan dans le navigateur, rien à installer.
- **Auto-hébergé :** clonez et `docker compose up` (voir [Démarrage rapide](#démarrage-rapide)). En l'hébergeant vous-même, le plafond de snapshots par scan disparaît : vous pouvez scanner un domaine en entier.

L'interface est entièrement bilingue (anglais / français), basculable depuis la barre de navigation.

---

## Nouveautés de la v1.6.0

- **Rapport repensé, deux vues.** *Catégories* (défaut) : un rail des 43 catégories, une ouverte à la fois, montrant ses résultats **et** sa propre activité (apparu/disparu par valeur + un flux de changements daté). *Activité* : cochez des catégories et des pivots précis pour composer une frise partagée, avec la galerie d'évolution des favicons. **La provenance d'abord, neutre** : chaque résultat porte son *first/last-seen*, ses *occurrences* et sa *page source archivée* ; l'UI de « gravité » et le graphe Pivots encombrant disparaissent.
- **Scan vivant.** L'extraction chevauche le téléchargement et tourne hors de l'event loop : les résultats se remplissent pendant le scan et le serveur reste réactif. Chargement honnête en quatre phases.
- **Plus de re-scan accidentel.** Un domaine scanné dans les **14 derniers jours** est réutilisé au lieu d'être re-scanné ; « Scan more » force un scan frais.
- **Finitions.** Recherche plein-texte corrigée pour la ponctuation, focus clavier visible, état d'erreur du flux, et la source Wayback Machine créditée par son logo.

## Nouveautés de la v1.5.0

- **Accès archive.org auto-régulé, sûr pour l'IP.** Chaque requête passe par un gouverneur de débit *adaptatif* partagé (AIMD, comme le contrôle de congestion TCP : il monte doucement tant que les réponses restent propres et se divise par deux au premier refus de connexion) plus une limite de concurrence partagée. La v1.5 fixe le plafond à **80 req/min** (sous le seuil où archive.org a été mesuré refusant les connexions) et fait **démarrer une pause de blocage à 2 minutes** au lieu de 30 fixes, escaladant seulement sur refus consécutifs - un refus passager coûte donc peu.
- **Un seul scan à la fois.** Un scan actif unique, une file d'attente de 15, et un scan en cours max par client gardent la charge archive.org minimale.
- **Recherche plein-texte dans le contenu des pages** (depuis la v1.2.0) : cherchez n'importe quel mot dans les pages archivées d'un scan, pas seulement les pivots extraits, avec extraits surlignés et liens vers la capture Wayback.
- **Finitions UX :** progression de chargement honnête (vraies pages récupérées + ETA mesurée, sans hoquet), bannière d'état archive.org bilingue, catégories de résultats auto-descriptives, et beaucoup de code mort retiré.

Voir [CHANGELOG.md](CHANGELOG.md) pour l'historique complet (v1.0 → v1.6).

---

## Sommaire

- [Fonctionnement](#fonctionnement)
- [Le scan guidé](#le-scan-guidé)
- [Sélection intelligente des snapshots](#sélection-intelligente-des-snapshots)
- [Catégories d'extraction](#catégories-dextraction)
- [Résultats et provenance](#résultats-et-provenance)
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

**Plafond adaptatif.** Le nombre maximum de pages évolue avec la taille du domaine. Sur le service hébergé, un plafond par scan (`HOSTED_SNAPSHOT_CEILING`, défaut 3000) borne les runs ; mettez-le à `0` sur une installation auto-hébergée pour scanner en entier.

---

## Catégories d'extraction

43 catégories, chaque résultat suivi avec `first_seen`, `last_seen` et `occurrences`.

**Personnes et contact**
`emails` · `phones` · `persons` · `social_profiles` · `pgp_keys`

**Secrets et expositions**
`api_keys` · `connection_strings` · `cloud_buckets` · `jwt_tokens` · `internal_ips` · `hidden_fields` · `directory_listings`

**Infrastructure et hébergement**
`subdomains` · `hosting` · `http_headers` · `status_pages` · `favicons` · `sitemaps_and_robots`

**Technique et tracking**
`technologies` · `analytics_trackers` · `analytics_ids` · `adsense_ids` · `verification_tags` · `captcha_providers` · `cookie_consent` · `auth_providers`

**Identifiants et corrélation**
`crypto_addresses` · `french_business_ids` · `github_repos` · `organizations` · `bug_bounty_programs` · `job_boards`

**Structure et contenu**
`endpoints` · `js_urls` · `iframe_sources` · `outgoing_links` · `linked_documents` · `rss_feeds` · `assets` · `html_comments` · `meta_info` · `html_titles` · `addresses`

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

## Résultats et provenance

WayTrace ne vous dit **pas** ce qui est « important » : il montre la preuve et vous laissez juger. Chaque résultat porte :

| Champ | Ce qu'il vous dit |
|-------|-------------------|
| **vu de / à** | quand la valeur est apparue dans l'archive et quand elle était présente pour la dernière fois (ce qui est encore là vs disparu) |
| **occurrences** | sur combien de pages archivées elle apparaît |
| **page source** | la capture Wayback exacte d'où elle vient, vérifiable en un clic |

Les catégories avec résultats remontent en premier ; le périmètre complet des 43 catégories (y compris les vides) reste visible pour la transparence, pour qu'un résultat propre se lise « on a cherché et rien trouvé », pas « on n'a pas cherché ».

---

## Interface des résultats

Le rapport est une page unique avec deux vues entre lesquelles vous basculez :

- **Catégories (par défaut).** Un rail à gauche liste les 43 catégories : celles avec résultats d'abord (avec compteurs), puis les vides repliées mais présentes. Vous ouvrez **une catégorie à la fois** ; le panneau montre tous ses résultats (valeur, occurrences, vu de / à, et un lien vers la page source archivée) **et sa propre activité** en dessous : une voie par valeur montrant quand elle est apparue et a disparu, plus un flux de changements daté. « Tout afficher » déroule toutes les catégories trouvées d'un coup.
- **Activité.** Cochez des catégories **et** des pivots précis (un sous-domaine, un tracker, un favicon, une personne...) pour composer une frise partagée : chacun devient une voie sur le même axe d'années (pivots mis en avant), pour lire d'un coup d'œil les recouvrements et les disparitions. L'axe couvre toujours exactement ce qui est affiché. Inclut la galerie d'évolution des favicons et un flux de changements global. Les pivots sont cherchables.

Deux recherches en tête, bien distinctes : **filtrer les résultats extraits** (instantané, côté client) et **recherche plein-texte dans le contenu des pages archivées** (n'importe quel mot du HTML récupéré, avec extraits surlignés et lien vers la capture Wayback exacte). Chaque valeur est copiable (par valeur ou par colonne entière), et vous pouvez **exporter** en JSON, CSV ou rapport HTML autonome.

WayTrace ne classe pas les résultats par « importance » : chaque résultat porte sa **provenance** (vu de / à, occurrences, source archivée) et c'est vous qui jugez. Chaque catégorie est affichée, y compris celles à **zéro résultat**, pour que vous voyiez toujours l'étendue complète de ce qui a été cherché, pas seulement de ce qui a été trouvé.

---

## Partage et flux public

Un scan terminé est adressé par un `url_id` de 24 caractères (un jeton de capacité). Vous pouvez le garder privé ou le publier dans le flux public. Les scans partagés sont consultables par toute personne ayant le lien et exportables en JSON, CSV ou rapport HTML autonome. L'instance hébergée sur [waytrace.org](https://waytrace.org) fonctionne en mode authentifié pour lancer des scans ; une installation auto-hébergée peut tourner totalement ouverte.

---

## Démarrage rapide

### Docker (recommandé)

```bash
git clone https://github.com/thomashousset/WayTrace.git
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

### Scans partagés & stockage

Chaque scan est stocké sous un `url_id` stable et reste disponible pendant la fenêtre de rétention (14 jours sur le build hébergé ; configurable en auto-hébergé) :

- `GET /api/s/{url_id}` - consulter un scan ; `DELETE` pour le supprimer ; `POST /api/s/{url_id}/publish` pour basculer public.
- `GET /api/s/{url_id}/search?q=…` - recherche plein-texte dans le contenu des pages archivées du scan.
- `GET /api/s/{url_id}/export.{json,csv,html}` - télécharger.
- `GET /api/feed` - scans récemment publiés.
- `GET /api/local-scans` - **auto-hébergé uniquement** : liste tous les scans lancés par cette instance (publiés ou privés), pour qu'un utilisateur solo conserve et réaccède à tous ses scans depuis « Mes scans ». Désactivé sur le build hébergé, qui rattache les scans aux comptes.

### GET /api/health

```json
{ "status": "ok", "version": "1.6.0", "uptime_seconds": 3842, "active_jobs": 1 }
```

---

## Configuration

Tous les réglages sont dans `.env` (copié depuis `.env.example`). Les valeurs par défaut sont polies envers archive.org ; augmenter la concurrence ou baisser les délais entraîne un rate-limit.

| Variable | Défaut | Description |
|----------|--------|-------------|
| `ARCHIVE_RATE_PER_MINUTE` | `75` | Débit **de départ** des requêtes archive.org (req/min). Le gouverneur l'adapte en direct. |
| `ARCHIVE_RATE_MIN` / `ARCHIVE_RATE_MAX` | `60` / `80` | Plancher et plafond dans lesquels le débit adaptatif reste (1 → 1,33 req/s) |
| `ARCHIVE_GLOBAL_CONCURRENCY` | `3` | Connexions archive.org simultanées max, tous scans confondus |
| `MAX_CONCURRENT_SCRAPES` | `4` | Requêtes parallèles par scan (1-50) |
| `SCRAPE_DELAY_MIN` / `SCRAPE_DELAY_MAX` | `0.5` / `1.2` | Gigue par requête (s) |
| `MAX_ACTIVE_TOTAL` | `1` | Scans exécutés en même temps ; le reste attend en file |
| `MAX_QUEUE_TOTAL` | `15` | Profondeur de la file d'attente (actifs + en attente) |
| `MAX_ACTIVE_PER_IP` | `1` | Scans en cours par client (impossible d'en empiler un 2ᵉ) |
| `ARCHIVE_REQUEST_TIMEOUT` | `60` | Délai par requête (s) |
| `HOSTED_SNAPSHOT_CEILING` | `3000` | Plafond de snapshots par scan ; `0` le désactive pour des scans **complets** auto-hébergés |
| `SCAN_RETENTION_DAYS` | `14` | Durée de conservation d’un scan (et de réutilisation par le garde-fou) |
| `IS_PRODUCTION` | `0` | `1` en prod : refuse de démarrer avec le `SECRET_KEY` par défaut |
| `DATABASE_URL` | `/data/waytrace.db` | Chemin SQLite (à surcharger hors Docker) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

**À propos du gouverneur de débit.** archive.org ne publie aucune limite de scraping et sa tolérance est dynamique, donc WayTrace ne devine pas un chiffre fixe : il démarre prudemment, pousse le débit vers le haut tant que les réponses restent propres, et *le divise par deux dès qu'archive.org refuse une connexion* (AIMD, comme le contrôle de congestion TCP). Cela garde l'IP du serveur hors de la liste de blocage d'archive.org quel que soit le nombre de scans ou d'utilisateurs simultanés. Relever les plafonds accélère les scans à vos risques. Voir `.env.example` pour l'ensemble.

---

## Architecture

```
backend/
  main.py                 App FastAPI, middleware, lifespan (nettoyage TTL)
  config.py               Réglages Pydantic depuis .env
  models.py               Schémas requête/réponse (Pydantic v2)
  db.py                   SQLite (aiosqlite) - scans + index FTS5 du contenu des pages
  store.py                Index de jobs en mémoire + file équitable (progression)
  routers/
    scan.py               POST /scan, POST /scan/preflight, GET /jobs/{id}, SSE
    public.py             Scans partagés (/api/s/{url_id}), recherche, exports, flux
    health.py             GET /health, GET /archive-status, GET /stats
  services/
    cdx.py                Client CDX, HTML uniquement, paginé, cache gzip
    filters.py            Sélection des snapshots, notation des chemins, dédup, densité
    scraper.py            Téléchargeur Wayback concurrent, budget, recul
    archive_rate.py       Gouverneur de débit (AIMD) + concurrence partagé
    archive_health.py     Disjoncteur : détection throttling + blocage d'IP dur
    extractor/            Un module par catégorie (43 au total) + finalize/highlights

frontend/                 index.html + styles.css + app.js - JS vanilla, sans build,
                          clair/sombre, bilingue FR/EN, rapport à deux vues
tests/                    1200+ tests : extraction, sélection, API, anti-blocage, régressions
```

**Stack :** Python 3.12+, FastAPI, aiohttp, selectolax, Pydantic v2, aiosqlite, loguru.

**Notes de conception :**

- **selectolax** plutôt que BeautifulSoup - en C, ~10x plus rapide en parsing volumineux.
- **Tout en asynchrone** - aiohttp pour toutes les E/S réseau, aucun appel bloquant.
- **Filtrage CDX côté serveur** - demande seulement `text/html` + `status:200`, jamais des milliers d'assets.
- **Gouverneur de débit adaptatif, sûr pour l'IP** - un token bucket partagé dont le débit s'auto-ajuste (AIMD) sur chaque appel archive.org, plus un plafond de concurrence partagé et un disjoncteur qui distingue un blocage d'IP dur d'un throttling ordinaire. Garde l'IP du serveur hors de la liste de blocage sous n'importe quelle charge.
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
