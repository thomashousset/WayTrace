# WayTrace

[English](README.md) · **Français**

Reconstruire ce qu'un domaine a publié, puis effacé, à partir des pages que la
Wayback Machine a conservées. 48 catégories de trouvailles, chacune datée du
mois où elle est apparue et du mois où elle a été vue pour la dernière fois.

**Aucune clé d'API. Aucun trafic vers la cible.** Tout vient d'archive.org.

[![En ligne sur waytrace.org](https://img.shields.io/badge/live-waytrace.org-E87A48)](https://waytrace.org)
[![tests](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/thomashousset/WayTrace/actions/workflows/ci.yml)
![Licence MIT](https://img.shields.io/badge/license-MIT-blue)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)

![Le rapport](docs/screenshots/report.png)

<sub>Capture prise sur waytrace.org. Les deux boutons flottants en bas à droite
sont propres à la version hébergée et absents de la version auto-hébergée.</sub>

## À quoi ressemble une trouvaille

Un scan réel de `compaq.com`, entreprise absorbée par HP en 2002. 199 pages archivées
lues, 1931 trouvailles, dont 28 encore présentes dans la capture la plus
récente.

```json
{
  "path": "/alphaserver/site_index.html",
  "first_seen": "2001-04",
  "last_seen": "2016-04",
  "occurrences": 97,
  "source_url": "https://web.archive.org/web/20010405141733/http://www.compaq.com:80/..."
}
```

```json
{
  "value": "openvms.compaq.com",
  "first_seen": "2000-06",
  "last_seen": "2009-10",
  "occurrences": 60,
  "source": "html",
  "source_url": "https://web.archive.org/web/20000620181347/http://www.compaq.com:80/..."
}
```

Chaque trouvaille porte la page archivée d'où elle vient, donc toute
affirmation peut être ouverte et vérifiée. Rien n'est déduit.

## Démarrage rapide

```bash
git clone https://github.com/thomashousset/WayTrace && cd WayTrace
cp .env.example .env
docker compose up -d          # http://localhost:8000
```

Sans Docker :

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn main:app --reload     # http://localhost:8000
```

Rechargement à chaud avec Docker : `docker compose -f docker-compose.dev.yml up`.

Hébergé sur [waytrace.org](https://waytrace.org) si vous préférez ne rien
installer. La version auto-hébergée n'a pas de comptes, pas de plafond de
captures par scan, et une page Réglages qui expose toutes les options de scan
et d'archive.org.

## Utilisation

Ouvrez `http://localhost:8000`, tapez un domaine, cliquez sur Scan. Appuyez
plutôt sur Entrée pour choisir les sous-domaines, une période et une densité
de captures avant de lancer.

Ou pilotez-le en HTTP :

```bash
# Ce que contient l'archive, sans rien télécharger
curl -X POST localhost:8000/api/scan/preflight \
     -H 'Content-Type: application/json' \
     -d '{"domain":"example.com"}'

# Lancer un scan
curl -X POST localhost:8000/api/scan \
     -H 'Content-Type: application/json' \
     -d '{"domain":"example.com","config":{"depth":"quick","cap":400}}'

# Interroger, puis lire le rapport
curl localhost:8000/api/s/<url_id>
```

Les rapports s'exportent en JSON, en CSV, ou en un fichier HTML autonome qui
s'ouvre hors ligne sans serveur dessous. Référence complète des points
d'entrée : [docs/API.md](docs/API.md).

## Ce qui est extrait

48 catégories. Tout porte `first_seen`, `last_seen` et `occurrences`.

| | |
|---|---|
| **Identité** (7) | `emails` `persons` `phones` `addresses` `organizations` `social_profiles` `github_repos` |
| **Infrastructure** (8) | `subdomains` `endpoints` `internal_ips` `hosting` `http_headers` `connection_strings` `directory_listings` `cdn_accounts` |
| **Secrets** (7) | `api_keys` `jwt_tokens` `cloud_buckets` `crypto_addresses` `pgp_keys` `verification_tags` `adsense_ids` |
| **Technique et SaaS** (8) | `technologies` `js_urls` `payment_processors` `support_chat` `email_marketing` `mobile_apps` `captcha_providers` `auth_providers` |
| **Traceurs** (4) | `analytics_trackers` `analytics_ids` `cookie_consent` `rss_feeds` |
| **Signaux d'organisation** (4) | `bug_bounty_programs` `job_boards` `status_pages` `french_business_ids` |
| **Contenu** (10) | `html_titles` `html_comments` `meta_info` `hidden_fields` `assets` `linked_documents` `outgoing_links` `iframe_sources` `favicons` `sitemaps_and_robots` |

La liste exacte est `ALL_CATEGORIES` dans
[`backend/services/extractor/finalize.py`](backend/services/extractor/finalize.py).

Les trouvailles sont classées en quatre niveaux : **LEAK** pour une exposition
que le propriétaire n'avait pas prévu de publier, **PIVOT** pour une piste à
suivre, **CONTEXT** pour le contexte, **BACKGROUND** pour tout ce qui est
listé sans jamais être mis en avant.

## Lire un rapport

![Activité dans le temps](docs/screenshots/activity.png)

La vue Activité place les catégories sur un axe de temps commun et marque le
mois où chaque valeur est apparue et celui où elle a cessé. **Still present**
signifie qu'une valeur atteint la capture la plus récente que ce scan a
regardée, ce qui n'est pas la même chose qu'être en ligne aujourd'hui.

## Fonctionnement

```
1. cdx.py         demander à l'index CDX d'archive.org quelles captures HTML existent
2. filters.py     noter les chemins par valeur OSINT, répartir la sélection sur les
                  années, écarter les captures dont l'empreinte est déjà prise
3. scraper.py     télécharger les pages retenues, concurrence plafonnée, avec un
                  régulateur AIMD qui lève le pied quand archive.org le demande
4. extractor/     48 modules de catégorie, expressions régulières et analyse DOM selectolax
```

FastAPI, SQLite, aiohttp, selectolax. Aucun cadriciel JavaScript et aucune
étape de compilation : l'interface, ce sont `index.html`, `app.js` et
`styles.css`, servis tels quels. Détail dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Configuration

Tout est dans `.env`. Celles qui comptent :

| Variable | Dans `.env.example` | Rôle |
|---|---|---|
| `MAX_CONCURRENT_SCRAPES` | `4` | Plafond de requêtes vers archive.org par scan |
| `ARCHIVE_GLOBAL_CONCURRENCY` | `3` | Plafond du processus, partagé par tous les scans |
| `HOSTED_SNAPSHOT_CEILING` | `3000` | Plafond de captures par scan. `0` le désactive, ce qui est le bon réglage en auto-hébergé |
| `SCAN_TIMEOUT_SECONDS` | `3600` | Durée maximale d'un scan |
| `SCAN_RETENTION_DAYS` | `14` | Durée de conservation d'un scan terminé |
| `DATABASE_URL` | non défini | Chemin SQLite. Non défini, la base se crée à la racine du dépôt |

Liste complète dans [`.env.example`](.env.example).

## Tests

```bash
cd backend && python -m pytest tests/ -q
```

## Cadre légal

WayTrace lit une archive publique. Il n'envoie rien au domaine que vous
regardez, et il ne peut rien voir qu'archive.org n'ait déjà publié. Cela ne
rend pas pour autant libre d'usage tout ce qu'il fait remonter : une page
archivée peut contenir des données personnelles, et ce que vous avez le droit
d'en faire relève du droit applicable chez vous, pas de cet outil.

Utilisez-le sur des domaines qui vous appartiennent, ou dans le cadre d'un
mandat, ou sur des sujets où l'intérêt public est réel. Ne l'utilisez pas pour
profiler des personnes privées.

## Auteur

Écrit et maintenu par [Thomas Housset](https://thomashousset.com/).

Les bugs et les idées sont les bienvenus en issues. Pour tout ce qui touche à
la sécurité, lisez d'abord [SECURITY.md](SECURITY.md) : tester waytrace.org
lui-même mérite un mot au préalable, et les raisons y sont expliquées.

## Licence

MIT. Voir [LICENSE](LICENSE).
