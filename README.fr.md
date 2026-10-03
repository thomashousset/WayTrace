# WayTrace

[English](README.md) · **Français**

WayTrace reconstruit l'histoire publique d'un domaine à partir de la Wayback Machine (archive.org). Vous donnez un domaine ; l'outil lit le HTML qu'archive.org a déjà enregistré au fil des ans, choisit les instantanés les plus révélateurs à travers le temps, et en extrait **48 catégories** de renseignement, des e-mails et sous-domaines aux secrets exposés, technologies et personnes. Chaque trouvaille porte les dates `first_seen` et `last_seen` dans l'archive : vous obtenez une chronologie de ce qui est apparu, a changé et a disparu, pas seulement un instantané d'aujourd'hui.

**L'outil ne touche jamais la cible.** Aucun scan de port, aucun brute force, aucune énumération DNS, aucun trafic vers le domaine lui-même. Chaque octet vient de l'archive publique d'archive.org. La cible ne vous voit jamais.

[![En ligne sur waytrace.org](https://img.shields.io/badge/live-waytrace.org-6f5bd6)](https://waytrace.org)
[![tests](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml)
![Licence MIT](https://img.shields.io/badge/license-MIT-blue)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)

## Essayer

- **Hébergé :** [**waytrace.org**](https://waytrace.org) lance un scan dans le navigateur, rien à installer.
- **Auto-hébergé :** clonez et `docker compose up` (voir [Démarrage rapide](#démarrage-rapide)). La version auto-hébergée n'a aucun compte, aucun plafond de snapshots par scan, et une page Réglages qui expose chaque paramètre de scan et d'archive.org : vous pouvez analyser un domaine en entier et régler l'outil à votre machine.

L'interface est entièrement bilingue (anglais et français), basculable depuis la barre de navigation.

## Sommaire

- [Démarrage rapide](#démarrage-rapide)
- [Fonctionnement](#fonctionnement)
- [Le scan guidé](#le-scan-guidé)
- [Ce qui est extrait](#ce-qui-est-extrait)
- [Trouvailles et provenance](#trouvailles-et-provenance)
- [Le rapport](#le-rapport)
- [Réglages (auto-hébergé)](#réglages-auto-hébergé)
- [Configuration](#configuration)
- [API](#api)
- [Architecture](#architecture)
- [Tests](#tests)
- [Légal et éthique](#légal-et-éthique)
- [Licence](#licence)

## Démarrage rapide

### Docker (recommandé)

```bash
git clone https://github.com/thomashousset/WayTrace.git
cd WayTrace
cp .env.example .env
docker compose up -d
```

Ouvrez **http://localhost:8000**. Le fichier compose par défaut n'écoute que sur `127.0.0.1` ; placez un reverse proxy devant pour l'exposer.

### Docker (développement, rechargement à chaud)

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up
```

### Sans Docker

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn main:app --reload
```

Ouvrez **http://localhost:8000**. La base de données est par défaut `waytrace.db` à la racine du projet ; rien d'autre à configurer.

## Fonctionnement

Un scan est un pipeline en quatre phases. Seules les phases 3 et 4 sollicitent archive.org pour du contenu ; la phase 1 est une seule requête d'index et la phase 2 est un calcul purement local.

```
  domaine
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  1 · Index (CDX)                                                     │
│  Interroger l'API CDX d'archive.org : chaque URL HTML archivée      │
│  Garder text/html + statut 200, paginé ; cache gzip dans data/cdx/  │
│  → jusqu'à des dizaines de milliers d'enregistrements (date+digest) │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  2 · Sélection                                                      │
│  Noter chaque chemin d'URL par valeur OSINT (haute / moyenne / basse)│
│  Écarter les captures identiques par digest, garder la plus ancienne│
│  Répartir les choix par année pour qu'aucune époque ne domine       │
│  Plafonner le nombre selon la taille du domaine                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  3 · Téléchargement                                                 │
│  Récupérer les captures choisies depuis la Wayback Machine          │
│  Débit adaptatif + limite de concurrence partagée, recul sur refus  │
│  Budget de temps : garder ce qui est téléchargé, jamais bloqué      │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  4 · Extraction                                                     │
│  Parser avec selectolax (en C), lancer 48 extracteurs de catégorie  │
│  (regex + DOM + JSON-LD), agréger first_seen / last_seen /          │
│  occurrences, et estampiller chaque trouvaille de sa capture source │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
              Résultats structurés avec une chronologie complète
```

## Le scan guidé

Rien n'est téléchargé à l'aveugle. Chaque scan commence par une étape de cadrage légère.

**Le prévol** exécute la phase 1 seulement : une requête CDX, sans scraping. Il renvoie le nombre total de snapshots, les chemins uniques, la plage de dates archivées, et un explorateur de snapshots par chemin.

**Le cadrage** permet de façonner le scan avant tout téléchargement :

- un **histogramme des snapshots** dans le temps ; cliquez deux années pour borner une plage ;
- un **calendrier au mois près** pour une fenêtre exacte `de → à` (le mois correspond à la granularité de Wayback) ;
- la **densité** : Rapide (peu par an), Dense (par défaut) ou Max (autant que le plafond l'autorise) ;
- un **sélecteur de sous-domaines** : chaque sous-domaine vu dans l'archive, sélectionnable individuellement ;
- l'**exclusion par mot-clé** : des puces avec préréglages (blog, tag, category, author, feed…) ;
- une **estimation en direct** des pages et du temps, qui se met à jour pendant que vous ajustez.

Au lancement, les snapshots exacts que vous avez choisis sont envoyés directement à la phase 3, sans second aller-retour d'index.

## Ce qui est extrait

48 catégories. Chaque trouvaille suit `first_seen`, `last_seen` et `occurrences`, et retient la page archivée dont elle provient.

**Personnes et contact**
`emails` · `phones` · `persons` · `organizations` · `addresses` · `social_profiles`

**Secrets et expositions**
`api_keys` · `connection_strings` · `cloud_buckets` · `jwt_tokens` · `crypto_addresses` · `internal_ips` · `hidden_fields` · `directory_listings` · `pgp_keys`

**Infrastructure et hébergement**
`subdomains` · `hosting` · `http_headers` · `favicons` · `technologies` · `status_pages`

**Traçage et identifiants**
`analytics_trackers` · `analytics_ids` · `adsense_ids` · `verification_tags` · `cookie_consent` · `captcha_providers` · `auth_providers` · `github_repos` · `bug_bounty_programs` · `job_boards` · `french_business_ids`

**Business et intégrations SaaS**
`support_chat` · `email_marketing` · `payment_processors` · `cdn_accounts` · `mobile_apps`

**Structure et contenu**
`endpoints` · `js_urls` · `iframe_sources` · `outgoing_links` · `linked_documents` · `assets` · `sitemaps_and_robots` · `rss_feeds` · `html_comments` · `meta_info` · `html_titles`

Quelques-unes valent d'être soulignées :

- **api_keys** couvre AWS, Google, Stripe, SendGrid, webhooks Slack, jetons GitHub, et des motifs modernes à faible faux positif (Supabase, DigitalOcean, Shopify, Linear, npm). Toujours traité comme une fuite.
- **support_chat / email_marketing / payment_processors / cdn_accounts** extraient l'identifiant de compte ou de tenant des embeds SaaS tiers (app_id Intercom, portail HubSpot, boutique Shopify, cloud Cloudinary, app Algolia...). Le même identifiant sur un autre domaine relie les deux sites au même opérateur.
- **mobile_apps** relie le domaine à ses applications iOS / Android via les balises meta app-banner et App Links et les liens de store, avec pivot vers la fiche du store (éditeur, avis, historique des versions).
- **cloud_buckets** repère les URL S3, GCS, Azure Blob et DigitalOcean Spaces, refuge habituel du stockage public mal configuré.
- **connection_strings** reconnaît MySQL, Postgres, Mongo, Redis, AMQP, MSSQL et plus ; les identifiants sont masqués en sortie.
- **subdomains** fait remonter les hôtes dev / staging / api / internes encore référencés par de vieilles pages longtemps après leur extinction.
- **favicons** hache chaque icône (MD5 et SHA-256), un vecteur de corrélation entre domaines.
- **analytics_trackers** capte GA/GA4, GTM, Meta Pixel, Hotjar, Mixpanel et d'autres ; un identifiant de traçage partagé entre domaines les relie à un même propriétaire.

Comme chaque trouvaille retient sa **capture source**, les entités qui coexistent sur une même page archivée (un e-mail et un numéro, une personne et une adresse) peuvent être pivotées ensemble.

### Sévérité

Les trouvailles sont triées en quatre niveaux pour faire remonter le signal, sans rien cacher :

| Niveau | Signification |
|--------|---------------|
| **LEAK** | Une exposition sensible réelle que le propriétaire n'avait pas l'intention de publier. |
| **PIVOT** | Une piste à suivre ; elle mène à des entités liées. |
| **CONTEXT** | Contexte utile pour comprendre la cible. |
| **BACKGROUND** | Listé pour l'exhaustivité, jamais mis en avant. |

## Trouvailles et provenance

WayTrace ne vous dit pas ce qui est « important » et ne note pas les trouvailles à la sensation. Il montre les preuves et vous laisse juger. Chaque trouvaille porte :

| Champ | Ce qu'il vous dit |
|-------|-------------------|
| **first seen / last seen** | quand la valeur est apparue dans l'archive, et quand elle y était présente pour la dernière fois (donc ce qui est vivant ou disparu) |
| **occurrences** | sur combien de pages archivées elle est apparue |
| **page source** | la capture Wayback exacte d'où elle vient, un clic pour vérifier |

Les catégories qui ont trouvé quelque chose sont présentées en premier. Les catégories vides restent visibles aussi : un résultat propre se lit « on a cherché et rien trouvé », pas « on n'a pas cherché ».

## Le rapport

Le résultat est une page unique avec deux vues entre lesquelles vous basculez.

**Catégories** (par défaut) est un rail de toutes les 48 catégories, celles avec trouvailles d'abord (avec compteurs), les vides présentes mais repliées. Vous ouvrez une catégorie à la fois ; son panneau montre les trouvailles (valeur, occurrences, first/last-seen, lien vers la capture source) et, en dessous, sa propre activité : une piste par valeur montrant quand elle est apparue et a disparu, plus un fil de changements daté. « Tout afficher » aplatit d'un coup toutes les catégories trouvées.

**Activité** permet de cocher des catégories et des pivots individuels (un sous-domaine précis, un traceur, un favicon, une personne) pour composer une chronologie partagée sur un même axe d'années : chevauchements et disparitions se lisent d'un coup d'œil. Elle inclut la galerie d'évolution des favicons et un fil de changements global.

Deux recherches en haut, volontairement distinctes : **filtrer les trouvailles extraites** (instantané, côté client) et **rechercher en plein texte dans le contenu archivé** (n'importe quel mot dans le HTML récupéré, avec extraits surlignés et lien vers la capture exacte). Chaque valeur est copiable, et vous pouvez **exporter** le scan entier en JSON, CSV, ou un rapport HTML autonome.

Un scan terminé est adressé par un `url_id` de 24 caractères, un jeton de capacité : connaître le lien suffit pour le voir ou l'exporter. Les scans sont privés ; il n'y a ni flux public ni comptes. Sur une installation auto-hébergée, **Mes scans** liste chaque scan lancé par l'instance, avec son heure de début exacte et sa durée, pour garder tout votre historique en local.

## Réglages (auto-hébergé)

La version auto-hébergée embarque une page **Réglages** (dans la barre de navigation) qui transforme chaque paramètre de scan et d'archive.org en formulaire. C'est le panneau que vous éditeriez sinon dans `.env`, rendu vivant :

- chaque réglage groupé par étape du pipeline (politesse archive.org, sélection des snapshots, scans et file, avancé), avec sa description, son unité et sa valeur recommandée ;
- les changements ne s'appliquent qu'au clic sur le **Enregistrer** propre à chaque champ, donc rien n'est validé par accident ;
- des zones de risque plutôt que des plafonds durs : une valeur peut aller partout où c'est techniquement valide, mais la zone orange est signalée agressive et la zone rouge prévient d'un risque réel qu'archive.org bloque votre IP ;
- les quelques réglages qui exigent un redémarrage offrent un **Redémarrer maintenant** en un clic ;
- la rétention des scans accepte une option infinie (∞), pour qu'une installation auto-hébergée puisse garder chaque scan indéfiniment.

Sur le service hébergé ces limites restent verrouillées ; le panneau est une fonctionnalité d'auto-hébergement.

## Configuration

Les réglages vivent dans `.env` (copiez `.env.example`), et les variables inconnues sont ignorées : un reliquat d'une ancienne version ne bloque jamais le démarrage. Chaque valeur ci-dessous peut aussi être changée en direct depuis la page Réglages. Les valeurs par défaut sont volontairement polies envers archive.org ; augmenter la concurrence ou baisser les délais est ce qui fait limiter une IP.

| Variable | Défaut | Description |
|----------|--------|-------------|
| `ARCHIVE_RATE_PER_MINUTE` | `75` | Débit de requêtes archive.org de **départ** (req/min) ; le gouverneur l'adapte en direct |
| `ARCHIVE_RATE_MIN` / `ARCHIVE_RATE_MAX` | `60` / `80` | Plancher et plafond dans lesquels le débit adaptatif reste |
| `ARCHIVE_GLOBAL_CONCURRENCY` | `3` | Connexions archive.org simultanées, tous scans confondus |
| `MAX_CONCURRENT_SCRAPES` | `4` | Requêtes parallèles par scan (1 à 50) |
| `SCRAPE_DELAY_MIN` / `SCRAPE_DELAY_MAX` | `0.5` / `1.2` | Délai aléatoire par requête (s) |
| `MAX_ACTIVE_TOTAL` | `1` | Scans exécutés en même temps ; les autres patientent |
| `MAX_QUEUE_TOTAL` | `100` | Plafond des scans en cours et en attente |
| `MAX_ACTIVE_PER_IP` | `2` | Scans simultanés par IP cliente |
| `ARCHIVE_REQUEST_TIMEOUT` | `60` | Délai maximal par requête (s) |
| `HOSTED_SNAPSHOT_CEILING` | `3000` | Plafond de snapshots par scan ; `0` le retire, pour des scans auto-hébergés complets |
| `SNAPSHOT_CAP_MULTIPLIER` | `1.0` | Multiplie le plafond adaptatif de snapshots avant le préréglage de profondeur |
| `SCAN_RETENTION_DAYS` | `14` | Durée de conservation et de réutilisation d'un scan ; `0` les garde indéfiniment |
| `DATABASE_URL` | `<repo>/waytrace.db` | Chemin SQLite ; les images Docker fixent `/data/waytrace.db` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `EXPOSE_API_DOCS` | `0` | `1` sert la documentation interactive de l'API sur `/api/docs` |

**Le gouverneur de débit.** archive.org ne publie aucune limite de scraping et sa tolérance évolue, donc WayTrace ne devine pas un nombre fixe. Un seau à jetons partagé démarre prudent, pousse le débit vers le haut tant que les réponses restent saines, et le divise par deux dès qu'archive.org refuse une connexion (AIMD, comme le contrôle de congestion TCP). Combiné au plafond de concurrence partagé et à un disjoncteur qui distingue un vrai blocage d'IP d'un simple bridage, cela garde l'IP du serveur hors de la liste de blocage d'archive.org quel que soit le nombre de scans ou d'utilisateurs. Voir `.env.example` pour l'ensemble complet.

## API

L'API HTTP est celle qu'utilise le frontend. La documentation interactive est servie sur `/api/docs` quand `EXPOSE_API_DOCS=1`. Référence complète des endpoints : [docs/API.md](docs/API.md) (en anglais).

**Scan**

- `POST /api/scan/preflight` : requête CDX seule ; renvoie les stats du domaine sans scraper.
- `POST /api/scan` : lance un scan ; renvoie immédiatement un `job_id`. Accepte un `config` (profondeur, plage de dates, catégories, mots-clés exclus) ou une liste explicite `selected_snapshots` issue du prévol.
- `GET /api/jobs/{job_id}` : interroge le statut et, à la fin, les résultats.
- `GET /api/jobs/{job_id}/stream` : Server-Sent Events pour la progression en direct (`progress`, `complete`, `error`).

**Scans**

- `GET /api/s/{url_id}` : voir un scan stocké ; `DELETE` pour le supprimer.
- `GET /api/s/{url_id}/search?q=…` : recherche plein texte dans le contenu archivé du scan.
- `GET /api/s/{url_id}/export.{json,csv,html}` : télécharger le scan.
- `GET /api/local-scans` : chaque scan lancé par l'instance (« Mes scans » en auto-hébergé).

**Service**

- `GET /api/health` : `{ "status": "ok", "version": "1.8.0", "uptime_seconds": …, "active_jobs": … }`
- `GET /api/service-status` : profondeur de file, santé d'archive.org, décompte glissant des scans.
- `GET /api/config`, `PUT /api/config`, `POST /api/config/reset`, `POST /api/config/restart` : le panneau Réglages (auto-hébergé uniquement).

Exemple :

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
  "date_range": { "first": "2003-08", "last": "2026-01" }
}
```

## Architecture

```
backend/
  main.py             app FastAPI, middleware, cycle de vie (reprise de file, purge)
  config.py           réglages Pydantic depuis .env (variables inconnues ignorées)
  models.py           schémas requête/réponse (Pydantic v2)
  db.py               SQLite (aiosqlite) : scans + index FTS5 du contenu de page
  store.py            index mémoire des jobs + file équitable à l'épreuve du redémarrage
  routers/
    scan.py           /scan, /scan/preflight, /jobs/{id}, flux SSE
    public.py         scans stockés (/s/{url_id}), recherche, exports, mes scans
    health.py         /health, /service-status, /stats
    selfhost_config.py  l'API du panneau Réglages (/config)
  services/
    cdx.py            client CDX, HTML seul, paginé, cache gzip
    filters.py        sélection des snapshots : score de chemin, dédup, densité
    scraper.py        téléchargeur Wayback concurrent, budget de temps, recul
    archive_rate.py   gouverneur adaptatif (AIMD) de débit + concurrence, partagé
    archive_health.py disjoncteur : bridage vs détection de vrai blocage d'IP
    runtime_config.py registre des réglages modifiables derrière le panneau Réglages
    extractor/        un module par catégorie (48) + finalize + highlights

frontend/             index.html + styles.css + app.js : JS vanilla, sans étape de
                      build, thèmes sombre/clair, bilingue EN/FR, rapport à deux vues
tests/                ~1250 tests sur ~80 fichiers : extraction, sélection,
                      API, anti-blocage, régressions
```

**Stack :** Python 3.12+, FastAPI, aiohttp, selectolax, Pydantic v2, aiosqlite, loguru.

Une présentation plus détaillée du pipeline et des choix de conception se trouve dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (en anglais).

Choix de conception à connaître :

- **selectolax** plutôt que BeautifulSoup : un parseur en C, environ dix fois plus rapide sur du HTML en volume.
- **Asynchrone partout** : aiohttp pour toutes les E/S réseau, aucun appel bloquant.
- **Filtrage CDX côté serveur** : ne demander que `text/html` et `status:200`, jamais des milliers de lignes d'assets.
- **Un seul gouverneur de débit sûr pour l'IP** : un unique seau à jetons partagé et auto-réglé sur chaque appel archive.org, plus un plafond de concurrence partagé et un disjoncteur, pour qu'aucune charge ne mette l'IP sur la liste de blocage.
- **Un budget de temps de scraping** : un archive.org lent ne bloque jamais un scan ; les pages téléchargées sont gardées et analysées même si des traînards sont abandonnés.
- **Une file à l'épreuve du redémarrage** : les scans en file ou en cours survivent à un redémarrage et se remettent en file sous le même lien.
- **Provenance par trouvaille** : chaque entité est estampillée de sa capture source pour les pivots de coexistence.

## Tests

```bash
cd backend
python -m pytest tests/ -q                    # suite complète
python -m pytest tests/test_extractor.py -q   # motifs d'extraction
python -m pytest tests/test_filters.py -q     # sélection des snapshots
python -m pytest tests/test_api.py -q         # endpoints de l'API
```

Chaque catégorie d'extraction embarque des tests positifs et de faux positifs dédiés (au moins cinq de chaque), aux côtés de tests de validation d'API, de cycle de vie des jobs, d'algorithme de sélection et de bout en bout. La CI exécute la suite complète sur Python 3.12.

## Légal et éthique

WayTrace lit **uniquement les archives publiques** de la Wayback Machine. Il ne fait aucun scan actif, aucun scan de port, aucun brute force, aucune énumération DNS, et n'envoie rien à la cible.

- Il est conçu pour la recherche en sécurité légitime, les enquêtes OSINT, le travail journalistique et la recherche académique ou historique.
- Toutes les données source proviennent de l'Internet Archive. En utilisant WayTrace, vous acceptez également les [conditions d'utilisation de l'Internet Archive](https://archive.org/about/terms.php) ; n'inondez pas de requêtes et ne cherchez pas à contourner les limites.
- Les pages archivées peuvent contenir des données personnelles ; il n'existe pas d'exemption générale pour les données personnelles publiques au titre du RGPD. Manipulez ce que vous trouvez de façon responsable, et signalez les risques aux personnes qui possèdent les données, jamais contre elles.
- Vous êtes seul responsable de l'usage que vous faites de WayTrace et de ses résultats. Le logiciel est fourni « en l'état », sans garantie, et l'auteur décline toute responsabilité dans la mesure permise par la loi.

Signalements d'abus et demandes de retrait : [housset.thomas@pm.me](mailto:housset.thomas@pm.me).

Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour ajouter une catégorie d'extraction, et [CHANGELOG.md](CHANGELOG.md) pour l'historique des versions.

## Licence

[MIT](LICENSE)
