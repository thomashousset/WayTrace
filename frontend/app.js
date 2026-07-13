/* ===== STATE ===== */
const API = '';
let currentDomainId = null;

/* ===== v2 PUBLIC STATE ===== */
let publicScanUrlId = null;
let publicScanPollTimer = null;
let publicScanLastStatus = null;
let v2PublicMode = false;  // set true when a public scan is rendered into view-results

// Results state
let allFindings = [];
let filteredFindings = [];
let sortCol = 'occurrences';
let sortDir = 'desc';
let findingsPage = 0;
let activeCategory = null;

// History state
let historyData = [];

// Compare selection (set of domain IDs picked for side-by-side comparison)
let histSortCol = 'updated_at';
let histSortDir = 'desc';

// Timeline drawer state
let timelineOpen = false;
let timelineSynced = true;      // sync with table filters by default
let timelineShowAll = false;    // show pivot-worthy subset by default
let timelineHiddenTracks = new Set();  // category strings hidden by user
let timelineActiveFilter = null; // { type: 'category'|'tick', category: '...', pivotMonth: '...' }

const TIMELINE_DEFAULT_CATEGORIES = [
  'analytics_trackers',
  'adsense_ids',
  'favicons',
  'hosting',
  'verification_tags',
  'meta_info',
  'subdomains',
  'outgoing_links',
];

const TIMELINE_CATEGORY_LABELS = {
  analytics_trackers: 'Trackers',
  adsense_ids: 'Adsense',
  favicons: 'Favicons',
  hosting: 'Hosting',
  verification_tags: 'Verification',
  meta_info: 'Meta tags',
  subdomains: 'Subdomains',
  cloud_buckets: 'Cloud buckets',
  addresses: 'Postal addresses',
  outgoing_links: 'Outgoing',
  // Fallback: any other category is title-cased from the string
};

/* ===== HELPERS ===== */
const $ = id => document.getElementById(id);
const esc = s => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
// Attribute-safe escape: esc() only handles & < > (text context); inside a
// double-quoted attribute a " in the value would end the attribute early, so
// also entity-escape the quotes. Use for any value interpolated into an
// attribute (title="...", etc.).
const escAttr = s => esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function highlightMatch(value, query) {
  // Wrap case-insensitive matches of query in <mark>. Done on the raw string
  // (before HTML escaping) to avoid the escape sequences (e.g. &amp;) being
  // interpreted as part of the search pattern.
  const raw = String(value || '');
  if (!query) return esc(raw);
  const q = String(query).trim();
  if (!q) return esc(raw);

  const lower = raw.toLowerCase();
  const qLower = q.toLowerCase();
  if (!lower.includes(qLower)) return esc(raw);

  // Walk the raw string, emit escaped pieces separated by <mark>…</mark>
  let out = '';
  let i = 0;
  while (i < raw.length) {
    const idx = lower.indexOf(qLower, i);
    if (idx === -1) {
      out += esc(raw.slice(i));
      break;
    }
    out += esc(raw.slice(i, idx));
    out += '<mark>' + esc(raw.slice(idx, idx + q.length)) + '</mark>';
    i = idx + q.length;
  }
  return out;
}

function showToast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2000);
}

// Global safety net: an uncaught JS error or rejected promise used to leave a
// half-rendered screen with no feedback. Surface a friendly, throttled toast
// (and keep logging to the console for debugging) instead.
let _lastErrToast = 0;
function _reportClientError(detail) {
  try { console.error('WayTrace client error:', detail); } catch (_) {}
  const now = Date.now();
  if (now - _lastErrToast < 5000) return;   // throttle
  _lastErrToast = now;
  try { showToast(t('Something went wrong. Please try again.')); } catch (_) {}
}
window.addEventListener('error', (e) => _reportClientError(e.error || e.message));
window.addEventListener('unhandledrejection', (e) => _reportClientError(e.reason));

// Poll archive.org health and surface a banner when it is slow or paused, so
// users understand why a scan is delayed instead of seeing a silent failure.
async function checkArchiveStatus() {
  const el = $('archive-banner');
  if (!el) return;
  try {
    const d = await (await fetch(API + '/api/archive-status')).json();
    if (!d || d.state === 'ok') { el.hidden = true; return; }
    el.className = 'archive-banner ' + (d.state === 'paused' ? 'paused' : 'slow');
    el.textContent = _archiveStatusMessage(d);
    el.hidden = false;
  } catch (_) { el.hidden = true; }
}

// Localise the archive.org banner client-side (the backend message is English
// only). Distinguishes a hard IP block from ordinary throttling.
function _archiveStatusMessage(d) {
  if (d.state === 'paused') {
    if (d.blocked) {
      const mins = Math.max(1, Math.round((d.cooldown_remaining || 0) / 60));
      return t('Archive.org is refusing connections from this server (it looks IP-blocked). Scanning is paused for about {n} min to let it recover.').replace('{n}', mins);
    }
    return t('Scanning is paused for about {s}s: archive.org is rate-limiting us. Please retry in a moment.').replace('{s}', d.cooldown_remaining || 0);
  }
  return t('Archive.org is slow right now; scans may take longer than usual.');
}

// Show the clean 404 view for unknown routes (and for routes a viewer cannot
// access, without revealing whether they exist).
function showNotFound() {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const nf = $('view-notfound'); if (nf) nf.classList.add('active');
}

function showError(elId, msg) {
  const el = $(elId);
  el.textContent = msg;
  el.classList.add('visible');
  setTimeout(() => el.classList.remove('visible'), 8000);
}


/* ===== THEME TOGGLE ===== */
function applyThemeLabel() {
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  const label = document.getElementById('theme-btn-label');
  if (label) label.textContent = isLight ? 'Dark' : 'Light';
}
function toggleTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  if (next === 'light') document.documentElement.setAttribute('data-theme', 'light');
  else document.documentElement.removeAttribute('data-theme');
  try { localStorage.setItem('wt_theme', next); } catch (_) {}
  applyThemeLabel();
}
applyThemeLabel();

/* ===== i18n (FR / EN) =====
   EN is the literal text in the HTML; only FR overrides live in the dict.
   On first apply each annotated node's English original is cached so the
   toggle can restore it. Static surfaces (nav, home, legal) are covered;
   JS-built strings use t(). */
let LANG = 'en';
const I18N = {
  fr: {
    'nav.history': 'Historique',
    'nav.signin': 'Connexion',
    'nav.scan': 'Analyser',
    'No published scans yet': 'Aucun scan publié pour l\'instant',
    'Run one above. A scan stays private until you choose to publish it here.': "Lancez-en un ci-dessus. Un scan reste privé jusqu'à ce que vous choisissiez de le publier ici.",
    'home.tagline': "Internet n'oublie jamais.",
    'home.sub': "Outil d'OSINT pour chercheurs et professionnels. Révélez ce qu'un domaine a exposé au fil du temps (e-mails, sous-domaines, technos, fuites) depuis les archives de la <a href=\"https://web.archive.org\" target=\"_blank\" rel=\"noopener\">Wayback Machine</a>.",
    'home.scan': 'Analyser',
    'home.publish': 'Publier dans le flux public à la fin de ce scan',
    'home.adv.summary': 'Pré-filtres (optionnel)',
    'home.adv.exclude': 'Exclure les URL contenant',
    'home.adv.daterange': 'Plage de dates',
    'home.adv.hint': "Les sous-domaines et la densité des snapshots se choisissent à l'étape suivante, une fois archive.org interrogé pour ce domaine.",
    'home.hint': 'Appuyez sur <kbd>Entrée</kbd> pour choisir les sous-domaines, les dates et la densité avant de lancer.',
    'home.cap.identities': 'Identités',
    'home.cap.identities.d': 'E-mails, personnes, comptes apparus au fil du temps.',
    'home.cap.infra': 'Infrastructure',
    'home.cap.infra.d': 'Sous-domaines, stack technique, endpoints, en-têtes.',
    'home.cap.timeline': 'Chronologie',
    'home.cap.timeline.d': 'Quand chaque élément est apparu, et quand il a disparu.',
    'home.caption': 'Données publiques uniquement &middot; <a href="#/legal">Mentions légales</a>',
    'home.version': 'WayTrace v1.6.0 &middot; hébergé &middot; <a href="https://github.com/HXLLO/WayTrace" target="_blank" rel="noopener">source</a>',
    'home.archivedby': 'Archives par',
    'Pages read from': 'Pages lues depuis',
    'Querying archive.org': 'Interrogation archive.org',
    'Selecting snapshots': 'Sélection des snapshots',
    'Fetching pages': 'Récupération des pages',
    'Extracting & cross-referencing': 'Extraction & recoupement',
    'Extracting & cross-referencing…': 'Extraction & recoupement…',
    'findings so far': 'résultats trouvés',
    // Report 2.0
    'Categories': 'Catégories',
    'Activity': 'Activité',
    'Found': 'Trouvées',
    'Show all': 'Tout afficher',
    'empty categories (searched)': 'catégories vides (cherchées)',
    'Views': 'Vues',
    'tick': 'coche',
    'Pivots from ticked categories': 'Pivots des catégories cochées',
    'Search pivots…': 'Chercher un pivot…',
    'No pivot matches.': 'Aucun pivot correspondant.',
    'Tick a category above to pick pivots from its values.': 'Cochez une catégorie ci-dessus pour choisir des pivots parmi ses valeurs.',
    'email, subdomain, tech…': 'email, sous-domaine, techno…',
    'any word in the archived HTML…': "n'importe quel mot du HTML archivé…",
    'Loading your scans…': 'Chargement de vos scans…',
    'public': 'public',
    'No findings': 'Aucun résultat',
    'WayTrace searched all 43 categories across {n} archived pages and found nothing to extract.': 'WayTrace a cherché dans les 43 catégories sur {n} pages archivées et n\'a rien trouvé à extraire.',
    'value': 'valeur',
    'occ.': 'occ.',
    'seen': 'vu de → à',
    'source': 'source',
    'shown': 'affichés',
    'copy column': 'copier la colonne',
    'values': 'valeurs',
    'Searched across every snapshot, found nothing in this category.': 'Cherché sur tous les snapshots, rien trouvé dans cette catégorie.',
    'Showing first': 'Affiche les',
    'of': 'sur',
    'Activity of': 'Activité de',
    'when each value was visible': 'quand chaque valeur était visible',
    'Composed activity': 'Activité composée',
    'categories': 'catégories',
    'pivots': 'pivots',
    'untick to remove a lane': 'décoche pour retirer un couloir',
    'category': 'catégorie',
    'pivot': 'pivot',
    'appeared': 'apparu',
    'disappeared': 'disparu',
    'last capture': 'dernière capture',
    'Favicon over time': 'Favicon dans le temps',
    'Tick categories or pivots on the left to build a timeline.': 'Cochez des catégories ou des pivots à gauche pour construire une frise.',
    'Copy': 'Copier',
    "Couldn't load recent scans": 'Impossible de charger les scans récents',
    'Check your connection and try again.': 'Vérifiez votre connexion et réessayez.',
    'Retry': 'Réessayer',
    'Scan complete': 'Scan terminé',
    'This domain was already scanned recently. Showing that scan.': 'Ce domaine a déjà été scanné récemment, affichage de ce scan.',
    'Filter extracted results': 'Filtrer les résultats extraits',
    'Search the archived pages': 'Chercher dans les pages archivées',
    'Copied': 'Copié',
    'home.provenance': "Outil OSINT open source. La version hébergée limite le nombre de snapshots par scan ; auto-hébergez-la depuis GitHub pour analyser un domaine en entier.",
    'home.ethic': "Conçu pour les chercheurs en sécurité, les équipes, les journalistes et les professionnels curieux. Utilisez ce que vous trouvez de façon responsable : signalez les risques aux personnes qui possèdent les données, jamais contre elles.",
    'home.feed.title': 'Scans publiés récemment',
    'home.mrp.all': 'Toutes les dates',
    'mrp.all': 'Tout',
    'mrp.12m': '12 derniers mois',
    'mrp.24m': '24 derniers mois',
    'mrp.ytd': 'Cette année',
    // Legal page
    'legal.title': 'Mentions légales, licence et usage acceptable',
    'legal.updated': 'Dernière mise à jour 2026-06 · WayTrace',
    'legal.note': "WayTrace est un outil OSINT <strong>passif</strong>. Il lit uniquement ce que l'Internet Archive (Wayback Machine) a <strong>déjà</strong> archivé publiquement. Il n'effectue <strong>aucun scan actif, sondage ou connexion</strong> sur un site cible, n'envoie aucun trafic vers la cible, et n'ajoute rien qui n'était pas déjà public. Cette page est fournie à titre de transparence et ne constitue pas un avis juridique.",
    'legal.h1': '1. Ce que fait WayTrace',
    'legal.p1': "WayTrace reconstruit l'histoire publique d'un domaine à partir des snapshots stockés par <a href=\"https://web.archive.org\" target=\"_blank\" rel=\"noopener\">archive.org</a>. Il récupère un échantillon représentatif de ces pages archivées et en extrait des signaux (technologies, endpoints, liens, identifiants, etc.) avec les dates où ils ont été vus. Toutes les données source étaient déjà publiques et archivées par un tiers avant tout scan. WayTrace est indépendant et non affilié à l'Internet Archive.",
    'legal.h2': '2. Usage autorisé',
    'legal.p2': 'WayTrace est destiné à des usages licites uniquement, notamment :',
    'legal.p2.li1': "l'éducation et l'apprentissage du renseignement en sources ouvertes ;",
    'legal.p2.li2': 'la recherche en sécurité autorisée et la sécurité défensive (vos propres actifs, ou avec autorisation) ;',
    'legal.p2.li3': "la due diligence, le journalisme et les enquêtes anti-fraude / menace que vous êtes en droit de mener ;",
    'legal.p2.li4': 'la recherche historique et académique sur le web public.',
    'legal.h3': '3. Usage interdit',
    'legal.p3': "Vous ne devez <strong>pas</strong> utiliser WayTrace pour :",
    'legal.p3.li1': 'traquer, harceler, intimider, divulguer (doxxing) ou mettre en danger une personne ;',
    'legal.p3.li2': "tenter un accès non autorisé, un abus d'identifiants, ou contourner une authentification ;",
    'legal.p3.li3': 'violer les lois sur la vie privée, la protection des données ou le piratage dans toute juridiction applicable ;',
    'legal.p3.li4': "mener une surveillance ou un profilage portant atteinte aux droits d'autrui ;",
    'legal.p3.li5': 'toute autre activité illégale ou abusive.',
    'legal.p3b': "Les comptes utilisés pour faciliter ce qui précède peuvent être suspendus ou résiliés.",
    'legal.h4': '4. Votre responsabilité',
    'legal.p4': "Vous êtes seul responsable de l'usage que vous faites de WayTrace et de ses résultats, et du respect de toutes les lois qui vous sont applicables et applicables au sujet de votre recherche, y compris dans la juridiction du sujet. Lorsque les résultats contiennent des données personnelles, <strong>vous</strong> agissez en tant que responsable du traitement pour tout traitement ultérieur.",
    'legal.h5': '5. Données personnelles (RGPD)',
    'legal.p5': "Les pages archivées peuvent contenir des données personnelles (par exemple des adresses e-mail ou des noms). Il n'existe pas d'exemption générale pour les données personnelles publiquement disponibles au titre du RGPD. WayTrace minimise l'exposition par conception : il ne traite que des données déjà archivées publiquement, n'effectue aucun enrichissement au-delà de ces pages, conserve les scans terminés pour une durée limitée (7 jours sur le service hébergé), et s'appuie sur l'<strong>intérêt légitime</strong> (recherche en sécurité et transparence du web). Les personnes concernées peuvent demander le retrait d'un scan publié (voir Contact). Le service hébergé exige un compte pour <em>lancer</em> un scan ; la consultation d'un scan via son lien et le flux public restent ouverts.",
    'legal.h6': '6. Données source et tiers',
    'legal.p6': "Tous les snapshots proviennent de l'Internet Archive. Leur disponibilité, leur exactitude et leur exhaustivité échappent au contrôle de WayTrace ; les résultats peuvent être partiels ou périmés (lacunes d'archive). Merci de respecter également les <a href=\"https://archive.org/about/terms.php\" target=\"_blank\" rel=\"noopener\">conditions de l'Internet Archive</a>.",
    'legal.h7': '7. Licence',
    'legal.p7': "WayTrace est open source sous <strong>licence MIT</strong>. Vous pouvez l'auto-héberger ; la version auto-hébergée n'a pas de plafond de snapshots et peut analyser un domaine en intégralité. Le logiciel est fourni <strong>« EN L'ÉTAT », sans aucune garantie</strong> ; voir le fichier LICENSE du dépôt.",
    'legal.h8': '8. Clause de non-responsabilité',
    'legal.p8': "WayTrace est fourni comme une aide à la recherche, sans garantie. Dans toute la mesure permise par la loi, l'opérateur n'est pas responsable de l'usage, du mésusage ou de la confiance accordée à l'outil ou à ses résultats, ni du contenu des pages archivées.",
    'legal.h9': '9. Contact / abus / retrait',
    'legal.p9': "Signalements d'abus et demandes de retrait : <a href=\"mailto:legal@waytrace.org\">legal@waytrace.org</a>. Nous examinons les demandes légitimes et pouvons dépublier ou supprimer un scan.",
    'legal.back': 'Retour à WayTrace',
    // --- Auth modal + account (English text used as the key) ---
    'Email': 'E-mail',
    'Email me a sign-in link': 'Recevoir un lien de connexion par e-mail',
    'No password. We send a one-tap link to your inbox.': 'Sans mot de passe. Nous envoyons un lien en un clic dans votre boîte.',
    'or use a password': 'ou utiliser un mot de passe',
    'Continue with password': 'Continuer avec un mot de passe',
    'New here? Either option creates your account. We email a verification link you can confirm anytime.': "Nouveau ici ? Chaque option crée votre compte. Nous envoyons un lien de vérification que vous pouvez confirmer à tout moment.",
    'Check your inbox': 'Vérifiez votre boîte de réception',
    'Use a different email': 'Utiliser une autre adresse',
    'Password (min 8 characters)': 'Mot de passe (8 caractères min)',
    'Sign in': 'Connexion',
    'Sign in or create an account': 'Connectez-vous ou créez un compte',
    'Create your account to scan': 'Créez votre compte pour analyser',
    'Scanning is free. An account just keeps your scans tied to you and lets you choose which stay public.': "L'analyse est gratuite. Le compte relie simplement vos scans à vous et vous laisse choisir lesquels restent publics.",
    'Sign in or create your account': 'Connectez-vous ou créez votre compte',
    'One account to run scans, keep your history, and choose which results stay public.': 'Un compte pour lancer des scans, garder votre historique et choisir quels résultats restent publics.',
    // --- Scope / scan journey (static labels) ---
    'Subdomains': 'Sous-domaines',
    'filter subdomains…': 'filtrer les sous-domaines…',
    'All / none': 'Tout / rien',
    'Pages': 'Pages',
    'most-archived paths. untick to skip a noisy section': 'pages les plus archivées. décochez pour ignorer une section bruyante',
    'filter pages…': 'filtrer les pages…',
    'Timeline & density': 'Chronologie et densité',
    'click a year, then another, to set a range': 'cliquez sur une année, puis une autre, pour définir une plage',
    'Pick exact months': 'Choisir des mois précis',
    'Pick exact dates': 'Choisir des dates précises',
    'snapshots per day': 'snapshots par jour',
    'all archived days': 'tous les jours archivés',
    'Done': 'Terminé',
    'Mo': 'Lu', 'Tu': 'Ma', 'We': 'Me', 'Th': 'Je', 'Fr': 'Ve', 'Sa': 'Sa', 'Su': 'Di',
    'January': 'Janvier', 'February': 'Février', 'March': 'Mars', 'April': 'Avril',
    'May': 'Mai', 'June': 'Juin', 'July': 'Juillet', 'August': 'Août',
    'September': 'Septembre', 'October': 'Octobre', 'November': 'Novembre', 'December': 'Décembre',
    'all dates': 'toutes les dates', 'snapshots': 'snapshots', 'est.': 'est.', 'density': 'densité',
    'good coverage': 'bonne couverture',
    'thin coverage, raise density or range': 'couverture faible, augmentez la densité ou la plage',
    'sampled to fit the cap': 'échantillonné pour tenir dans le plafond',
    'More density = more snapshots = longer scan.': 'Plus de densité = plus de snapshots = scan plus long.',
    // --- History rows + results meta ---
    'No scans yet.': 'Aucun scan pour le moment.',
    'Run a scan': 'Lancer un scan',
    'Public': 'Public', 'Private': 'Privé',
    'completed': 'terminé', 'running': 'en cours', 'failed': 'échec',
    'queued': 'en file', 'cancelled': 'annulé', 'pending': 'en attente',
    'findings': 'résultats', 'snapshots analysed': 'snapshots analysés', 'pages scraped': 'pages récupérées',
    'distinct': 'distincts', 'archived': 'archivés', 'of': 'sur',
    'Download HTML': 'Télécharger HTML', 'Copy link': 'Copier le lien',
    'Scan more': 'Scanner plus',
    'In queue': 'En file d\'attente',
    'Position in queue': 'Position dans la file',
    'Estimated wait:': 'Attente estimée :',
    'Starting shortly…': 'Démarrage imminent…',
    'Cancel my spot': 'Annuler ma place',
    'Scanning': 'Analyse en cours',
    'Preparing scan…': 'Préparation du scan…',
    'estimating…': 'estimation…',
    'Scraped {done} / {total} archived pages': '{done} / {total} pages archivées récupérées',
    '~{s}s left': '~{s}s restantes',
    '~{m} min left': '~{m} min restantes',
    'Copy link': 'Copier le lien',
    'Private': 'Privé',
    'archived': 'archivés',
    'density': 'densité',
    'expires': 'expire',
    'more': 'de plus',
    'of': 'sur',
    'pages scraped': 'pages récupérées',
    'snapshots analysed': 'snapshots analysés',
    'Archive.org is refusing connections from this server (it looks IP-blocked). Scanning is paused for about {n} min to let it recover.': "Archive.org refuse les connexions depuis ce serveur (IP vraisemblablement bloquée). Les scans sont en pause pendant environ {n} min, le temps que ça se rétablisse.",
    'Scanning is paused for about {s}s: archive.org is rate-limiting us. Please retry in a moment.': "Scans en pause pendant environ {s}s : archive.org nous limite. Réessayez dans un instant.",
    'Archive.org is slow right now; scans may take longer than usual.': "Archive.org est lent en ce moment ; les scans peuvent prendre plus de temps que d'habitude.",
    'Search': 'Rechercher',
    'Search a word in the archived page content…': 'Rechercher un mot dans le contenu des pages archivées…',
    'Searching…': 'Recherche…',
    'Search failed.': 'La recherche a échoué.',
    'No pages matched.': 'Aucune page ne correspond.',
    'pivot': 'pivot',
    'Search this favicon on Shodan': 'Chercher ce favicon sur Shodan',
    'Leaks & secrets': 'Fuites et secrets',
    'Pivots': 'Pivots',
    'Context': 'Contexte',
    'Other signals': 'Autres signaux',
    'All & searched': 'Tout et recherché',
    'categories searched, nothing found (greyed above)': 'catégories recherchées, sans résultat (grisées ci-dessus)',
    'Loading…': 'Chargement…',
    'Run a denser scan of this domain, reusing what was already found': 'Relancer un scan plus dense de ce domaine, en réutilisant ce qui a déjà été trouvé',
    'Something went wrong. Please try again.': 'Une erreur est survenue. Réessayez.',
    'Filter the table to': 'Filtrer la table sur', 'more': 'autres',
    'Publish to feed': 'Publier dans le flux', 'public': 'public', 'expires': 'expire',
    'Copied ✓': 'Copié ✓',
    'Density': 'Densité',
    'Full range': 'Plage complète',
    'Exclude URLs': 'Exclure des URL',
    'drop noisy paths by keyword (e.g. a whole blog)': 'écartez les chemins bruyants par mot-clé (ex. tout un blog)',
    'type a word, press Enter, e.g. blog': 'tapez un mot, Entrée, ex. blog',
    'Add': 'Ajouter',
    'Launch scan': 'Lancer le scan',
    'Tune the scan before launching it.': 'Réglez le scan avant de le lancer.',
    'Querying archive.org for subdomains...': 'Interrogation d’archive.org pour les sous-domaines...',
    // Publish / email checkboxes (split into bold + small)
    'Publish to the public feed when done': 'Publier dans le flux public à la fin',
    'Off by default. Your scan stays private, only people with the link can see it. Tick to publish it to the public feed.': 'Désactivé par défaut. Votre scan reste privé, seules les personnes avec le lien peuvent le voir. Cochez pour le publier dans le flux public.',
    'Delete scan': 'Supprimer le scan',
    'Delete this scan permanently? This cannot be undone.': 'Supprimer définitivement ce scan ? Cette action est irréversible.',
    "Email me when it's done": 'Me prévenir par e-mail à la fin',
    'Off by default. We send a link to your account email once the scan finishes, handy for long scans.': 'Désactivé par défaut. Nous envoyons un lien à l’adresse de votre compte une fois le scan terminé, pratique pour les longs scans.',
    // --- Scope dynamic (density labels/hints) ---
    'Light': 'Léger', 'Fast': 'Rapide', 'Balanced': 'Équilibré', 'Dense': 'Dense', 'Deep': 'Profond', 'Max': 'Max',
    '~2 snapshots/year, quick skim': '~2 snapshots/an, survol rapide',
    '~6/year, fast overview': '~6/an, aperçu rapide',
    '~12/year, recommended': '~12/an, recommandé',
    '~24/year, thorough': '~24/an, approfondi',
    '~50/year, heavy': '~50/an, lourd',
    // --- Results + history chrome ---
    'Timeline': 'Chronologie',
    'Export': 'Exporter',
    'Categories': 'Catégories',
    'Activity': 'Activité',
    'Pivots': 'Pivots',
    'Leak': 'Fuite', 'Context': 'Contexte', 'Background': 'Arrière-plan',
    'My scans': 'Mes scans',
    'New Scan': 'Nouveau scan',
    'Analyzed': 'Analysé',
    'Collecting': 'En cours',
    'Failed': 'Échec',
    // --- Empty-state banner + fallback (dynamic) ---
    'No Wayback Machine data for this domain.': "Aucune donnée Wayback Machine pour ce domaine.",
    'No findings extracted.': 'Aucun résultat extrait.',
    'The Internet Archive has no archived HTML snapshots for this domain, so there is nothing to analyse. This is not an error: the domain may be too new, never crawled, or excluded from archive.org.': "L'Internet Archive n'a aucun snapshot HTML archivé pour ce domaine : il n'y a donc rien à analyser. Ce n'est pas une erreur : le domaine peut être trop récent, jamais exploré, ou exclu d'archive.org.",
    'Archived pages were analysed but no signals matched any category. Try a wider date range or a denser snapshot selection.': "Des pages archivées ont été analysées mais aucun signal ne correspond à une catégorie. Essayez une plage de dates plus large ou une sélection plus dense.",
    'Could not enumerate subdomains': "Impossible d'énumérer les sous-domaines",
    'No archived pages found for this domain.': "Aucune page archivée trouvée pour ce domaine.",
    // --- Category labels (English value used as key) ---
    'Emails': 'E-mails',
    'API keys': 'Clés API',
    'JWT tokens': 'Jetons JWT',
    'Internal IPs': 'IP internes',
    'Connection strings': 'Chaînes de connexion',
    'Hidden form fields': 'Champs de formulaire cachés',
    'Hosting providers': 'Hébergeurs',
    'Tech stack': 'Stack technique',
    'Analytics & trackers': 'Analytics & traqueurs',
    'Analytics IDs': 'ID analytics',
    'Ad IDs': 'ID publicitaires',
    'Favicons': 'Favicons',
    'Meta tags': 'Méta-tags',
    'HTML titles': 'Titres HTML',
    'Outgoing links': 'Liens sortants',
    'Iframe sources': 'Sources iframe',
    'Linked documents (PDF, etc.)': 'Documents liés (PDF, etc.)',
    'Endpoints': 'Endpoints',
    'JavaScript URLs': 'URL JavaScript',
    'Asset files': 'Fichiers assets',
    'HTML comments': 'Commentaires HTML',
    'Social profiles': 'Profils sociaux',
    'GitHub repositories': 'Dépôts GitHub',
    'Named persons': 'Personnes nommées',
    'Organizations': 'Organisations',
    'Sitemaps & robots': 'Sitemaps & robots',
    'PGP keys': 'Clés PGP',
    'French business IDs': 'Identifiants entreprise FR',
    'Captcha providers': 'Fournisseurs de captcha',
    'Auth providers': "Fournisseurs d'authentification",
    'Cookie consent': 'Consentement cookies',
    'Bug bounty programs': 'Programmes bug bounty',
    'RSS feeds': 'Flux RSS',
    'JSON-LD structured data': 'Données structurées JSON-LD',
    'Status pages': 'Pages de statut',
    'Verification tags': 'Balises de vérification',
    'Job boards': "Sites d'emploi",
    'Phone numbers': 'Numéros de téléphone',
    'Crypto wallets': 'Portefeuilles crypto',
    'Directory listings': 'Listings de répertoires',
    'HTTP headers': 'En-têtes HTTP',
    // --- Findings count + generic description ---
    'finding': 'résultat', 'findings': 'résultats',
    'Every signal extracted across the archived history. Pick a category above to see what each pivot is and focus the table.': "Tous les signaux extraits de l'historique archivé. Choisissez une catégorie ci-dessus pour voir ce qu'est chaque pivot et filtrer la table.",
    // --- Category descriptions ---
    'Email addresses found in pages. Named mailboxes (jane.doe@) beat generic info@/contact@; pivot on breaches and social.': "Adresses e-mail trouvées dans les pages. Les boîtes nominatives (jean.dupont@) valent mieux que info@/contact@ ; pivotez sur les fuites et les réseaux sociaux.",
    'Subdomains seen in links, scripts and content. Expands attack surface and reveals internal/infra naming.': "Sous-domaines vus dans les liens, scripts et contenus. Élargit la surface d'attaque et révèle le nommage interne / infra.",
    'Exposed API keys and secret tokens (AWS, Stripe, Google, GitHub, Slack, OpenAI...). High-value leaks.': "Clés API et jetons secrets exposés (AWS, Stripe, Google, GitHub, Slack, OpenAI...). Fuites à forte valeur.",
    'Cloud storage buckets (S3, GCS, Azure, DO Spaces). May expose files and reveal infra ownership.': "Buckets de stockage cloud (S3, GCS, Azure, DO Spaces). Peuvent exposer des fichiers et révéler le propriétaire de l'infra.",
    'Connection strings with embedded credentials (mysql://, postgres://, mongodb://, redis://...).': "Chaînes de connexion avec identifiants intégrés (mysql://, postgres://, mongodb://, redis://...).",
    'Open directory listings (auto-index pages) that enumerate files served on the host.': "Listings de répertoires ouverts (pages d'auto-index) qui énumèrent les fichiers servis par l'hôte.",
    'Private/internal IPs (RFC1918, link-local, CGNAT) leaked in markup. Hints at internal topology.': "IP privées / internes (RFC1918, link-local, CGNAT) laissées dans le code. Indices sur la topologie interne.",
    'JSON Web Tokens in cookies, storage or markup. Decode for user, role and issuer hints.': "Jetons JWT dans les cookies, le stockage ou le code. Décodez-les pour des indices sur l'utilisateur, le rôle et l'émetteur.",
    'Named individuals from bylines, meta and JSON-LD. Pivot to LinkedIn, breaches and org charts.': "Personnes nommées dans les signatures, méta et JSON-LD. Pivotez vers LinkedIn, les fuites et les organigrammes.",
    'Analytics, ads and measurement IDs (GA4, UA, GTM, Meta Pixel, Hotjar, Matomo, Segment...). The same ID across sites means the same operator.': "ID analytics, pub et mesure (GA4, UA, GTM, Meta Pixel, Hotjar, Matomo, Segment...). Un même ID sur plusieurs sites = même opérateur.",
    'AdSense publisher IDs. Cluster sites sharing one ad account (publicwww, spyonweb).': "ID éditeur AdSense. Regroupez les sites partageant un même compte pub (publicwww, spyonweb).",
    'Domain-verification tokens (Google, Microsoft, Facebook...). Tie the domain to registrant accounts.': "Jetons de vérification de domaine (Google, Microsoft, Facebook...). Relient le domaine aux comptes du déclarant.",
    'Crypto wallet addresses (BTC, ETH, XMR, LTC...). Trace on-chain; address reuse links operators.': "Adresses de portefeuilles crypto (BTC, ETH, XMR, LTC...). Traçables on-chain ; la réutilisation d'adresse relie les opérateurs.",
    'Favicon URLs and hashes. Pivot identical favicons across hosts via Shodan/Censys.': "URL et hash de favicons. Pivotez sur des favicons identiques entre hôtes via Shodan/Censys.",
    'URL paths and endpoints (/api, /admin, /login...). Maps the app surface and sensitive routes.': "Chemins et endpoints (/api, /admin, /login...). Cartographie la surface de l'app et les routes sensibles.",
    'Hidden form inputs (CSRF tokens, workflow state, internal IDs) left in markup.': "Champs de formulaire cachés (jetons CSRF, état de workflow, ID internes) laissés dans le code.",
    'URLs referenced inside JavaScript (API bases, internal/staging/debug paths).': "URL référencées dans le JavaScript (bases d'API, chemins internes/staging/debug).",
    'Measurement IDs (GA4, UA, Hotjar, Matomo, Segment...) for cross-site operator correlation.': "ID de mesure (GA4, UA, Hotjar, Matomo, Segment...) pour corréler les opérateurs entre sites.",
    'Consent platform (Cookiebot, OneTrust...) account IDs that cluster sites run by one operator.': "ID de compte des plateformes de consentement (Cookiebot, OneTrust...) qui regroupent les sites d'un même opérateur.",
    'Referenced GitHub repos and users. Pivot to commits, contributors and the owning org.': "Dépôts et utilisateurs GitHub référencés. Pivotez vers les commits, contributeurs et l'organisation propriétaire.",
    'PGP public keys, fingerprints, key IDs and keybase handles. Look them up on keyservers.': "Clés publiques PGP, empreintes, key IDs et pseudos keybase. Recherchez-les sur les serveurs de clés.",
    'Hosted status pages (statuspage.io, instatus...). Reveal infra and incident history.': "Pages de statut hébergées (statuspage.io, instatus...). Révèlent l'infra et l'historique des incidents.",
    'ATS and career boards (Greenhouse, Lever, Ashby...) carrying the company slug.': "ATS et sites carrière (Greenhouse, Lever, Ashby...) portant le slug de l'entreprise.",
    'Identity providers (Auth0, Okta, Cognito, Keycloak...) and their tenant slugs.': "Fournisseurs d'identité (Auth0, Okta, Cognito, Keycloak...) et leurs slugs de tenant.",
    'French business IDs (SIREN, SIRET, TVA, RCS, RNCP). Link the site to a legal entity.': "Identifiants d'entreprise français (SIREN, SIRET, TVA, RCS, RNCP). Relient le site à une entité légale.",
    'Detected CMS, frameworks and libraries, and how the stack changed over time.': "CMS, frameworks et bibliothèques détectés, et l'évolution de la stack dans le temps.",
    'Hosting, CDN and infra providers inferred from headers and assets.': "Hébergeur, CDN et fournisseurs d'infra déduits des en-têtes et des assets.",
    'Meta tags: description, author, generator, robots and Open Graph.': "Méta-tags : description, auteur, generator, robots et Open Graph.",
    'HTML <title> text over time: how the page title changed across snapshots (rebrands, owners, focus shifts).': "Texte du <title> HTML dans le temps : évolution du titre de page entre snapshots (rebrandings, propriétaires, changements de cap).",
    'Original HTTP response headers preserved by Wayback (Server, X-Powered-By, CSP, Set-Cookie names...).': "En-têtes de réponse HTTP d'origine conservés par Wayback (Server, X-Powered-By, CSP, noms Set-Cookie...).",
    'Embedded iframe sources: third-party widgets and embedded apps.': "Sources d'iframe intégrées : widgets tiers et applications embarquées.",
    'Linked documents (PDF, DOCX, XLSX...), often carrying metadata and internal info.': "Documents liés (PDF, DOCX, XLSX...), portant souvent des métadonnées et des infos internes.",
    'Phone numbers found across the archived pages.': "Numéros de téléphone trouvés dans les pages archivées.",
    'Organizations declared in JSON-LD / structured data.': "Organisations déclarées en JSON-LD / données structurées.",
    'Postal addresses declared in JSON-LD / structured data.': "Adresses postales déclarées en JSON-LD / données structurées.",
    'RSS/Atom feeds. Publication cadence and author cross-reference.': "Flux RSS/Atom. Cadence de publication et recoupement d'auteurs.",
    'sitemap.xml, robots.txt and .well-known files. Site structure and otherwise-hidden paths.': "Fichiers sitemap.xml, robots.txt et .well-known. Structure du site et chemins autrement cachés.",
    'Bug-bounty and disclosure references (HackerOne, Bugcrowd, security.txt). Security contacts.': "Références bug-bounty et divulgation (HackerOne, Bugcrowd, security.txt). Contacts sécurité.",
    'CAPTCHA providers and site keys (reCAPTCHA, hCaptcha, Turnstile, Arkose/FunCaptcha, GeeTest, AWS WAF, Friendly Captcha).': "Fournisseurs de CAPTCHA et clés de site (reCAPTCHA, hCaptcha, Turnstile, Arkose/FunCaptcha, GeeTest, AWS WAF, Friendly Captcha).",
    'External domains linked from the site. Useful for relationship mapping.': "Domaines externes liés depuis le site. Utile pour cartographier les relations.",
    'Linked social-media profiles.': "Profils de réseaux sociaux liés.",
    'HTML comments in source. Often leak tooling, TODOs and internal notes.': "Commentaires HTML dans le source. Révèlent souvent l'outillage, des TODO et des notes internes.",
    'Static asset files (JS, CSS, images) referenced by the site.': "Fichiers assets statiques (JS, CSS, images) référencés par le site.",
    // --- Finding drawer + tab empty states ---
    'Source page': 'Page source',
    'Co-occurring on same page': 'Co-occurrence sur la même page',
    'No other findings share this source page.': "Aucun autre résultat ne partage cette page source.",
    'No source page recorded for this finding (mined from the archive index, or an older scan).': "Aucune page source enregistrée pour ce résultat (extrait de l'index d'archive, ou ancien scan).",
    'Hashes': 'Empreintes',
    'No subdomains found in the archive.': "Aucun sous-domaine trouvé dans l'archive.",
    'No pivots to graph yet': 'Aucun pivot à représenter pour le moment',
  },
};

function t(key) {
  if (LANG === 'fr' && I18N.fr[key] !== undefined) return I18N.fr[key];
  return key;  // callers pass an English fallback only where noted
}

function _i18nApplyAttr(attr, prop) {
  document.querySelectorAll('[' + attr + ']').forEach(el => {
    const k = el.getAttribute(attr);
    const cacheKey = 'i18nOrig_' + attr.replace(/[^a-z]/gi, '');
    if (el.dataset[cacheKey] === undefined) {
      el.dataset[cacheKey] = prop === 'innerHTML' ? el.innerHTML
        : (prop === 'textContent' ? el.textContent : (el.getAttribute(prop) || ''));
    }
    const fr = LANG === 'fr' ? I18N.fr[k] : undefined;
    const val = fr !== undefined ? fr : el.dataset[cacheKey];
    if (prop === 'innerHTML') el.innerHTML = val;
    else if (prop === 'textContent') el.textContent = val;
    else el.setAttribute(prop, val);
  });
}

function applyI18n() {
  _i18nApplyAttr('data-i18n', 'textContent');
  _i18nApplyAttr('data-i18n-html', 'innerHTML');
  _i18nApplyAttr('data-i18n-ph', 'placeholder');
  _i18nApplyAttr('data-i18n-title', 'title');
}

function setLang(l) {
  LANG = (l === 'fr') ? 'fr' : 'en';
  try { localStorage.setItem('wt_lang', LANG); } catch (_) {}
  document.documentElement.lang = LANG;
  document.querySelectorAll('#lang-switch .lang-opt').forEach(o => {
    const on = o.getAttribute('data-lang') === LANG;
    o.classList.toggle('active', on);
    o.setAttribute('aria-pressed', String(on));
  });
  applyI18n();
  // Re-render the few dynamic strings that JS sets directly.
  try { if (typeof renderAccountControl === 'function') renderAccountControl(); } catch (_) {}
  try {
    const adv = document.getElementById('scope-adv');
    if (adv && adv.style.display !== 'none' && typeof onScopeDensity === 'function') {
      onScopeDensity();        // density label/hint + estimate
    }
  } catch (_) {}
}
function toggleLang() { setLang(LANG === 'fr' ? 'en' : 'fr'); }
function initLang() {
  let l = null;
  try { l = localStorage.getItem('wt_lang'); } catch (_) {}
  if (!l) l = (navigator.language || '').toLowerCase().startsWith('fr') ? 'fr' : 'en';
  setLang(l);
}

/* ===== ROUTER ===== */
function navigate(hash) {
  const parts = (hash || '#/').replace('#/', '').split('/').filter(Boolean);
  // v2 public scan route: #/s/{url_id}
  let view = parts[0] === 's' ? 'scan-public' : (parts[0] || 'home');
  const valid = new Set(['home', 'scope', 'history', 'scan-public', 'legal']);
  if (!valid.has(view)) view = 'notfound';
  // 'results' stays here (not in `valid`): the public flow reuses view-results,
  // so navigate() must still deactivate it when leaving, even though there is no
  // longer a /#/results route.
  const views = ['home', 'scope', 'results', 'history', 'scan-public', 'legal', 'admin', 'notfound'];

  views.forEach(v => {
    const el = $('view-' + v);
    if (el) el.classList.toggle('active', v === view);
  });

  $('history-btn').classList.toggle('active', view === 'history');
  stopPublicScanPolling();
  if (view !== 'admin') { try { _stopAdminMon(); } catch (_) {} }
  // Clear v2 public mode when navigating away from /s/{url_id}.
  if (view !== 'scan-public') v2PublicMode = false;

  if (view === 'home') {
    $('domain-input').focus();
    loadHomeFeed();
  } else if (view === 'scan-public' && parts[1]) {
    const newUrlId = decodeURIComponent(parts[1]);
    // Reset auto-publish state whenever the visible scan changes so the
    // user's intent for the previous scan never carries over.
    if (newUrlId !== publicScanUrlId) {
      publicScanAutoPublish = false;
      publicScanAutoPublishFired = false;
    }
    publicScanUrlId = newUrlId;
    publicScanLastStatus = null;
    showScanSkeleton();
    pollPublicScan();
  } else if (view === 'scope' && parts[1]) {
    loadScope(decodeURIComponent(parts[1]));
  } else if (view === 'history') {
    loadHistory();
  }
}

/* Send the user to the scope picker (#/scope/{domain}) for fine-grained
   subdomain selection before kicking off the scan. */
function goToAdvancedScope() {
  const raw = (document.querySelector('.home-search-input')?.value
            || document.getElementById('domain-input')?.value
            || '').trim().toLowerCase()
    .replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/+$/, '');
  if (!raw) {
    showToast('Type a domain first.');
    document.querySelector('.home-search-input')?.focus();
    return;
  }
  location.hash = '#/scope/' + encodeURIComponent(raw);
}

// Carried from the homepage pre-filters into the scope step (loadScope reads it).
let _pendingScopePrefill = null;

/* Homepage Scan: every scan now goes through the scope step (preflight ->
   subdomains + density + dates) instead of launching blind. The homepage
   pre-filters (exclude keywords + date range) are carried over as defaults. */
function startAdvancedScan() {
  const raw = (document.querySelector('.home-search-input')?.value
            || document.getElementById('domain-input')?.value
            || '').trim().toLowerCase()
    .replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/+$/, '');
  if (!raw) {
    showToast('Type a domain first.');
    document.querySelector('.home-search-input')?.focus();
    return;
  }
  // Minimalist homepage: no pre-filters here. Subdomains, pages, exact dates,
  // density and the publish choice are all set on the next (scope) step, which
  // keeps its own defaults (publish pre-checked).
  _pendingScopePrefill = null;
  _forceRescan = false;   // a fresh homepage scan honours the guardrail
  location.hash = '#/scope/' + encodeURIComponent(raw);
}

/* ===== MONTH-RANGE PICKER (homepage date calendar) ===== */
let mrpFrom = null;   // "YYYY-MM" | null
let mrpTo = null;     // "YYYY-MM" | null
let mrpAnchor = null; // first endpoint while selecting
let mrpHover = null;  // hovered month for range preview
let mrpViewYear = null;

function _mrpNow() { const d = new Date(); return { y: d.getFullYear(), m: d.getMonth() }; }
function _mrpKey(y, m0) { return y + '-' + String(m0 + 1).padStart(2, '0'); }
function _mrpCurKey() { const n = _mrpNow(); return _mrpKey(n.y, n.m); }
function _mrpShift(key, months) {
  const y = parseInt(key.slice(0, 4), 10), m = parseInt(key.slice(5, 7), 10) - 1;
  const t = y * 12 + m + months;
  return _mrpKey(Math.floor(t / 12), ((t % 12) + 12) % 12);
}

function mrpToggle() {
  const pop = document.getElementById('home-mrp-pop');
  const field = document.getElementById('home-mrp-field');
  if (!pop) return;
  const open = pop.hasAttribute('hidden');
  if (open) {
    mrpViewYear = parseInt((mrpTo || mrpFrom || _mrpCurKey()).slice(0, 4), 10);
    mrpRender();
    pop.removeAttribute('hidden');
    field?.setAttribute('aria-expanded', 'true');
  } else {
    mrpClose();
  }
}
function mrpClose() {
  const pop = document.getElementById('home-mrp-pop');
  if (pop) pop.setAttribute('hidden', '');
  document.getElementById('home-mrp-field')?.setAttribute('aria-expanded', 'false');
  mrpAnchor = null; mrpHover = null;
}

function mrpNudgeYear(delta) { mrpViewYear += delta; mrpRender(); }

function _mrpBounds() {
  // Returns [lo, hi] keys for highlighting (incl. hover preview), or null.
  if (mrpAnchor && !mrpTo) {
    const other = mrpHover || mrpAnchor;
    return [mrpAnchor < other ? mrpAnchor : other, mrpAnchor < other ? other : mrpAnchor];
  }
  if (mrpFrom && mrpTo) return [mrpFrom, mrpTo];
  if (mrpFrom) return [mrpFrom, mrpFrom];
  return null;
}

function mrpRender() {
  const cal = document.getElementById('home-mrp-cal');
  if (!cal) return;
  const cur = _mrpCurKey();
  const bounds = _mrpBounds();
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  let cells = '';
  for (let m = 0; m < 12; m++) {
    const key = _mrpKey(mrpViewYear, m);
    const future = key > cur;
    let cls = 'mrp-m';
    if (future) cls += ' disabled';
    else if (bounds) {
      if (key === bounds[0] || key === bounds[1]) cls += ' end';
      else if (key > bounds[0] && key < bounds[1]) cls += ' in-range';
    }
    cells += `<button type="button" class="${cls}" ${future ? 'disabled' : ''}
        onclick="mrpPickMonth('${key}')" onmouseenter="mrpHoverMonth('${key}')">${months[m]}</button>`;
  }
  const nextDisabled = mrpViewYear >= _mrpNow().y ? 'disabled' : '';
  cal.innerHTML = `
    <div class="mrp-cal-head">
      <button type="button" onclick="mrpNudgeYear(-1)" aria-label="Previous year">&#8249;</button>
      <span class="mrp-cal-year">${mrpViewYear}</span>
      <button type="button" ${nextDisabled} onclick="mrpNudgeYear(1)" aria-label="Next year">&#8250;</button>
    </div>
    <div class="mrp-grid" onmouseleave="mrpHoverMonth(null)">${cells}</div>`;
}

function mrpHoverMonth(key) {
  if (mrpAnchor && !mrpTo) { mrpHover = key; mrpRender(); }
}

function mrpPickMonth(key) {
  if (key > _mrpCurKey()) return;
  if (!mrpAnchor || (mrpFrom && mrpTo)) {
    // Start a fresh selection.
    mrpAnchor = key; mrpFrom = key; mrpTo = null; mrpHover = key;
  } else {
    const lo = key < mrpAnchor ? key : mrpAnchor;
    const hi = key < mrpAnchor ? mrpAnchor : key;
    mrpFrom = lo; mrpTo = hi; mrpAnchor = null; mrpHover = null;
  }
  mrpSync(); mrpRender();
}

function mrpPreset(kind) {
  const cur = _mrpCurKey();
  if (kind === 'all') { mrpFrom = null; mrpTo = null; }
  else if (kind === '12m') { mrpFrom = _mrpShift(cur, -11); mrpTo = cur; }
  else if (kind === '24m') { mrpFrom = _mrpShift(cur, -23); mrpTo = cur; }
  else if (kind === 'ytd') { mrpFrom = _mrpNow().y + '-01'; mrpTo = cur; }
  mrpAnchor = null; mrpHover = null;
  mrpViewYear = parseInt((mrpTo || cur).slice(0, 4), 10);
  mrpSync(); mrpRender();
}

const _MRP_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
function mrpApplyManual() {
  const f = (document.getElementById('home-mrp-from')?.value || '').trim();
  const t = (document.getElementById('home-mrp-to')?.value || '').trim();
  const fv = _MRP_RE.test(f) ? f : null;
  const tv = _MRP_RE.test(t) ? t : null;
  if (fv && tv) { mrpFrom = fv < tv ? fv : tv; mrpTo = fv < tv ? tv : fv; }
  else { mrpFrom = fv; mrpTo = tv; }
  mrpAnchor = null; mrpHover = null;
  if (mrpFrom || mrpTo) mrpViewYear = parseInt((mrpTo || mrpFrom).slice(0, 4), 10);
  mrpSync(); mrpRender();
}

function mrpLabelText() {
  if (mrpFrom && mrpTo) return mrpFrom === mrpTo ? mrpFrom : (mrpFrom + ' → ' + mrpTo);
  if (mrpFrom) return 'from ' + mrpFrom;
  if (mrpTo) return 'until ' + mrpTo;
  return 'All dates';
}
function mrpSync() {
  const lab = document.getElementById('home-mrp-label');
  if (lab) lab.textContent = mrpLabelText();
  const fi = document.getElementById('home-mrp-from'); if (fi) fi.value = mrpFrom || '';
  const ti = document.getElementById('home-mrp-to'); if (ti) ti.value = mrpTo || '';
  const live = document.getElementById('home-mrp-live');
  if (live) live.textContent = (mrpFrom || mrpTo) ? ('Selected range: ' + mrpLabelText()) : 'No date filter (all archived months).';
}

// Close the picker on outside click / Escape.
document.addEventListener('click', (e) => {
  const mrp = document.getElementById('home-mrp');
  const pop = document.getElementById('home-mrp-pop');
  if (mrp && pop && !pop.hasAttribute('hidden') && !mrp.contains(e.target)) mrpClose();
  // Scope-step month calendar: close when clicking outside its wrapper.
  const scalWrap = document.getElementById('scal-field')?.closest('.scal-wrap');
  const scalPop = document.getElementById('scal-pop');
  if (scalWrap && scalPop && !scalPop.hasAttribute('hidden') && !scalWrap.contains(e.target)) scalClose();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const pop = document.getElementById('home-mrp-pop');
    if (pop && !pop.hasAttribute('hidden')) mrpClose();
    const scalPop = document.getElementById('scal-pop');
    if (scalPop && !scalPop.hasAttribute('hidden')) scalClose();
  }
});

/* ===== v2 PUBLIC FLOW ===== */

function stopPublicScanPolling() {
  if (publicScanPollTimer) {
    clearTimeout(publicScanPollTimer);
    publicScanPollTimer = null;
  }
}

async function pollPublicScan() {
  if (!publicScanUrlId) return;
  try {
    const resp = await fetch(API + '/api/s/' + encodeURIComponent(publicScanUrlId));
    if (resp.status === 404) { renderPublicScanNotFound(); return; }
    if (resp.status === 410) { renderPublicScanExpired(); return; }
    if (!resp.ok) {
      // Transient backend hiccup (502/503/429 from Caddy under load, etc.).
      // A long scan keeps running server-side; if we stop polling here the
      // view freezes on the last % forever even though the scan completes.
      // Keep polling so the page advances to results on its own.
      publicScanPollTimer = setTimeout(pollPublicScan, 3000);
      return;
    }
    const job = await resp.json();
    renderPublicScan(job);
    const next = (job.status === 'queued') ? 2000 : (job.status === 'running' ? 1000 : null);
    if (next) {
      publicScanPollTimer = setTimeout(pollPublicScan, next);
      return;
    }
    // Status is terminal (completed/failed). Honour the user's
    // upfront auto-publish choice exactly once.
    if (job.status === 'completed' && publicScanAutoPublish && !publicScanAutoPublishFired) {
      publicScanAutoPublishFired = true;
      try {
        await fetch(API + '/api/s/' + encodeURIComponent(publicScanUrlId) + '/publish', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({published: true}),
        });
        // Re-render with is_published=1 so the control shows the read-only "public" badge.
        renderV2InLegacyView({...job, is_published: 1});
        showToast('Published to feed.');
      } catch (_) {
        showToast('Auto-publish failed. Use the Publish button manually.');
      }
    }
  } catch (e) {
    // Network blip (lost wifi, sleep/resume, archive.org-driven backend stall).
    // Do not kill the loop: a finished scan would otherwise never show up.
    publicScanPollTimer = setTimeout(pollPublicScan, 3000);
  }
}

// Placeholder shown the instant a scan link opens, so a deep-link never flashes
// a blank card while the first /api/s fetch is in flight. Replaced by the real
// render on the first response.
function showScanSkeleton() {
  const dom = $('public-scan-domain'); if (dom) dom.innerHTML = '<span class="skel skel-title"></span>';
  const meta = $('public-scan-meta'); if (meta) meta.innerHTML = '<span class="skel skel-line"></span>';
  const actions = $('public-scan-actions'); if (actions) actions.style.display = 'none';
  const body = $('public-scan-body');
  if (body) {
    body.innerHTML = '<div class="skel-wrap" aria-hidden="true"><span class="skel skel-row"></span>'
      + '<div class="skel-grid">' + '<span class="skel skel-tile"></span>'.repeat(8) + '</div></div>';
  }
}

// Live progress state for the running scan: drives a monotonic percentage and
// an ETA derived from the REAL page-completion rate (not a hardcoded guess).
let _runStats = null;

function _fmtEtaSecs(secs) {
  if (secs < 60) return t('~{s}s left').replace('{s}', secs);
  return t('~{m} min left').replace('{m}', Math.max(1, Math.round(secs / 60)));
}

// The four honest phases of a scan, mapped from the backend's `step` string.
const SCAN_PHASES = ['Querying archive.org', 'Selecting snapshots', 'Fetching pages', 'Extracting & cross-referencing'];
function _scanPhaseIndex(step) {
  const s = (step || '').toLowerCase();
  if (s.includes('extract')) return 3;
  if (s.includes('scrap'))   return 2;
  if (s.includes('select'))  return 1;
  return 0;   // starting / fetching CDX / using selected snapshots
}
function _phasesHTML() {
  return SCAN_PHASES.map((p, i) =>
    `<span class="pub-phase" data-i="${i}"><span class="d"></span>${esc(t(p))}</span>`).join('');
}
function _updatePhases(root, idx) {
  root.querySelectorAll('.pub-phase').forEach(el => {
    const i = +el.dataset.i;
    el.classList.toggle('done', i < idx);
    el.classList.toggle('now', i === idx);
  });
}

// Live findings during the extraction phase: category chips with running counts,
// most first. Neutral "found so far", not a success verdict.
function _liveFindingsHTML(counts) {
  if (!counts || typeof counts !== 'object') return '';
  const entries = Object.entries(counts).filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!entries.length) return '';
  const total = entries.reduce((s, [, n]) => s + n, 0);
  const chips = entries.slice(0, 14).map(([cat, n]) =>
    `<span class="pub-live-chip"><b>${n}</b> ${esc(catLabel(cat))}</span>`).join('');
  return `<div class="pub-live-head">${total} ${esc(t('findings so far'))}</div>`
    + `<div class="pub-live-chips">${chips}</div>`;
}

function renderPublicScan(job) {
  $('public-scan-domain').textContent = job.domain || '';
  const meta = $('public-scan-meta');
  const status = job.status;
  const prevStatus = publicScanLastStatus;   // to detect the running -> completed moment
  publicScanLastStatus = status;
  const body = $('public-scan-body');
  const actions = $('public-scan-actions');
  if (status !== 'running') _runStats = null;   // reset between phases / scans

  if (status === 'queued') {
    actions.style.display = 'none';
    const pos = Math.max(job.position || 1, 1);
    const eta = job.eta_seconds || 0;
    const total = Math.max(job.total_in_queue || pos, pos);
    meta.textContent = t('In queue');
    body.innerHTML = `
      <div class="pub-state-card">
        <div class="pub-state-label">${esc(t('Position in queue'))}</div>
        <div class="pub-state-num">${pos}<span class="total"> / ${total}</span></div>
        <div class="pub-state-eta">${eta ? esc(t('Estimated wait:')) + ' ' + esc(formatEta(eta)) : esc(t('Starting shortly…'))}</div>
        <div class="pub-run-bar indeterminate"><div class="pub-run-bar-fill"></div></div>
        <button class="btn" style="margin-top: 28px;" onclick="cancelPublicScan()">${esc(t('Cancel my spot'))}</button>
      </div>
      ${renderPrivacyCard(job)}
    `;
    wireCopyShareLink();
  } else if (status === 'running') {
    actions.style.display = 'none';
    meta.textContent = t('Scanning');
    // Percentage from REAL work: pages scraped X/N (the scrape phase is nearly
    // all the wall-clock time). No arbitrary phase floor. Kept monotonic.
    const phaseIdx = _scanPhaseIndex(job.step);
    const now = Date.now();
    let stepTxt, pctTxt = '', etaTxt = '', fillPct = null, liveHTML = '';   // fillPct null => indeterminate
    if (phaseIdx === 3) {
      // Extraction phase: findings stream in live via job.live_counts, and the
      // bar is determinate from the backend's 75->96% progress. Check this BEFORE
      // the "X/N" match because the step reads "Extracting X/N" too.
      const p = Math.max(0, Math.min(99, Math.round(job.progress || 75)));
      stepTxt = t('Extracting & cross-referencing…');
      pctTxt = p + '%';
      fillPct = p;
      _runStats = null;   // the page-rate ETA no longer applies
    } else {
      const m = (job.step || '').match(/(\d+)\s*\/\s*(\d+)/);
      if (m) {
        const done = +m[1], total = Math.max(+m[2], 1);
        let p = Math.round((done / total) * 74);           // 0 -> 74%, real pages (extraction takes 75->96)
        if (!_runStats) _runStats = { pct: 0, rate: 0, lastDone: done, lastTime: now };
        p = Math.min(74, Math.max(_runStats.pct, p));      // never regress
        _runStats.pct = p;
        // Observed page rate (EMA) -> honest ETA.
        const dt = (now - _runStats.lastTime) / 1000;
        if (done > _runStats.lastDone && dt > 0.25) {
          const inst = (done - _runStats.lastDone) / dt;
          _runStats.rate = _runStats.rate ? _runStats.rate * 0.6 + inst * 0.4 : inst;
          _runStats.lastDone = done; _runStats.lastTime = now;
        }
        const remaining = Math.max(0, total - done);
        stepTxt = t('Scraped {done} / {total} archived pages').replace('{done}', done).replace('{total}', total);
        pctTxt = p + '%';
        etaTxt = (_runStats.rate > 0 && remaining > 0)
          ? _fmtEtaSecs(Math.round(remaining / _runStats.rate)) : t('estimating…');
        fillPct = p;
      } else {
        // Setup phase (querying archive.org, selecting): honest indeterminate bar.
        stepTxt = job.step || t('Preparing scan…');
      }
    }
    // Findings stream in as pages download (extraction overlaps the scrape), so
    // show them from whenever the backend starts pushing live counts.
    liveHTML = _liveFindingsHTML(job.live_counts);
    // Build the running scaffold ONCE, then patch only the dynamic text/width on
    // every poll. Rebuilding innerHTML each tick recreated the spinner node (its
    // rotation restarted from 0deg -> the visible stutter) and reset the bar's
    // width transition. Keeping the nodes alive lets both animate smoothly.
    let live = body.querySelector('.pub-run-live');
    if (!live) {
      body.innerHTML = `
        <div class="pub-state-card pub-run-live">
          <div class="pub-run-spinner" aria-hidden="true"></div>
          <div class="pub-phases">${_phasesHTML()}</div>
          <div class="pub-run-step"></div>
          <div class="pub-run-pct"></div>
          <div class="pub-run-bar"><div class="pub-run-bar-fill"></div></div>
          <div class="pub-run-eta"></div>
          <div class="pub-live"></div>
          <div class="pub-run-wb"><span>${esc(t('Pages read from'))}</span> <img class="wb-logo" src="/icons/wayback.svg" alt="Wayback Machine"></div>
        </div>
        ${renderPrivacyCard(job)}
      `;
      wireCopyShareLink();
      live = body.querySelector('.pub-run-live');
    }
    const stepEl = live.querySelector('.pub-run-step');
    const pctEl  = live.querySelector('.pub-run-pct');
    const barEl  = live.querySelector('.pub-run-bar');
    const fillEl = live.querySelector('.pub-run-bar-fill');
    const etaEl  = live.querySelector('.pub-run-eta');
    _updatePhases(live, phaseIdx);
    stepEl.textContent = stepTxt;
    pctEl.textContent = pctTxt;  pctEl.style.display = pctTxt ? '' : 'none';
    etaEl.textContent = etaTxt;  etaEl.style.display = etaTxt ? '' : 'none';
    const liveEl = live.querySelector('.pub-live');
    if (liveEl) { liveEl.innerHTML = liveHTML; liveEl.style.display = liveHTML ? '' : 'none'; }
    if (fillPct === null) {
      barEl.classList.add('indeterminate');
      fillEl.style.width = '';
    } else {
      barEl.classList.remove('indeterminate');
      fillEl.style.width = fillPct + '%';
    }
  } else if (status === 'completed') {
    // Clear the loading skeleton (this view is about to be hidden) and switch
    // to the rich results view via the adapter.
    if (body) body.innerHTML = '';
    if (meta) meta.innerHTML = '';
    renderV2InLegacyView(job);
    // Completion moment: if this scan was running in this session, a brief,
    // neutral toast on arrival ("Scan complete · N findings, M categories").
    if (prevStatus === 'running' || prevStatus === 'queued') {
      try {
        const res = job.results || {};
        let nf = 0, nc = 0;
        for (const k in res) {
          if (k === 'highlights' || !Array.isArray(res[k])) continue;
          if (res[k].length) { nf += res[k].length; nc += 1; }
        }
        showToast(`${t('Scan complete')} · ${nf} ${t('findings')} · ${nc} ${t('categories')}`);
      } catch (_) {}
    }
    return;
  } else if (status === 'failed' || status === 'cancelled') {
    actions.style.display = 'none';
    meta.textContent = status === 'cancelled' ? 'Cancelled' : 'Failed';
    body.innerHTML = `
      <div class="pub-error">
        ${_PUB_ERROR_ICON}
        <h2>${status === 'cancelled' ? 'Scan cancelled' : 'Scan failed'}</h2>
        <p>${esc(job.step || '')}</p>
        <a href="#/" class="btn btn-accent">Back to homepage</a>
      </div>
    `;
  }
}

const CAT_LABELS = {
  emails: 'Emails',
  subdomains: 'Subdomains',
  api_keys: 'API keys',
  jwt: 'JWT tokens',
  internal_ips: 'Internal IPs',
  connection_strings: 'Connection strings',
  hidden_fields: 'Hidden form fields',
  hosting: 'Hosting providers',
  technologies: 'Tech stack',
  analytics_trackers: 'Analytics & trackers',
  analytics_ids: 'Analytics IDs',
  adsense_ids: 'Ad IDs',
  favicons: 'Favicons',
  meta_info: 'Meta tags',
  html_titles: 'HTML titles',
  outgoing_links: 'Outgoing links',
  iframe_sources: 'Iframe sources',
  linked_documents: 'Linked documents (PDF, etc.)',
  endpoints: 'Endpoints',
  js_urls: 'JavaScript URLs',
  assets: 'Asset files',
  html_comments: 'HTML comments',
  social_profiles: 'Social profiles',
  github_repos: 'GitHub repositories',
  persons: 'Named persons',
  organizations: 'Organizations',
  sitemaps_and_robots: 'Sitemaps & robots',
  pgp_keys: 'PGP keys',
  french_business_ids: 'French business IDs',
  captcha_providers: 'Captcha providers',
  auth_providers: 'Auth providers',
  cookie_consent: 'Cookie consent',
  bug_bounty: 'Bug bounty programs',
  rss_feeds: 'RSS feeds',
  jsonld_structured: 'JSON-LD structured data',
  status_pages: 'Status pages',
  verification_tags: 'Verification tags',
  job_boards: 'Job boards',
  phones: 'Phone numbers',
  crypto: 'Crypto wallets',
  dirlist: 'Directory listings',
  http_headers: 'HTTP headers',
  jwt_tokens: 'JWT tokens',
  crypto_addresses: 'Crypto wallets',
  directory_listings: 'Directory listings',
  bug_bounty_programs: 'Bug bounty programs',
};

function catLabel(key) {
  return t(CAT_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()));
}



// Categories where displaying many items adds little (technical noise that's
// better browsed via the export). For these, cap at a smaller default.
const VERBOSE_CATS = new Set(['endpoints', 'js_urls', 'assets', 'outgoing_links', 'meta_info', 'html_titles', 'html_comments']);
// Categories folded into another at scan time. Hidden from the category grid so
// they don't show up as duplicate/empty tiles. analytics_ids -> analytics_trackers.
const MERGED_AWAY_CATS = new Set(['analytics_ids']);


function wireCatToggles() {
  // Auto-open the first category if no highlights so the user sees data immediately
  const cats = document.querySelectorAll('#public-scan-body .pub-cat');
  if (cats.length && !document.querySelector('#public-scan-body .pub-highlights')) {
    cats[0].classList.add('open');
  }
}

function wireCopyButtons() {
  document.querySelectorAll('#public-scan-body .pub-copy-btn').forEach(btn => {
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const v = btn.getAttribute('data-copy');
      try {
        await navigator.clipboard.writeText(v);
        flashMsg('Copied');
        btn.textContent = '✓';
        setTimeout(() => { btn.textContent = '⧉'; }, 1200);
      } catch (_) {
        flashMsg('Copy failed');
      }
    });
  });
}

function flashMsg(msg) {
  const el = document.getElementById('pub-flash');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 1400);
}

/* Mirror of the user's upfront publish choice (the home-form / scope-view
   checkbox). Set on submit, displayed on the privacy card so users see
   what they decided. Backend persists the same flag and applies it in
   _persist_and_finish, this var is just for the UI hint. */
let publicScanAutoPublish = false;
let publicScanAutoPublishFired = false;  // legacy guard; backend handles it now

/* Private-by-default note + a one-tap "copy the share URL" affordance.
   Shows whether the scan is going to be auto-published when done (read
   straight from the API's publish_on_complete field, falling back to the
   in-memory flag set during submission). */
function renderPrivacyCard(job) {
  const shareUrl = window.location.origin + '/s/' + (publicScanUrlId || '');
  const willPublish = !!(job && job.publish_on_complete) || publicScanAutoPublish;
  const badge = willPublish
    ? `<div class="pub-publish-badge will-publish">Will publish to the feed when done</div>`
    : `<div class="pub-publish-badge will-stay-private">Stays private. Publish anytime from this page when it's done.</div>`;
  return `
    <div class="pub-privacy-card">
      <div class="pub-privacy-title">Your share link</div>
      <div class="pub-share-row">
        <input class="pub-share-input" id="pub-share-url" type="text" readonly value="${esc(shareUrl)}" onclick="this.select()">
        <button class="btn" id="pub-share-copy" type="button">Copy link</button>
      </div>
      ${badge}
    </div>
  `;
}

function wireCopyShareLink() {
  const btn = document.getElementById('pub-share-copy');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const input = document.getElementById('pub-share-url');
    const v = input ? input.value : '';
    try {
      await navigator.clipboard.writeText(v);
      btn.textContent = t('Copied ✓');
      setTimeout(() => { btn.textContent = t('Copy link'); }, 1400);
    } catch (_) {
      input?.select();
      flashMsg('Press Ctrl/Cmd-C to copy');
    }
  });
}

/* Empty-page icons for error / expired states. SVG inline so they tint with
   the surrounding text color and don't require an extra round-trip. */
const _PUB_ERROR_ICON = `
  <svg class="pub-error-icon" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="10"></circle>
    <line x1="12" y1="8" x2="12" y2="12"></line>
    <line x1="12" y1="16" x2="12.01" y2="16"></line>
  </svg>`;
const _PUB_EXPIRED_ICON = `
  <svg class="pub-error-icon" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="10"></circle>
    <polyline points="12 6 12 12 16 14"></polyline>
  </svg>`;

function renderPublicScanNotFound() {
  $('public-scan-domain').textContent = '';
  $('public-scan-meta').textContent = '';
  $('public-scan-actions').style.display = 'none';
  $('public-scan-body').innerHTML = `
    <div class="pub-error">
      ${_PUB_ERROR_ICON}
      <h2>Scan not found</h2>
      <p>The URL is incorrect or the scan has already expired.</p>
      <a href="#/" class="btn btn-accent">Back to homepage</a>
    </div>
  `;
}

function renderPublicScanExpired() {
  $('public-scan-domain').textContent = '';
  $('public-scan-meta').textContent = '';
  $('public-scan-actions').style.display = 'none';
  $('public-scan-body').innerHTML = `
    <div class="pub-error">
      ${_PUB_EXPIRED_ICON}
      <h2>This scan has expired</h2>
      <p>Scans are kept for 7 days. If you downloaded the HTML snapshot, you can still open it.</p>
      <a href="#/" class="btn btn-accent">Run a new scan</a>
    </div>
  `;
}

async function onPublishToggle() {
  // Publish-only: users can add a scan to the feed but not remove it.
  if (!publicScanUrlId) return;
  if (!confirm('This scan will be publicly visible on the homepage feed for the remaining retention period. Continue?')) return;
  try {
    const resp = await fetch(API + '/api/s/' + encodeURIComponent(publicScanUrlId) + '/publish', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({published: true}),
    });
    if (!resp.ok) { showToast('Publish failed.'); return; }
    pollPublicScan();
    showToast('Published to feed');
  } catch (_) { showToast('Publish failed.'); }
}

async function cancelPublicScan() {
  if (!publicScanUrlId) return;
  if (!confirm('Cancel this scan?')) return;
  await fetch(API + '/api/s/' + encodeURIComponent(publicScanUrlId), {method: 'DELETE'});
  location.hash = '#/';
}

function _feedError(listEl, emptyEl) {
  // A failed request is NOT the same as "no scans yet" — show an error with a
  // retry, never the empty state (which would misrepresent a network problem).
  emptyEl.style.display = 'none';
  listEl.innerHTML =
    `<div class="feed-error">
      <div class="feed-error-title">${esc(t("Couldn't load recent scans"))}</div>
      <div class="feed-error-sub">${esc(t('Check your connection and try again.'))}</div>
      <button class="btn" onclick="loadHomeFeed()">${esc(t('Retry'))}</button>
    </div>`;
}

async function loadHomeFeed() {
  const listEl = $('home-feed-list');
  const emptyEl = $('home-feed-empty');
  if (!listEl) return;
  try {
    const resp = await fetch(API + '/api/feed?limit=20');
    if (!resp.ok) {
      _feedError(listEl, emptyEl);
      return;
    }
    const data = await resp.json();
    if (!data.items || data.items.length === 0) {
      listEl.innerHTML = '';
      emptyEl.style.display = 'flex';   // genuine empty 200: "no scans yet"
      return;
    }
    emptyEl.style.display = 'none';
    listEl.innerHTML = data.items.map(it => {
      const top = (it.summary?.top_categories || []).slice(0, 3)
        .map(c => `<span class="pub-chip">${esc(catLabel(c.name))} · ${c.count}</span>`)
        .join('');
      return `
        <a class="feed-card" href="#/s/${encodeURIComponent(it.url_id)}">
          <div class="domain">${esc(it.domain)}</div>
          <div class="when">${relativePastTime(it.published_at)} · ${it.summary?.snapshots_analyzed || 0} snapshots</div>
          <div class="chips">${top}</div>
        </a>
      `;
    }).join('');
  } catch (e) {
    _feedError(listEl, emptyEl);
  }
}

function formatEta(seconds) {
  if (!seconds || seconds < 1) return '<1s';
  if (seconds < 60) return Math.round(seconds) + 's';
  if (seconds < 3600) return Math.round(seconds / 60) + ' min';
  return Math.round(seconds / 3600) + 'h';
}

function relativePastTime(iso) {
  if (!iso) return '';
  const ts = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function relativeFutureTime(iso) {
  if (!iso) return '';
  const ts = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
  const diff = (ts - Date.now()) / 1000;
  if (diff < 0) return 'expired';
  if (diff < 3600) return 'in ' + Math.floor(diff / 60) + ' min';
  if (diff < 86400) return 'in ' + Math.floor(diff / 3600) + 'h';
  return 'in ' + Math.floor(diff / 86400) + 'd';
}


window.addEventListener('hashchange', () => navigate(location.hash));
window.addEventListener('DOMContentLoaded', () => {
  // Standalone HTML hydration: when this page is opened from a downloaded
  // export file, window.__WAYTRACE_PRELOAD__ is set by the injected script
  // tag. Render the scan directly without hitting any API.
  if (window.__WAYTRACE_PRELOAD__) {
    document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
    const scanView = document.getElementById('view-scan-public');
    if (scanView) scanView.classList.add('active');
    publicScanUrlId = window.__WAYTRACE_PRELOAD__.url_id;
    renderPublicScan(window.__WAYTRACE_PRELOAD__);
    return;
  }
  // Promote /s/{url_id} path-only landings (pasted / email-stripped links)
  // into the hash router. The backend serves index.html for these paths;
  // the JS then converts the pathname so navigate() picks it up.
  if (!location.hash && /^\/s\/[A-Za-z0-9_-]+\/?$/.test(location.pathname)) {
    const cleaned = location.pathname.replace(/\/$/, '');
    history.replaceState(null, '', '/#' + cleaned + location.search);
  }
  initLang();
  navigate(location.hash || '#/');
  checkArchiveStatus();
  setInterval(checkArchiveStatus, 60000);
  // Event delegation for the findings table. one listener handles row
  // open + row copy-button, survives every re-render without inline
  // handlers so untrusted finding values never reach an HTML attribute
  // context. Pattern: row-copy-btn clicks open the copy helper and stop
  // propagation; any other click on a row reads data-finding-id and
  // opens the drawer.
  const tbody = document.getElementById('res-tbody');
  if (tbody) {
    tbody.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.row-copy-btn');
      if (btn) {
        ev.stopPropagation();
        const v = btn.getAttribute('data-copy-value') || '';
        copyFindingValue(v, btn);
        return;
      }
      const row = ev.target.closest('tr[data-finding-id]');
      if (!row) return;
      const id = Number(row.getAttribute('data-finding-id'));
      if (!Number.isNaN(id)) openFindingDrawer(id);
    });
    tbody.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      const row = ev.target.closest('tr[data-finding-id]');
      if (!row) return;
      ev.preventDefault();
      const id = Number(row.getAttribute('data-finding-id'));
      if (!Number.isNaN(id)) openFindingDrawer(id);
    });
  }
});

/* ===== SCAN ===== */
$('domain-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') startScan();
});

let scopeDomain = '';
let scopeSubdomains = [];
let _scopePathGroups = [];   // raw path_groups from preflight, used to assemble selected_snapshots
let scopeCheckedSubs = new Set();   // subdomain hostnames currently selected
let scopeExcludedPaths = new Set(); // normalized paths the user unticked in step 2
let scopeExcludeKeywords = [];      // URL substrings to drop (lowercase)
let scopeFallback = false;          // preflight failed: backend will crawl on its own
const SCOPE_CAP = 5000;             // hard ceiling on snapshots scanned on the hosted service (local build is unlimited)
const SCOPE_YEAR_FLOOR = 3;         // keep at least this many per archived year when capping
const SCOPE_EXCL_PRESETS = ['blog', 'tag', 'category', 'author', 'page/', 'feed', 'comment', 'wp-json'];

async function startScan() {
  // The navbar / home scan button routes through the interactive preflight
  // (subdomain + timeline picker) so every scan can be tuned before launch.
  const raw = ($('domain-input')?.value || '').trim().toLowerCase()
    .replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/+$/, '');
  if (!raw) { showToast('Type a domain first.'); return; }
  location.hash = '#/scope/' + encodeURIComponent(raw);
}

// --- Density: a finer ladder. 'Max' fills the cap proportionally per year
// (not "newest first"), so every archived year stays represented. ---
const SCOPE_DENSITY = [
  { label: 'Light', perYear: 2,  hint: '~2 snapshots/year, quick skim' },
  { label: 'Fast', perYear: 6,  hint: '~6/year, fast overview' },
  { label: 'Balanced', perYear: 12, hint: '~12/year, recommended' },
  { label: 'Dense', perYear: 24, hint: '~24/year, thorough' },
  { label: 'Deep', perYear: 50, hint: '~50/year, heavy' },
  { label: 'Max', perYear: Infinity, hint: 'up to ' + SCOPE_CAP.toLocaleString() + ', spread across all years (the local build is unlimited)' },
];
let scopeRangeFrom = null;   // inclusive year; both null = all years (timeline highlight)
let scopeRangeTo = null;
let scopeRangeAnchor = null;  // first click of an in-progress range selection
// Month-precise range ("YYYY-MM" | null), the source of truth for selection.
// Year-bar clicks set it to that year's 01..12; the homepage calendar can set
// exact months. Kept in sync with scopeRangeFrom/To (years) for the histogram.
let scopeMonthFrom = null;
let scopeMonthTo = null;
// Day-precise range (compact "YYYYMMDD" | null), the real filter source of
// truth. The day calendar sets it; year-bar clicks and the homepage month
// prefill widen to whole years/months. Month/year vars stay derived for the
// histogram + estimate text.
let scopeDayFrom = null;
let scopeDayTo = null;
let scopeDensityIdx = 5;   // default to Max density; user can dial it down

async function loadScope(domain) {
  scopeDomain = domain;
  scopeSubdomains = [];
  scopeCheckedSubs = new Set();
  scopeExcludedPaths = new Set();
  scopeExcludeKeywords = [];
  scopeFallback = false;
  scopeRangeFrom = null; scopeRangeTo = null; scopeRangeAnchor = null; scopeDensityIdx = 5;
  { const _d = $('scope-density'); if (_d) _d.value = 5; }  // every scan starts at Max density
  scopeMonthFrom = null; scopeMonthTo = null;
  scopeDayFrom = null; scopeDayTo = null;
  $('scope-domain').textContent = domain;
  // Always re-enable the launch button when entering the scope view. It is a
  // static element, so a disabled state left over from a previous successful
  // launch would otherwise persist and make the button inert on the next scan.
  $('scope-launch-btn').disabled = false;
  $('scope-sub').textContent = 'Tune the scan before launching it.';
  { const c = $('scope-intro-cap'); if (c) c.textContent = SCOPE_CAP.toLocaleString(); }
  { const intro = document.querySelector('.scope-intro'); if (intro) intro.style.display = ''; }
  $('scope-loading').style.display = '';
  $('scope-adv').style.display = 'none';
  renderScopePresets();
  renderScopeChips();

  try {
    const resp = await fetch(API + '/api/scan/preflight', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({domain})
    });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({})).then(d =>
        (d.detail && d.detail.message) || (typeof d.detail === 'string' ? d.detail : null) || resp.statusText);
      showFallbackScopeUI(domain, detail);
      return;
    }
    const data = await resp.json();
    const subs = data.subdomain_groups || [];
    scopeSubdomains = subs;
    _scopePathGroups = data.path_groups || [];

    if (subs.length === 0) {
      $('scope-loading').textContent = t('No archived pages found for this domain.');
      return;
    }

    scopeCheckedSubs = new Set(subs.map(s => s.subdomain));
    const subsTmpl = LANG === 'fr'
      ? '{n} snapshots archivés sur {k} sous-domaine(s). Choisissez ci-dessous ce qu’il faut analyser.'
      : '{n} archived snapshots across {k} subdomain(s). Pick what to scan below.';
    $('scope-sub').textContent = subsTmpl
      .replace('{n}', data.html_snapshots).replace('{k}', subs.length);
    $('scope-loading').style.display = 'none';
    $('scope-adv').style.display = '';
    // Default every scan to Max density (index 5). The user can dial it down;
    // if the selection exceeds the 5000 cap the cap-note guides them to narrow
    // the date range or lower the density.
    scopeDensityIdx = 5;
    if ($('scope-density')) { $('scope-density').value = 5; $('scope-density').style.setProperty('--fill', (5 / (SCOPE_DENSITY.length - 1) * 100) + '%'); }
    if ($('scope-density-val')) $('scope-density-val').textContent = t(SCOPE_DENSITY[5].label);
    if ($('scope-density-hint')) $('scope-density-hint').textContent = t(SCOPE_DENSITY[5].hint);

    const capEl = $('scope-intro-cap');
    if (capEl) capEl.textContent = SCOPE_CAP.toLocaleString();
    _applyScopePrefill();
    renderScopeSubList();
    renderScopePaths();
    if ($('scope-card-timeline')) $('scope-card-timeline').style.display = '';
    renderScopeChips();
    renderScopeTimeline();
  } catch (e) {
    showFallbackScopeUI(domain, e.message);
  }
}

// Carry the homepage pre-filters (exclude keywords, date range, publish) into
// the scope step as defaults the user can still change.
function _applyScopePrefill() {
  const p = _pendingScopePrefill;
  _pendingScopePrefill = null;
  if (!p) return;
  if (Array.isArray(p.exclude_keywords) && p.exclude_keywords.length) {
    scopeExcludeKeywords = p.exclude_keywords.slice(0, 50);
  }
  if (p.date_from || p.date_to) {
    scopeMonthFrom = p.date_from || null;
    scopeMonthTo = p.date_to || null;
    scopeRangeFrom = scopeMonthFrom ? parseInt(scopeMonthFrom.slice(0, 4), 10) : null;
    scopeRangeTo = scopeMonthTo ? parseInt(scopeMonthTo.slice(0, 4), 10) : null;
    // Bridge the homepage month pre-filter to the day-precise range.
    if (scopeMonthFrom) scopeDayFrom = scopeMonthFrom.replace('-', '') + '01';
    if (scopeMonthTo) scopeDayTo = scopeMonthTo.replace('-', '') + '31';
  }
  const pubEl = document.getElementById('scope-publish-on-complete');
  if (pubEl && typeof p.publish === 'boolean') pubEl.checked = p.publish;
}

function showFallbackScopeUI(domain, detailMsg) {
  // Preflight failed (usually a huge domain the bounded 60s preflight could
  // not enumerate). The scan itself has a longer budget, so still offer a
  // launch - minus the subdomain/timeline pickers we have no data for. The
  // keyword blacklist + publish toggle stay useful and apply server-side.
  scopeFallback = true;
  scopeSubdomains = [];
  _scopePathGroups = [];
  $('scope-loading').style.display = 'none';
  $('scope-adv').style.display = '';
  if ($('scope-card-subs')) $('scope-card-subs').style.display = 'none';
  if ($('scope-card-paths')) $('scope-card-paths').style.display = 'none';
  if ($('scope-card-timeline')) $('scope-card-timeline').style.display = 'none';
  if ($('scope-estimate')) $('scope-estimate').style.display = 'none';
  // The guided intro describes the 1/2/3 cards, which are hidden in fallback.
  { const intro = document.querySelector('.scope-intro'); if (intro) intro.style.display = 'none'; }
  const fbReason = LANG === 'fr' ? 'raison inconnue' : 'unknown reason';
  const fbTail = LANG === 'fr'
    ? '</span>. Le scan choisira lui-même sa profondeur depuis l’archive en direct. Vous pouvez tout de même exclure des mots-clés ci-dessous avant de lancer.'
    : '</span>. The scan will pick its own depth from the live archive. You can still exclude keywords below before launching.';
  $('scope-sub').innerHTML =
    t('Could not enumerate subdomains') + ': <span style="color:var(--text-dim)">'
    + esc(String(detailMsg || fbReason))
    + fbTail;
  _applyScopePrefill();
  renderScopeChips();
}

// Subdomains sorted as a hierarchy (sub-subdomains nested under their parent),
// each indented by depth so deep hosts are visible "dans le detail".
function _scopeSubDepth(host) {
  const base = (scopeDomain || '').split('.').length;
  return Math.max(0, Math.min(3, host.split('.').length - base));
}
function _scopeSubSorted() {
  return [...scopeSubdomains].sort((a, b) =>
    a.subdomain.split('.').reverse().join('.').localeCompare(
      b.subdomain.split('.').reverse().join('.')));
}

function renderScopeSubList() {
  const list = $('scope-list');
  if (!list) return;
  const q = ($('scope-sub-filter')?.value || '').trim().toLowerCase();
  const rows = _scopeSubSorted().filter(s => !q || s.subdomain.toLowerCase().includes(q));
  list.innerHTML = rows.map(s => {
    const on = scopeCheckedSubs.has(s.subdomain);
    const pad = _scopeSubDepth(s.subdomain) * 16;
    return `<label class="scope-item${on ? ' checked' : ''}">
        <input type="checkbox" ${on ? 'checked' : ''} onchange="onScopeSubToggle('${esc(s.subdomain)}', this.checked)">
        <div class="scope-item-name"><span class="scope-item-indent" style="width:${pad}px"></span>${esc(s.subdomain)}</div>
        <div class="scope-item-count">${s.snapshot_count} snapshots</div>
        <div class="scope-item-range">${s.first || '?'} - ${s.last || '?'}</div>
      </label>`;
  }).join('');
  const meta = $('scope-sub-meta');
  if (meta) meta.textContent = `${scopeCheckedSubs.size}/${scopeSubdomains.length} selected`;
}

function onScopeSubToggle(name, checked) {
  if (checked) scopeCheckedSubs.add(name); else scopeCheckedSubs.delete(name);
  renderScopeSubList();
  renderScopeTimeline();
}

function toggleAllScopes() {
  const allOn = scopeCheckedSubs.size === scopeSubdomains.length;
  scopeCheckedSubs = allOn ? new Set() : new Set(scopeSubdomains.map(s => s.subdomain));
  renderScopeSubList();
  renderScopeTimeline();
}

// --- Pages / paths (step 2): show the most-archived paths with their share
// of all snapshots, colour-coded, and let the user untick noisy sections. ---
function renderScopePaths() {
  const card = $('scope-card-paths'), list = $('scope-paths-list');
  if (!list) return;
  const groups = _scopePathGroups.filter(pg => pg.path);
  if (!groups.length) { if (card) card.style.display = 'none'; return; }
  if (card) card.style.display = '';
  const total = groups.reduce((a, pg) => a + (pg.count || 0), 0) || 1;
  const q = ($('scope-path-filter')?.value || '').trim().toLowerCase();
  const sorted = groups.slice().sort((a, b) => (b.count || 0) - (a.count || 0));
  const shown = sorted.filter(pg => !q || pg.path.toLowerCase().includes(q)).slice(0, 60);
  list.innerHTML = shown.map(pg => {
    const pct = (pg.count || 0) / total * 100;
    // Colour code: a path that dominates the archive is likely noise worth
    // dropping (red), a sizable share is amber, the long tail is neutral/green.
    const lvl = pct >= 35 ? 'lvl-red' : (pct >= 12 ? 'lvl-amber' : '');
    const off = scopeExcludedPaths.has(pg.path);
    const pctTxt = pct >= 10 ? pct.toFixed(0) + '%' : pct.toFixed(1) + '%';
    return `<label class="scope-path${off ? ' excluded' : ''}" title="${esc(pg.path)} · ${pg.count} snapshots">
        <input type="checkbox" ${off ? '' : 'checked'} onchange="onScopePathToggle('${esc(pg.path)}', this.checked)">
        <span class="scope-path-name">${esc(pg.path)}</span>
        <span class="scope-path-share">
          <span class="scope-path-bar ${lvl}"><i style="width:${Math.max(3, pct).toFixed(1)}%"></i></span>
          <span class="scope-path-pct">${pctTxt}</span>
        </span>
      </label>`;
  }).join('');
  const meta = $('scope-paths-meta');
  if (meta) {
    const kept = groups.length - scopeExcludedPaths.size;
    meta.textContent = `${kept}/${groups.length} kept · most-archived first, untick to skip`;
  }
}

function onScopePathToggle(path, checked) {
  if (checked) scopeExcludedPaths.delete(path); else scopeExcludedPaths.add(path);
  renderScopePaths();
  renderScopeTimeline();   // re-renders histogram + estimate from the new scope
}

// --- Keyword blacklist (exclude URLs containing a substring) ---
function addScopeKeyword(word) {
  // Keep only characters that legitimately appear in a URL path/host so the
  // value is safe to drop into the inline chip handlers and matches sensibly.
  const w = (word || '').trim().toLowerCase().replace(/[^a-z0-9._/\-]/g, '');
  if (!w || scopeExcludeKeywords.includes(w) || scopeExcludeKeywords.length >= 50) return;
  scopeExcludeKeywords.push(w);
  renderScopeChips();
  renderScopePresets();
  renderScopeTimeline();
}
function removeScopeKeyword(word) {
  scopeExcludeKeywords = scopeExcludeKeywords.filter(k => k !== word);
  renderScopeChips();
  renderScopePresets();
  renderScopeTimeline();
}
function renderScopeChips() {
  const el = $('scope-excl-chips');
  if (!el) return;
  el.innerHTML = scopeExcludeKeywords.map(k =>
    `<span class="scope-chip">${esc(k)}<button type="button" aria-label="remove" onclick="removeScopeKeyword('${esc(k)}')">&times;</button></span>`
  ).join('');
}
function renderScopePresets() {
  const el = $('scope-excl-presets');
  if (!el) return;
  const avail = SCOPE_EXCL_PRESETS.filter(p => !scopeExcludeKeywords.includes(p));
  el.innerHTML = avail.length
    ? 'Common: ' + avail.map(p => `<button type="button" onclick="addScopeKeyword('${esc(p)}')">${esc(p)}</button>`).join('')
    : '';
}

function _scopeSnaps() {
  // Flatten preflight path_groups into {ts, url, host, year}. The preflight
  // already shipped every snapshot, so the whole picker is client-side.
  const out = [];
  for (const pg of _scopePathGroups) {
    for (const s of (pg.snapshots || [])) {
      let host = '';
      try { host = new URL(s.url).hostname; } catch (_) { host = ''; }
      const year = parseInt((s.timestamp || '').slice(0, 4), 10);
      const month = (s.timestamp || '').slice(0, 4) + '-' + (s.timestamp || '').slice(4, 6);
      if (host && year) out.push({ ts: s.timestamp, url: s.url, host, year, month, path: pg.path });
    }
  }
  return out;
}

function _scopeCheckedHosts() {
  return new Set(scopeCheckedSubs);
}

function _scopeEvenlySpaced(items, n) {
  const k = items.length;
  if (n >= k) return items.slice();
  if (n <= 0) return [];
  if (n === 1) return [items[Math.floor(k / 2)]];
  const step = (k - 1) / (n - 1);
  const seen = new Set();
  const out = [];
  for (let i = 0; i < n; i++) {
    let idx = Math.round(i * step);
    while (seen.has(idx) && idx < k) idx++;
    if (idx < k) { seen.add(idx); out.push(items[idx]); }
  }
  return out;
}

// Snapshots passing the host / range / keyword filters, before density + cap.
function _scopeInScope() {
  const hosts = _scopeCheckedHosts();
  const kws = scopeExcludeKeywords;
  // Day-precise range is the real filter (compact YYYYMMDD compare on the full
  // timestamp). The day calendar / year bars keep it set.
  return _scopeSnaps().filter(s => {
    const day = s.ts.slice(0, 8);
    return (hosts.size === 0 || hosts.has(s.host)) &&
      !scopeExcludedPaths.has(s.path) &&
      (scopeDayFrom == null || day >= scopeDayFrom) &&
      (scopeDayTo == null || day <= scopeDayTo) &&
      !kws.some(kw => s.url.toLowerCase().includes(kw));
  });
}

// Year-proportional + floor selection, mirroring services/filters.py
// _allocate_budget_by_year so the client preview matches the server. Items
// carry {ts, year, url}; returns a subset of at most `cap`.
function _scopeProportionalByYear(items, cap, floor) {
  if (items.length <= cap) return items.slice();
  const byYear = {};
  for (const s of items) (byYear[s.year] = byYear[s.year] || []).push(s);
  const years = Object.keys(byYear).sort();
  if (years.length <= 1) {
    return items.slice().sort((a, b) => a.ts.localeCompare(b.ts)).slice(0, cap);
  }
  const counts = {}, alloc = {};
  for (const y of years) { counts[y] = byYear[y].length; alloc[y] = 0; }
  // Pass 1: floor per year (oldest first).
  let budget = cap;
  for (const y of years) {
    const give = Math.min(counts[y], floor, budget);
    alloc[y] = give; budget -= give;
    if (budget <= 0) break;
  }
  // Pass 2: distribute the rest proportional to remaining headroom.
  if (budget > 0) {
    const headroom = {}; let totalHr = 0;
    for (const y of years) { headroom[y] = counts[y] - alloc[y]; totalHr += headroom[y]; }
    if (totalHr > 0) {
      const ideal = {};
      for (const y of years) {
        ideal[y] = budget * headroom[y] / totalHr;
        const base = Math.min(headroom[y], Math.floor(ideal[y]));
        alloc[y] += base; budget -= base;
      }
      if (budget > 0) {
        const fr = years.filter(y => alloc[y] < counts[y])
          .sort((a, b) => (ideal[b] - Math.floor(ideal[b])) - (ideal[a] - Math.floor(ideal[a])));
        for (const y of fr) { if (budget <= 0) break; alloc[y]++; budget--; }
      }
    }
  }
  const picked = [];
  for (const y of years) {
    if (alloc[y] > 0) {
      const arr = byYear[y].slice().sort((a, b) => a.ts.localeCompare(b.ts));
      picked.push(..._scopeEvenlySpaced(arr, alloc[y]));
    }
  }
  return picked;
}

function _scopeAssembleSelected() {
  const inScope = _scopeInScope();
  const dens = SCOPE_DENSITY[scopeDensityIdx];
  let picked;
  if (!isFinite(dens.perYear)) {
    // Max: fill the cap proportionally to each year's volume, with a floor,
    // mirroring the backend so the timeline isn't biased toward recent years.
    picked = _scopeProportionalByYear(inScope, SCOPE_CAP, SCOPE_YEAR_FLOOR);
  } else {
    const byYear = {};
    for (const s of inScope) (byYear[s.year] = byYear[s.year] || []).push(s);
    picked = [];
    for (const y of Object.keys(byYear)) {
      const arr = byYear[y].sort((a, b) => a.ts.localeCompare(b.ts));
      picked.push(..._scopeEvenlySpaced(arr, dens.perYear));
    }
    if (picked.length > SCOPE_CAP) {
      // Over the cap even after sampling: re-balance proportionally per year.
      picked = _scopeProportionalByYear(picked, SCOPE_CAP, SCOPE_YEAR_FLOOR);
    }
  }
  picked.sort((a, b) => a.ts.localeCompare(b.ts));
  return picked.map(s => ({ timestamp: s.ts, url: s.url }));
}

function renderScopeTimeline() {
  const card = $('scope-card-timeline'), host = $('scope-histogram');
  if (!host) return;
  const hosts = _scopeCheckedHosts();
  const kws = scopeExcludeKeywords;
  const scoped = _scopeSnaps().filter(s =>
    (hosts.size === 0 || hosts.has(s.host)) && !kws.some(kw => s.url.toLowerCase().includes(kw)));
  if (!scoped.length) { if (card) card.style.display = 'none'; updateScopeEstimate(); return; }
  if (card) card.style.display = '';
  const counts = {};
  for (const s of scoped) counts[s.year] = (counts[s.year] || 0) + 1;
  const yrs = Object.keys(counts).map(Number);
  const minY = Math.min(...yrs), maxY = Math.max(...yrs);
  const maxC = Math.max(...Object.values(counts));
  let bars = '';
  for (let y = minY; y <= maxY; y++) {
    const c = counts[y] || 0;
    const h = maxC ? Math.max(2, Math.round(c / maxC * 100)) : 2;
    const inRange = (scopeRangeFrom == null || y >= scopeRangeFrom) &&
                    (scopeRangeTo == null || y <= scopeRangeTo);
    bars += `<button type="button" class="scope-bar${inRange ? ' in-range' : ''}" `
      + `data-year="${y}" onclick="onScopeBar(${y})" title="${y}: ${c} snapshots" `
      + `style="--h:${h}%"><span class="scope-bar-fill"></span>`
      + `<span class="scope-bar-yr">'${(y + '').slice(2)}</span></button>`;
  }
  host.innerHTML = bars;
  updateScopeEstimate();
  if ($('scal-pop') && !$('scal-pop').hidden) scalRender();
}

function onScopeBar(y) {
  if (scopeRangeAnchor == null) {
    scopeRangeAnchor = y; scopeRangeFrom = y; scopeRangeTo = y;
  } else {
    scopeRangeFrom = Math.min(scopeRangeAnchor, y);
    scopeRangeTo = Math.max(scopeRangeAnchor, y);
    scopeRangeAnchor = null;
  }
  // Clicking a year selects whole years; widen the day + month range to match
  // so the precise filter and the calendar stay consistent.
  scopeMonthFrom = scopeRangeFrom + '-01';
  scopeMonthTo = scopeRangeTo + '-12';
  scopeDayFrom = '' + scopeRangeFrom + '0101';
  scopeDayTo = '' + scopeRangeTo + '1231';
  renderScopeTimeline();
}

function resetScopeRange() {
  scopeRangeFrom = null; scopeRangeTo = null; scopeRangeAnchor = null;
  scopeMonthFrom = null; scopeMonthTo = null;
  scopeDayFrom = null; scopeDayTo = null;
  renderScopeTimeline();
}

// --- Precise day-by-day calendar (step 3): a popup showing per-day archive
// density that lets the user pick an exact date range, bound to the scope
// selection (scopeDayFrom/scopeDayTo). ---
// Day-by-day calendar. scalAnchor/scalHover/the selection are compact
// "YYYYMMDD" day keys; the grid navigates one month at a time and tints each
// day by how many snapshots fall on it.
let scalAnchor = null, scalHover = null, scalViewY = null, scalViewM = null;

function _scalScoped() {
  // Snapshots in scope by host / path / keyword (NOT date), for density+bounds.
  const hosts = _scopeCheckedHosts();
  const kws = scopeExcludeKeywords;
  return _scopeSnaps().filter(s =>
    (hosts.size === 0 || hosts.has(s.host)) &&
    !scopeExcludedPaths.has(s.path) &&
    !kws.some(kw => s.url.toLowerCase().includes(kw)));
}

// Per-day snapshot counts (key "YYYYMMDD") + min/max day present, from scope.
function _scalDayData() {
  const counts = {};
  let minD = null, maxD = null;
  for (const s of _scalScoped()) {
    const d = s.ts.slice(0, 8);
    counts[d] = (counts[d] || 0) + 1;
    if (minD == null || d < minD) minD = d;
    if (maxD == null || d > maxD) maxD = d;
  }
  return { counts, minD, maxD };
}

function scalToggle() {
  const pop = $('scal-pop');
  if (!pop) return;
  const open = pop.hidden;
  pop.hidden = !open;
  $('scal-field').setAttribute('aria-expanded', String(open));
  if (open) {
    scalAnchor = null; scalHover = null;
    // Open on the month of the current selection, else the latest archived day.
    const dd = _scalDayData();
    const focus = scopeDayTo || scopeDayFrom || dd.maxD || (new Date().getUTCFullYear() + '0101');
    scalViewY = parseInt(focus.slice(0, 4), 10);
    scalViewM = parseInt(focus.slice(4, 6), 10) - 1;  // 0-based
    scalRender();
  }
}
function scalClose() {
  const pop = $('scal-pop');
  if (pop) pop.hidden = true;
  if ($('scal-field')) $('scal-field').setAttribute('aria-expanded', 'false');
}
function scalNudgeMonth(delta) {
  scalViewM += delta;
  while (scalViewM < 0) { scalViewM += 12; scalViewY--; }
  while (scalViewM > 11) { scalViewM -= 12; scalViewY++; }
  scalRender();
}

function _scalPreview() {
  // Returns [loDay, hiDay] (YYYYMMDD) currently selected or being dragged.
  if (scalAnchor && scalHover) return scalAnchor <= scalHover ? [scalAnchor, scalHover] : [scalHover, scalAnchor];
  if (scalAnchor) return [scalAnchor, scalAnchor];
  if (scopeDayFrom && scopeDayTo) return [scopeDayFrom, scopeDayTo];
  if (scopeDayFrom) return [scopeDayFrom, scopeDayFrom];
  return null;
}

function _fmtDay(d) { return d ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : '…'; }

function scalRender() {
  const pop = $('scal-pop');
  if (!pop || pop.hidden) return;
  const { counts, minD, maxD } = _scalDayData();
  let maxC = 0;
  for (const k in counts) if (counts[k] > maxC) maxC = counts[k];
  const wd = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map(d => `<span class="scal-wd">${t(d) || d}</span>`).join('');
  const monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const first = new Date(Date.UTC(scalViewY, scalViewM, 1));
  const lead = (first.getUTCDay() + 6) % 7;  // Monday-first offset
  const daysInMonth = new Date(Date.UTC(scalViewY, scalViewM + 1, 0)).getUTCDate();
  const bounds = _scalPreview();
  let cells = '';
  for (let i = 0; i < lead; i++) cells += '<span class="scal-day scal-blank"></span>';
  for (let d = 1; d <= daysInMonth; d++) {
    const key = '' + scalViewY + String(scalViewM + 1).padStart(2, '0') + String(d).padStart(2, '0');
    const c = counts[key] || 0;
    const out = (minD && key < minD) || (maxD && key > maxD);
    let cls = 'scal-day';
    if (out) cls += ' scal-out';
    if (bounds) {
      if (key === bounds[0] || key === bounds[1]) cls += ' scal-end';
      else if (key > bounds[0] && key < bounds[1]) cls += ' scal-inrange';
    }
    if (c > 0) cls += ' scal-has';
    const intensity = maxC && c ? (0.25 + 0.75 * c / maxC).toFixed(2) : 0;
    const dot = c > 0 ? `<span class="scal-dot" style="opacity:${intensity}"></span>` : '';
    cells += `<button type="button" class="${cls}" onclick="scalPick('${key}')" onmouseenter="scalHoverDay('${key}')"`
      + ` title="${_fmtDay(key)}: ${c} snapshot${c === 1 ? '' : 's'}">${d}${dot}</button>`;
  }
  const sel = (scopeDayFrom || scopeDayTo)
    ? `${_fmtDay(scopeDayFrom)} → ${_fmtDay(scopeDayTo || scopeDayFrom)}`
    : (t('all archived days') || 'all archived days');
  pop.innerHTML = `
    <div class="mrp-cal-head">
      <button type="button" onclick="scalNudgeMonth(-1)" aria-label="Previous month">&#8249;</button>
      <span class="mrp-cal-year">${t(monthNames[scalViewM]) || monthNames[scalViewM]} ${scalViewY}</span>
      <button type="button" onclick="scalNudgeMonth(1)" aria-label="Next month">&#8250;</button>
    </div>
    <div class="scal-wdrow">${wd}</div>
    <div class="scal-daygrid" onmouseleave="scalHoverDay(null)">${cells}</div>
    <div class="scal-legend"><i></i> ${t('snapshots per day')} · <span style="color:var(--text-dim)">${sel}</span></div>
    <div class="mrp-manual">
      <button type="button" class="mrp-set" onclick="scalResetRange()">${t('Full range')}</button>
      <button type="button" class="mrp-set" style="margin-left:auto" onclick="scalClose()">${t('Done') || 'Done'}</button>
    </div>`;
}

function scalHoverDay(key) {
  if (scalAnchor && key) { scalHover = key; scalRender(); }
  else if (key == null && scalAnchor) { scalHover = null; scalRender(); }
}

function scalPick(key) {
  if (!scalAnchor) {
    scalAnchor = key; scalHover = key;
  } else {
    const lo = key < scalAnchor ? key : scalAnchor;
    const hi = key < scalAnchor ? scalAnchor : key;
    scopeDayFrom = lo; scopeDayTo = hi;
    scalAnchor = null; scalHover = null;
    // Keep month + year (histogram) in sync with the day-precise selection.
    scopeMonthFrom = lo.slice(0, 4) + '-' + lo.slice(4, 6);
    scopeMonthTo = hi.slice(0, 4) + '-' + hi.slice(4, 6);
    scopeRangeFrom = parseInt(lo.slice(0, 4), 10);
    scopeRangeTo = parseInt(hi.slice(0, 4), 10);
    renderScopeTimeline();
  }
  scalRender();
}

function scalResetRange() {
  scopeDayFrom = null; scopeDayTo = null;
  scopeMonthFrom = null; scopeMonthTo = null;
  scopeRangeFrom = null; scopeRangeTo = null; scopeRangeAnchor = null;
  scalAnchor = null; scalHover = null;
  renderScopeTimeline();
  scalRender();
}

function onScopeDensity() {
  const el = $('scope-density');
  scopeDensityIdx = parseInt(el.value, 10);
  el.style.setProperty('--fill', (scopeDensityIdx / (SCOPE_DENSITY.length - 1) * 100) + '%');
  $('scope-density-val').textContent = t(SCOPE_DENSITY[scopeDensityIdx].label);
  const hint = $('scope-density-hint');
  if (hint) hint.textContent = t(SCOPE_DENSITY[scopeDensityIdx].hint);
  updateScopeEstimate();
}

function updateScopeEstimate() {
  const el = $('scope-estimate');
  if (!el || scopeFallback) return;
  const available = _scopeInScope().length;
  const n = _scopeAssembleSelected().length;
  // Realistic estimate: snapshots are scraped MAX_CONCURRENT_SCRAPES at a time
  // (~10 in prod) with a polite delay, so wall-clock is roughly per-page / fan-out.
  // ~0.36s per snapshot at 10-way concurrency + fixed CDX/extract overhead.
  const secs = Math.round(n * 0.36 + 15);
  const etaTxt = secs < 90 ? `~${secs}s` : `~${Math.round(secs / 60)} min`;
  const rangeTxt = (scopeDayFrom || scopeDayTo)
    ? `${_fmtDay(scopeDayFrom)} → ${_fmtDay(scopeDayTo || scopeDayFrom)}`
    : (t('all dates') || 'all dates');
  const dens = t(SCOPE_DENSITY[scopeDensityIdx].label);
  // Colour code: too thin (< 25, amber), healthy (green), or capped/sampled (accent).
  const capped = available > SCOPE_CAP || n >= SCOPE_CAP;
  const lvl = capped ? 'lvl-capped' : (n < 25 ? 'lvl-thin' : 'lvl-good');
  const lvlLabel = capped ? t('sampled to fit the cap') : (n < 25 ? t('thin coverage, raise density or range') : t('good coverage'));
  const word = t('snapshots'), estW = t('est.'), densW = t('density');
  el.style.display = '';
  el.innerHTML = `<span class="scope-est-line"><span class="scope-est-dot ${lvl}"></span><strong class="scope-est-n ${lvl}">${n.toLocaleString()}</strong> ${word}`
    + ` <span class="scope-est-sep">·</span> ${estW} <strong class="scope-est-t">${etaTxt}</strong></span>`
    + `<span class="scope-est-sub">${dens} ${densW}, ${rangeTxt}. ${lvlLabel}. ${t('More density = more snapshots = longer scan.')}</span>`;
  // Cap note: explain the 5000 ceiling, more strongly when it actually bites.
  const note = $('scope-cap-note');
  if (note) {
    note.hidden = false;
    note.classList.toggle('is-capped', capped);
    const repo = '<a href="https://github.com/HXLLO/WayTrace" target="_blank" rel="noopener">github.com/HXLLO/WayTrace</a>';
    if (capped) {
      note.innerHTML = LANG === 'fr'
        ? `Ce domaine compte <strong>${available.toLocaleString()}</strong> snapshots, plus que les <strong>${SCOPE_CAP.toLocaleString()}</strong> que le service hébergé analyse par scan. WayTrace va donc échantillonner proportionnellement sur toutes les années. Pour un scan complet sans échantillonnage, <strong>resserrez la plage de dates</strong> ci-dessus (calendrier) ou <strong>baissez la densité</strong> pour tomber sous ${SCOPE_CAP.toLocaleString()} ; pour analyser tout le domaine en pleine profondeur, lancez WayTrace en local : ${repo}.`
        : `This domain has <strong>${available.toLocaleString()}</strong> snapshots, more than the <strong>${SCOPE_CAP.toLocaleString()}</strong> the hosted service scans per run, so WayTrace will sample proportionally across all years. For a complete, un-sampled scan, <strong>narrow the date range</strong> above (calendar) or <strong>lower the density</strong> to get under ${SCOPE_CAP.toLocaleString()}; to scan the whole domain at full depth, run WayTrace locally: ${repo}.`;
    } else {
      note.innerHTML = LANG === 'fr'
        ? `Le service hébergé analyse jusqu'à ${SCOPE_CAP.toLocaleString()} snapshots par scan, répartis sur toutes les années archivées. Pour de plus gros travaux, lancez WayTrace en local : ${repo}.`
        : `The hosted service scans up to ${SCOPE_CAP.toLocaleString()} snapshots per scan, spread across all archived years. For bigger jobs you can run WayTrace locally: ${repo}.`;
    }
  }
}

async function launchScopedScan() {
  $('scope-launch-btn').disabled = true;
  resetSessionState();
  try {
    // Assemble the explicit snapshot list from the picker. In fallback mode
    // (no preflight data) it is empty and the backend crawls on its own.
    const selected = scopeFallback ? [] : _scopeAssembleSelected();
    const publish = !!($('scope-publish-on-complete') && $('scope-publish-on-complete').checked);
    publicScanAutoPublish = publish;
    const body = { domain: scopeDomain, publish_on_complete: publish };
    // "Scan more" (or an explicit re-scan) bypasses the already-scanned guardrail.
    if (_forceRescan) { body.force = true; _forceRescan = false; }
    if (selected.length > 0) {
      body.selected_snapshots = selected;
    } else {
      // Fallback crawl (no preflight data): hand the blacklist + month range to
      // the backend filter so the user's choices still apply server-side.
      const cfg = {};
      if (scopeExcludeKeywords.length) cfg.exclude_keywords = scopeExcludeKeywords;
      if (scopeMonthFrom) cfg.date_from = scopeMonthFrom;
      if (scopeMonthTo) cfg.date_to = scopeMonthTo;
      if (Object.keys(cfg).length) body.config = cfg;
    }

    const resp = await fetch(API + '/api/scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (resp.status === 429) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.detail?.message || 'You already have scans in flight from this connection.');
    }
    if (resp.status === 503) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.detail?.message || 'Service is full, try again in a few minutes.');
    }
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Scan failed');
    }
    const data = await resp.json();
    // Guardrail: the server returned an existing recent scan for this domain
    // instead of re-scanning it. Tell the user why they landed on results.
    if (data.reused) showToast(t('This domain was already scanned recently. Showing that scan.'));
    location.hash = '#/s/' + encodeURIComponent(data.url_id);
  } catch (e) {
    showError('error-scope', e.message);
  } finally {
    // Guarantee the button is usable again on every exit path (success
    // navigates away, errors stay on the scope view for a retry).
    $('scope-launch-btn').disabled = false;
  }
}

// Hard reset of every cross-scan piece of state. Called when the user
// launches a new scan so the next progress page / results page starts
// from a clean slate. Without this, polling timers + cached findings
// from the previous domain leak into the new run.
function resetSessionState() {
  allFindings = [];
  filteredFindings = [];
  activeCategory = null;
  if (typeof timelineMonthFilter !== 'undefined') timelineMonthFilter = null;
  if (typeof timelineRangeFilter !== 'undefined') timelineRangeFilter = null;
  findingsPage = 0;
}

/* ===== RESULTS ===== */

// Re-poll handle for the "analysis pending" state. When the user lands on
// #/results/{id} between collection-done and analyze-done, the findings
// endpoint returns []. We re-fetch every 3s and re-render once findings
// appear, so the user never sees a permanently-empty results page.



function _ymToMs(ym) {
  // "YYYY-MM" -> first-of-month UTC ms.
  const [y, m] = ym.split('-').map(Number);
  return Date.UTC(y, (m || 1) - 1, 1);
}

function findingActiveInMonth(f, ym) {
  // Active = the finding's [first_seen, last_seen] interval overlaps
  // the given YYYY-MM month. parseMonth (defined elsewhere) tolerates
  // both "YYYY-MM" and "YYYYMMDDhhmmss"-prefix strings.
  const ms = _ymToMs(ym);
  const fs = parseMonth(f.first_seen);
  const ls = parseMonth(f.last_seen);
  if (fs == null || ls == null) return false;
  // We treat each finding as covering full months (inclusive both ends).
  return fs <= ms && ms <= ls;
}

function findingActiveInRange(f, range) {
  // range = {start: "YYYY-MM", end: "YYYY-MM"} (inclusive)
  const fs = parseMonth(f.first_seen);
  const ls = parseMonth(f.last_seen);
  if (fs == null || ls == null) return false;
  const startMs = _ymToMs(range.start);
  const endMs = _ymToMs(range.end);
  // Overlap: finding's interval intersects [start, end_of_endMonth].
  // We approximate end-of-month as endMs + 31 days so the inclusive end
  // bucket actually catches things active that month.
  const endMsInclusive = endMs + 31 * 24 * 60 * 60 * 1000;
  return fs <= endMsInclusive && ls >= startMs;
}
function renderResultsHeader(info) {
  $('res-domain').textContent = info.name;
  const el = $('res-meta');
  const m = info.scanMeta;
  const n = (v) => Number(v || 0).toLocaleString('en-US');
  if (m && (m.snapshots_analyzed || m.pages_scraped || info.total_findings)) {
    const found = m.total_snapshots_found, ana = m.snapshots_analyzed,
          scr = m.pages_scraped, failed = m.pages_failed || 0, dedup = m.pages_deduped || 0,
          fnd = info.total_findings || 0;
    // Pages archive.org refused (IP block) are NOT archive gaps - separate them
    // so the sentence stays honest and a block gets its own clear warning.
    const blocked = m.pages_blocked || 0;
    const gaps = Math.max(0, failed - blocked);
    const range = (m.date_first_seen && m.date_last_seen)
      ? `${m.date_first_seen} → ${m.date_last_seen}` : '';
    // Calm, non-redundant sentence: the headline counts already live in the
    // stat row above. "Not available from the archive" is what a failed fetch
    // actually means (a capture archive.org can no longer serve), not a tool
    // error, so it should not read like one.
    const explain = (LANG === 'fr')
      ? `Sur ${found ? `<b>${n(found)}</b> snapshots archivés` : 'les snapshots archivés'}, `
        + `<b>${n(scr)}</b> pages ont été récupérées et analysées`
        + (gaps ? `, <b>${n(gaps)}</b> n'étaient plus disponibles côté archive (lacunes d'archive)` : '')
        + (dedup ? `, <b>${n(dedup)}</b> doublons ignorés` : '')
        + (blocked ? `, <b>${n(blocked)}</b> pages non récupérées (archive.org limitait le débit)` : '')
        + `${range ? `, couvrant ${esc(range)}` : ''}.`
      : `Of ${found ? `<b>${n(found)}</b> archived snapshots` : 'the archived snapshots'}, `
        + `<b>${n(scr)}</b> pages were retrieved and analysed`
        + (gaps ? `, <b>${n(gaps)}</b> were no longer available from the archive (archive gaps)` : '')
        + (dedup ? `, <b>${n(dedup)}</b> duplicates skipped` : '')
        + (blocked ? `, <b>${n(blocked)}</b> pages archive.org rate-limited this run` : '')
        + `${range ? `, spanning ${esc(range)}` : ''}.`;
    el.innerHTML =
      `<div class="rm-line">`
      + `<span class="rm-stat"><span class="rm-num">${n(fnd)}</span> ${t('findings')}</span>`
      + `<span class="rm-stat"><span class="rm-num">${n(ana)}</span> ${t('snapshots analysed')}</span>`
      + `<span class="rm-stat"><span class="rm-num">${n(scr)}</span> ${t('pages scraped')}</span>`
      + (range ? `<span class="rm-range">${esc(range)}</span>` : '')
      + `</div>`
      + `<div class="rm-explain">${explain}</div>`;
  } else {
    const crawl = info.crawl || {};
    const parts = [];
    if (crawl.total_snapshots) parts.push(crawl.total_snapshots + ' snapshots');
    if (crawl.pages_downloaded) parts.push(crawl.pages_downloaded + ' pages');
    if (info.total_findings) parts.push(info.total_findings + ' findings');
    el.textContent = parts.join('  .  ');
  }
}

// Old severity → new OSINT taxonomy (keep old scans in DB readable).
const OSINT_VALUE_LEGACY = {
  CRITICAL: 'LEAK',
  HIGH: 'PIVOT',
  MEDIUM: 'CONTEXT',
  LOW: 'BACKGROUND',
};
const OSINT_VALUE_LABELS = {
  LEAK: 'Leak',
  PIVOT: 'Pivot',
  CONTEXT: 'Context',
  BACKGROUND: 'Background',
};
function osintValue(f) {
  const raw = (f && f.severity) || '';
  return OSINT_VALUE_LEGACY[raw] || raw;
}

// OSINT-priority ordering for the category grid. Categories that are
// typically LEAK land first, then PIVOT, then CONTEXT, then BACKGROUND.
// Within a bucket we fall back to the raw count so sparse tiles don't
// dominate. A category not listed here falls into the 'context' bucket
// (shown as 'other' if present).
const CAT_PRIORITY = {
  // leak tier
  api_keys: 0, cloud_buckets: 0, connection_strings: 0, directory_listings: 0,
  internal_ips: 0, jwt_tokens: 0,
  // pivot tier
  subdomains: 1, emails: 1, persons: 1, analytics_trackers: 1,
  adsense_ids: 1, verification_tags: 1, crypto_addresses: 1,
  favicons: 1, endpoints: 1, hidden_fields: 1, js_urls: 1,
  analytics_ids: 1, cookie_consent: 1, github_repos: 1,
  pgp_keys: 1, status_pages: 1, job_boards: 1, auth_providers: 1,
  french_business_ids: 1,
  // context tier
  technologies: 2, hosting: 2, meta_info: 2, html_titles: 2, http_headers: 2,
  iframe_sources: 2, linked_documents: 2, phones: 2,
  organizations: 2, addresses: 2,
  rss_feeds: 2, sitemaps_and_robots: 2,
  bug_bounty_programs: 2, captcha_providers: 2,
  // background tier
  outgoing_links: 3, social_profiles: 3, html_comments: 3, assets: 3,
};

// Investigator-facing description per category: what it captures and what
// pivot it offers. Shown in the description banner under the category grid.
const CAT_DESCRIPTIONS = {
  emails: 'Email addresses found in pages. Named mailboxes (jane.doe@) beat generic info@/contact@; pivot on breaches and social.',
  subdomains: 'Subdomains seen in links, scripts and content. Expands attack surface and reveals internal/infra naming.',
  api_keys: 'Exposed API keys and secret tokens (AWS, Stripe, Google, GitHub, Slack, OpenAI...). High-value leaks.',
  cloud_buckets: 'Cloud storage buckets (S3, GCS, Azure, DO Spaces). May expose files and reveal infra ownership.',
  connection_strings: 'Connection strings with embedded credentials (mysql://, postgres://, mongodb://, redis://...).',
  directory_listings: 'Open directory listings (auto-index pages) that enumerate files served on the host.',
  internal_ips: 'Private/internal IPs (RFC1918, link-local, CGNAT) leaked in markup. Hints at internal topology.',
  jwt_tokens: 'JSON Web Tokens in cookies, storage or markup. Decode for user, role and issuer hints.',
  persons: 'Named individuals from bylines, meta and JSON-LD. Pivot to LinkedIn, breaches and org charts.',
  analytics_trackers: 'Analytics, ads and measurement IDs (GA4, UA, GTM, Meta Pixel, Hotjar, Matomo, Segment...). The same ID across sites means the same operator.',
  adsense_ids: 'AdSense publisher IDs. Cluster sites sharing one ad account (publicwww, spyonweb).',
  verification_tags: 'Domain-verification tokens (Google, Microsoft, Facebook...). Tie the domain to registrant accounts.',
  crypto_addresses: 'Crypto wallet addresses (BTC, ETH, XMR, LTC...). Trace on-chain; address reuse links operators.',
  favicons: 'Favicon URLs and hashes. Pivot identical favicons across hosts via Shodan/Censys.',
  endpoints: 'URL paths and endpoints (/api, /admin, /login...). Maps the app surface and sensitive routes.',
  hidden_fields: 'Hidden form inputs (CSRF tokens, workflow state, internal IDs) left in markup.',
  js_urls: 'URLs referenced inside JavaScript (API bases, internal/staging/debug paths).',
  analytics_ids: 'Measurement IDs (GA4, UA, Hotjar, Matomo, Segment...) for cross-site operator correlation.',
  cookie_consent: 'Consent platform (Cookiebot, OneTrust...) account IDs that cluster sites run by one operator.',
  github_repos: 'Referenced GitHub repos and users. Pivot to commits, contributors and the owning org.',
  pgp_keys: 'PGP public keys, fingerprints, key IDs and keybase handles. Look them up on keyservers.',
  status_pages: 'Hosted status pages (statuspage.io, instatus...). Reveal infra and incident history.',
  job_boards: 'ATS and career boards (Greenhouse, Lever, Ashby...) carrying the company slug.',
  auth_providers: 'Identity providers (Auth0, Okta, Cognito, Keycloak...) and their tenant slugs.',
  french_business_ids: 'French business IDs (SIREN, SIRET, TVA, RCS, RNCP). Link the site to a legal entity.',
  technologies: 'Detected CMS, frameworks and libraries, and how the stack changed over time.',
  hosting: 'Hosting, CDN and infra providers inferred from headers and assets.',
  meta_info: 'Meta tags: description, author, generator, robots and Open Graph.',
  html_titles: 'HTML <title> text over time: how the page title changed across snapshots (rebrands, owners, focus shifts).',
  http_headers: 'Original HTTP response headers preserved by Wayback (Server, X-Powered-By, CSP, Set-Cookie names...).',
  iframe_sources: 'Embedded iframe sources: third-party widgets and embedded apps.',
  linked_documents: 'Linked documents (PDF, DOCX, XLSX...), often carrying metadata and internal info.',
  phones: 'Phone numbers found across the archived pages.',
  organizations: 'Organizations declared in JSON-LD / structured data.',
  addresses: 'Postal addresses declared in JSON-LD / structured data.',
  rss_feeds: 'RSS/Atom feeds. Publication cadence and author cross-reference.',
  sitemaps_and_robots: 'sitemap.xml, robots.txt and .well-known files. Site structure and otherwise-hidden paths.',
  bug_bounty_programs: 'Bug-bounty and disclosure references (HackerOne, Bugcrowd, security.txt). Security contacts.',
  captcha_providers: 'CAPTCHA providers and site keys (reCAPTCHA, hCaptcha, Turnstile, Arkose/FunCaptcha, GeeTest, AWS WAF, Friendly Captcha).',
  outgoing_links: 'External domains linked from the site. Useful for relationship mapping.',
  social_profiles: 'Linked social-media profiles.',
  html_comments: 'HTML comments in source. Often leak tooling, TODOs and internal notes.',
  assets: 'Static asset files (JS, CSS, images) referenced by the site.',
};

function renderCategoryGrid(info, activeKey) {
  const summary = (info && info.findings_summary) || {};
  // Show every search criterion, even at 0, so the investigator sees exactly
  // what was looked for. Canonical list = all known categories + anything the
  // backend reported.
  // analytics_ids is merged into analytics_trackers at scan time; never show it
  // as its own (always-empty, duplicate) tile.
  const keys = Array.from(new Set([...Object.keys(CAT_PRIORITY), ...Object.keys(summary)]))
    .filter((k) => !MERGED_AWAY_CATS.has(k));
  const cats = keys
    .map((k) => [k, summary[k] || 0])
    .sort(([a, ca], [b, cb]) => {
      const pa = CAT_PRIORITY[a] ?? 2;
      const pb = CAT_PRIORITY[b] ?? 2;
      if (pa !== pb) return pa - pb;        // leak first, background last
      if (cb !== ca) return cb - ca;        // within tier: populated first
      return a.localeCompare(b);
    });

  const kbd = `onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click()}"`;
  const tierClass = (k) => {
    const p = CAT_PRIORITY[k] ?? 2;
    return ['cat-tier-leak', 'cat-tier-pivot', 'cat-tier-context', 'cat-tier-background'][p] || '';
  };
  const label = (k) => CAT_LABELS[k] || k.replace(/_/g, ' ');
  const total = Object.values(summary).reduce((a, b) => a + b, 0);
  const tile = ([key, count]) => `
    <div class="cat-tile ${tierClass(key)}${count === 0 ? ' is-empty' : ''}${key === activeKey ? ' active' : ''}"
         role="button" tabindex="0" aria-pressed="${key === activeKey}"
         aria-label="${esc(label(key))}: ${count} findings"
         title="${esc(t(CAT_DESCRIPTIONS[key] || ''))}"
         onclick="selectCategory('${key}')" ${kbd}>
      <div class="cat-tile-head"><span class="cat-tile-count">${count}</span><span class="cat-tile-name">${esc(label(key))}</span></div>
      <div class="cat-tile-desc">${esc(t(CAT_DESCRIPTIONS[key] || ''))}</div>
    </div>`;

  // Progressive disclosure: surface categories that actually have findings;
  // tuck the searched-but-empty ones behind a toggle so the grid does not
  // dominate the page. Everything stays one click away.
  const populated = cats.filter(([, c]) => c > 0);
  const empty = cats.filter(([, c]) => c === 0);
  const allTile = `
    <div class="cat-tile cat-tile-all${!activeKey ? ' active' : ''}"
         role="button" tabindex="0" aria-pressed="${!activeKey}"
         aria-label="Show all categories" onclick="selectCategory(null)" ${kbd}>
      <div class="cat-tile-head"><span class="cat-tile-count">${total}</span><span class="cat-tile-name">${esc(t('All'))}</span></div>
      <div class="cat-tile-desc">${esc(t('Every finding across all categories.'))}</div>
    </div>`;
  // Group the populated categories by OSINT tier under a small header each, so
  // the grid reads as meaningful sections (leaks, pivots, context...) instead
  // of a flat wall. The All tile and the searched-but-empty categories stay
  // visible at the end so the investigator still sees everything that was looked
  // for.
  const TIER_LABELS = [t('Leaks & secrets'), t('Pivots'), t('Context'), t('Other signals')];
  const TIER_SLUG = ['leak', 'pivot', 'context', 'background'];
  const byTier = [[], [], [], []];
  populated.forEach(([k, c]) => { const p = Math.max(0, Math.min(3, CAT_PRIORITY[k] ?? 2)); byTier[p].push([k, c]); });
  let html = '<div class="cat-groups">';
  byTier.forEach((grp, i) => {
    if (!grp.length) return;
    html += `<div class="cat-grid-head cat-grid-head--${TIER_SLUG[i]}">${esc(TIER_LABELS[i])}</div>`;
    html += `<div class="cat-grid">${grp.map(tile).join('')}</div>`;
  });
  html += `<div class="cat-grid-head cat-grid-head--all">${esc(t('All & searched'))}</div>`;
  html += `<div class="cat-grid">${allTile}${empty.map(tile).join('')}</div>`;
  html += '</div>';
  if (empty.length) {
    html += `<div class="cat-empty-note">${empty.length} ${esc(t('categories searched, nothing found (greyed above)'))}</div>`;
  }
  // Explicit empty state: an all-zero grid reads like an error (Boris's
  // feedback: "I first thought it was a bug"). Distinguish "the Wayback
  // Machine has no archive for this domain" from "archived, but no signal
  // matched on the analysed pages".
  if (total === 0) {
    const m = (info && info.scanMeta) || {};
    const crawl = (info && info.crawl) || {};
    const snaps = Number(m.total_snapshots_found || m.snapshots_analyzed || crawl.total_snapshots || 0);
    const scraped = Number(m.pages_scraped || crawl.pages_downloaded || 0);
    const noArchive = snaps === 0 && scraped === 0;
    const title = noArchive
      ? t('No Wayback Machine data for this domain.')
      : t('No findings extracted.');
    const sub = noArchive
      ? t('The Internet Archive has no archived HTML snapshots for this domain, so there is nothing to analyse. This is not an error: the domain may be too new, never crawled, or excluded from archive.org.')
      : t('Archived pages were analysed but no signals matched any category. Try a wider date range or a denser snapshot selection.');
    html = `<div class="cat-empty-state"><div class="ces-title">${title}</div><div class="ces-sub">${sub}</div></div>` + html;
  }
  $('res-catgrid').innerHTML = html;

  // Visible per-category description: tells the investigator what this category
  // surfaces and what pivot it offers. Generic line for the "All" view.
  const desc = $('res-catdesc');
  if (desc) {
    if (activeKey && CAT_DESCRIPTIONS[activeKey]) {
      const n = summary[activeKey] || 0;
      desc.className = 'cat-desc';
      const fLbl = n === 1 ? t('finding') : t('findings');
      desc.innerHTML = `<span class="cat-desc-label">${esc(label(activeKey))}</span>`
        + `<span class="cat-desc-count">${n} ${fLbl}</span>`
        + `<span class="cat-desc-text">${esc(t(CAT_DESCRIPTIONS[activeKey]))}</span>`;
    } else {
      desc.className = 'cat-desc cat-desc-all';
      desc.innerHTML = `<span class="cat-desc-text">${esc(t('Every signal extracted across the archived history. Pick a category above to see what each pivot is and focus the table.'))}</span>`;
    }
  }
}

function selectCategory(cat) {
  activeCategory = cat;
  // In v2 public mode the URL is /s/{url_id}, don't navigate.
  // Just refresh the table + sev stats so the filter is applied.
  if (v2PublicMode) {
    applyFilters();
    if (allFindings && allFindings.length) renderCategoryGrid(_v2DomainInfoCache, cat);
    return;
  }
  if (cat) {
    location.hash = '#/results/' + currentDomainId + '/' + cat;
  } else {
    location.hash = '#/results/' + currentDomainId;
  }
}

let _v2DomainInfoCache = null;

/* Mirror of backend's _item_value() (routers/analyze.py). Different categories
   store their canonical value under different keys (path, url, provider, id,
   etc.); without this mapping ~half the findings render as empty rows. */
function _v2ItemValue(cat, it) {
  if (it == null || typeof it !== 'object') return '';
  const v = it.value;
  switch (cat) {
    case 'endpoints':              return it.path || v || '';
    case 'assets':                 return it.path || v || '';
    case 'analytics_trackers':     return it.id || v || '';
    case 'analytics_ids':          {
      const plat = it.platform || ''; const idv = it.id_value || '';
      if (plat && idv) return plat + ':' + idv;
      return idv || v || '';
    }
    case 'social_profiles':        return it.url || it.handle || v || '';
    case 'technologies':           return it.technology || v || '';
    case 'persons':                return it.name || v || '';
    case 'phones':                 return it.normalized || it.raw || v || '';
    case 'jwt_tokens':             return it.token || v || '';
    case 'directory_listings':     return it.path || it.url || v || '';
    case 'organizations':          return it.name || v || '';
    case 'linked_documents':       return it.url || v || '';
    case 'html_comments':          return (it.comment || '').slice(0, 200);
    case 'meta_info':              return (it.content || '').slice(0, 200);
    case 'html_titles':            return (it.content || '').slice(0, 200);
    case 'hidden_fields':          return (it.name || '') + ':' + String(it.value || '').slice(0, 40);
    case 'internal_ips':           return it.ip || v || '';
    case 'adsense_ids':            return it.id || v || '';
    case 'verification_tags':      return it.verification_id || v || '';
    case 'iframe_sources':         return it.url || v || '';
    case 'js_urls':                return it.url || v || '';
    case 'crypto_addresses':       return it.address || v || '';
    case 'favicons':               return it.url || v || '';
    case 'outgoing_links':         return it.url || v || '';
    case 'hosting':                return it.provider || v || '';
    case 'http_headers':           {
      const t = it.type || ''; const hv = it.value || '';
      return t ? (t + ': ' + hv) : hv;
    }
    case 'bug_bounty_programs':    return it.pivot_url || ((it.platform || '') + '/' + (it.handle || ''));
    case 'captcha_providers':      {
      const sk = it.sitekey || '';
      return sk ? ((it.provider || '?') + ':' + sk) : (it.provider || '');
    }
    case 'status_pages':           return it.pivot_url || it.slug || v || '';
    case 'job_boards':             return it.pivot_url || ((it.platform || '') + '/' + (it.slug || ''));
    case 'auth_providers':         return it.pivot_url || ((it.platform || '') + '/' + (it.tenant || ''));
    case 'cookie_consent':         {
      const plat = it.platform || ''; const acct = it.account_id || '';
      if (plat && acct) return plat + ':' + acct;
      return plat || v || '';
    }
    case 'rss_feeds':              return it.url || v || '';
    case 'github_repos':           {
      if (it.pivot_url) return it.pivot_url;
      const o = it.owner || ''; const r = it.repo || '';
      if (o && r) return o + '/' + r;
      return v || '';
    }
    case 'sitemaps_and_robots':    return it.url || v || '';
    case 'pgp_keys':               return it.identifier || it.pivot_url || v || '';
    default:                       return v || it.url || it.name || it.provider || '';
  }
}

/* ===== v2 → legacy view-results adapter ===== */
function v2BuildLegacyFindings(job) {
  const results = job.results || {};
  // Synthesize per-finding severity by looking up the (category, value) pair
  // in the highlights list. Items absent from highlights stay severity=null
  // (they default to "BACKGROUND" via osintValue() returning '').
  const sevByCatValue = new Map();
  const sevByCategory = new Map();  // fallback: blanket severity for the category
  for (const h of (results.highlights || [])) {
    if (!h) continue;
    const sev = h.severity || null;
    if (h.category) {
      if (h.value) sevByCatValue.set(h.category + '::' + h.value, sev);
      // Blanket fallback so e.g. all subdomain findings inherit "PIVOT" if the
      // highlight only references the count, not each individual value.
      if (!sevByCategory.has(h.category)) sevByCategory.set(h.category, sev);
    }
  }
  const out = [];
  let synthId = 1;
  for (const [cat, items] of Object.entries(results)) {
    if (cat === 'highlights') continue;
    if (!Array.isArray(items)) continue;
    for (const it of items) {
      if (it == null) continue;
      const isStr = typeof it === 'string';
      const value = isStr ? it : _v2ItemValue(cat, it);
      const key = cat + '::' + value;
      const sev = sevByCatValue.get(key) || sevByCategory.get(cat) || null;
      const f = {
        id: synthId++,  // synthesised so row click handlers can look it up
        category: cat,
        value: String(value),
        first_seen: isStr ? null : it.first_seen,
        last_seen: isStr ? null : it.last_seen,
        occurrences: isStr ? 1 : (it.occurrences || 1),
        severity: sev,
        // Pass extras through so finding-row chips can show them
        metadata: isStr ? null : it,
      };
      out.push(f);
    }
  }
  return out;
}

function v2BuildLegacyDomainInfo(job, findings) {
  const meta = job.meta || {};
  const summary = {};
  for (const f of findings) {
    summary[f.category] = (summary[f.category] || 0) + 1;
  }
  return {
    id: job.url_id,  // string; legacy normally expects int but we never re-fetch
    name: job.domain,
    total_findings: findings.length,
    findings_summary: summary,
    scanMeta: meta,
    crawl: {
      status: 'done',
      total_snapshots: meta.snapshots_analyzed || 0,
      pages_downloaded: meta.pages_scraped || 0,
    },
    coverage: {
      truncated: false,
    },
  };
}

// "Scan more": reopen the scope tuner for the same domain so the user can pick
// a higher density/cap and relaunch. The recent CDX result is cached (~6h) so
// preflight is instant and the enumeration is not redone from zero.
// Deliberate re-scan: bypass the "already scanned" guardrail so a denser/fresh
// scan actually runs even though a scan of this domain already exists.
let _forceRescan = false;
function scanMore(domain) {
  if (!domain) return;
  _forceRescan = true;
  location.hash = '#/scope/' + encodeURIComponent(domain);
}

/* ============================================================================
   REPORT 2.0  —  two-view master-detail results page
   Default "Categories" view: a rail of all 43 categories (found first, empty
   collapsed), one open at a time, its findings + its own activity together.
   "Activity" view: checkable categories + pivots compose a shared-axis timeline,
   plus the favicon evolution gallery and a dated change feed. Neutral: provenance
   (first/last-seen, occurrences, archived source) is the evidence, no severity.
   ========================================================================== */

const REPORT2_SCOPE = Object.keys(CAT_DESCRIPTIONS); // the canonical 43 categories

let report2State = {
  view: 'cats',        // 'cats' | 'activity'
  openCat: null,       // category key, or '__all__' for the flat dump
  filter: '',          // in-category / global value filter
  showEmpty: false,    // rail: reveal the empty categories
  checkedCats: null,   // Activity view: Set of category keys shown as lanes
  checkedPivots: null, // Activity view: Set of "cat::value" pivots shown as lanes
  pivotFilter: '',     // Activity view: search box over the pivot list
};
let _r2 = { findings: [], info: null, byCat: new Map(), found: [], empty: [], job: null, lo: 0, hi: 0 };

function _r2Chip(f) {
  const m = f.metadata;
  if (!m || typeof m !== 'object') return '';
  const c = m.version || m.type || m.platform || m.provider || m.service || m.kind
    || (f.category === 'subdomains' && /(^|\.)(dev|staging|test|preprod|uat|api|admin|internal)\b/.test(f.value) ? f.value.split('.')[0] : '');
  return c ? `<span class="r2-chip">${esc(String(c).slice(0, 22))}</span>` : '';
}

function _r2Month(s) {                      // "YYYY-MM" -> month index, or null
  if (!s || typeof s !== 'string') return null;
  const m = s.match(/^(\d{4})-(\d{2})/);
  return m ? (parseInt(m[1], 10) * 12 + (parseInt(m[2], 10) - 1)) : null;
}
function _r2Bounds() {
  let lo = Infinity, hi = -Infinity;
  for (const f of _r2.findings) {
    const a = _r2Month(f.first_seen), b = _r2Month(f.last_seen);
    if (a != null) { lo = Math.min(lo, a); hi = Math.max(hi, b != null ? b : a); }
    if (b != null) hi = Math.max(hi, b);
  }
  if (!isFinite(lo)) { lo = 0; hi = 0; }
  _r2.lo = lo; _r2.hi = hi;
}

// Set the timeline bounds from a SPECIFIC set of findings, so the axis always
// spans exactly what's shown (an open category, or the checked lanes) instead of
// the whole scan — no dead years. Called right before rendering each timeline.
function _r2SetBoundsFrom(findings) {
  let lo = Infinity, hi = -Infinity;
  for (const f of findings) {
    const a = _r2Month(f.first_seen), b = _r2Month(f.last_seen);
    if (a != null) { lo = Math.min(lo, a); hi = Math.max(hi, a); }
    if (b != null) { hi = Math.max(hi, b); lo = Math.min(lo, b); }
  }
  if (!isFinite(lo)) { lo = _r2.lo; hi = _r2.hi; }   // fallback to global
  _r2.lo = lo; _r2.hi = hi;
}
function _r2Pct(mi) {                        // month index -> 0..100 across the span
  const span = Math.max(1, _r2.hi - _r2.lo);
  return Math.max(0, Math.min(100, ((mi - _r2.lo) / span) * 100));
}
function _r2Year(mi) { return Math.floor(mi / 12); }

/* One horizontal lane: a bar from first_seen to last_seen, a dot where it
   appeared, and a hatched "gone" tail + hollow dot where it disappeared. */
function _r2Lane(label, first, last, opts) {
  opts = opts || {};
  const a = _r2Month(first), b = _r2Month(last);
  if (a == null) return '';
  const left = _r2Pct(a), right = _r2Pct(b != null ? b : a);
  const w = Math.max(1.5, right - left);
  const disappeared = (b != null && b < _r2.hi);
  const cls = opts.pivot ? 'pivot' : '';
  const tag = opts.pivot ? '<span class="r2-pvtag">pivot</span>' : '';
  return `<div class="r2-lane">
    <span class="r2-lbl" title="${esc(label)}">${esc(label)} ${tag}</span>
    <div class="r2-track">
      <span class="r2-capa ${cls}" style="left:${left}%"></span>
      <span class="r2-bar ${cls}${opts.faded ? ' faded' : ''}" style="left:${left}%;width:${w}%"></span>
      ${disappeared ? `<span class="r2-gone" style="left:${right}%;right:0"></span><span class="r2-capz" style="left:${right}%"></span>` : ''}
    </div>
  </div>`;
}
function _r2Years() {
  const y0 = _r2Year(_r2.lo), y1 = _r2Year(_r2.hi);
  const n = Math.max(1, y1 - y0);
  const step = n <= 4 ? 1 : Math.ceil(n / 4);
  let out = '';
  for (let y = y0; y <= y1; y += step) out += `<span>${y}</span>`;
  if ((y1 - y0) % step !== 0) out += `<span>${y1}</span>`;
  return out;
}

/* Dated change feed for a set of findings: an "appeared" event at first_seen,
   and a "disappeared" event at last_seen when it stopped before the archive's
   end. Sorted newest-relevant first, capped. Neutral wording. */
function _r2Feed(findings, cap) {
  const ev = [];
  for (const f of findings) {
    const a = _r2Month(f.first_seen), b = _r2Month(f.last_seen);
    if (a != null) ev.push({ mi: a, kind: 'up', f });
    if (b != null && b < _r2.hi) ev.push({ mi: b, kind: 'down', f });
  }
  ev.sort((x, y) => x.mi - y.mi);
  const pick = ev.slice(-(cap || 8));
  if (!pick.length) return '';
  const rows = pick.map(e => {
    const when = (e.kind === 'up' ? e.f.first_seen : e.f.last_seen) || '';
    const verb = e.kind === 'up' ? t('appeared') : t('disappeared');
    const src = (e.kind === 'down' && e.f.metadata && e.f.metadata.source_url)
      ? ` <a href="${esc(e.f.metadata.source_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${t('last capture')} ↗</a>` : '';
    return `<div class="r2-ev ${e.kind}"><span class="r2-when">${esc(when)}</span><span class="r2-mk"></span>
      <span class="r2-txt"><span class="r2-k">${esc(String(e.f.value).slice(0, 42))}</span> ${verb}
      <span class="r2-sub">${esc(catLabel(e.f.category))}${e.f.occurrences ? ' · ' + e.f.occurrences + '×' : ''}</span>${src}</span></div>`;
  }).join('');
  return `<div class="r2-feed">
    <div class="r2-evleg"><span><i class="up"></i> ${t('appeared')}</span><span><i class="down"></i> ${t('disappeared')}</span></div>
    ${rows}</div>`;
}

/* ---- entry point, called from renderV2InLegacyView ---- */
function renderReport2(info, findings, job) {
  const byCat = new Map();
  for (const f of findings) {
    if (!byCat.has(f.category)) byCat.set(f.category, []);
    byCat.get(f.category).push(f);
  }
  for (const arr of byCat.values()) arr.sort((a, b) => (b.occurrences || 0) - (a.occurrences || 0));
  const found = [...byCat.keys()].filter(c => byCat.get(c).length).sort((a, b) => byCat.get(b).length - byCat.get(a).length);
  const empty = REPORT2_SCOPE.filter(c => !byCat.has(c) || !byCat.get(c).length);
  _r2 = { findings, info, byCat, found, empty, job, lo: 0, hi: 0 };
  _r2Bounds();

  if (!report2State.openCat || (report2State.openCat !== '__all__' && !byCat.has(report2State.openCat))) {
    report2State.openCat = found[0] || '__all__';
  }
  if (!report2State.checkedCats) report2State.checkedCats = new Set(found.slice(0, 4));
  if (!report2State.checkedPivots) report2State.checkedPivots = new Set();   // opt-in
  _r2SetHeaderFavicon(info && info.name);
  report2Render();
}

/* Show the site's own (archived) favicon next to the scan name. Uses the most
   recent favicon finding's archived image (only archive.org is contacted); falls
   back to just the name if none or the image fails. */
function _r2SetHeaderFavicon(domain) {
  const el = document.getElementById('res-domain');
  if (!el) return;
  const favs = (_r2.byCat.get('favicons') || []).slice();
  favs.sort((a, b) => String(b.last_seen || '').localeCompare(String(a.last_seen || '')));
  const m = favs.length ? (favs[0].metadata || {}) : null;
  const src = (m && m.source_url && m.source_url.includes('web.archive.org'))
    ? m.source_url.replace(/(\/web\/\d+)\//, '$1im_/') : '';
  if (src) {
    el.innerHTML = `<img class="res-fav" src="${esc(src)}" alt="" onerror="this.remove()">${esc(domain || '')}`;
  } else {
    el.textContent = domain || '';
  }
}

/* Candidate pivots for the Activity view: individual high-value values whose
   timeline is worth overlaying. */
// Pivots for the Activity view are the individual values of the CHECKED
// categories: tick a category to include it, then its values become available to
// break out as their own lanes. ALL of them are offered (no cap); a search box
// in the rail filters the list. Deduped by key.
function _r2Pivots() {
  const out = [];
  const seen = new Set();
  for (const c of _r2.found) {
    if (!report2State.checkedCats.has(c)) continue;
    for (const f of (_r2.byCat.get(c) || [])) {
      const key = c + '::' + f.value;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ key, label: f.value, cat: c, f });
    }
  }
  return out;
}

// Keyboard nav in the category rail: Enter/Space opens, Up/Down move focus to the
// adjacent category link (keeps the rail operable without a mouse).
function report2RailKey(ev, cat) {
  if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); report2OpenCat(cat); return; }
  if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
    ev.preventDefault();
    const links = Array.from(document.querySelectorAll('#r2-rail .r2-rlink'));
    const i = links.indexOf(ev.currentTarget);
    const next = links[i + (ev.key === 'ArrowDown' ? 1 : -1)];
    if (next) next.focus();
  }
}
function report2SetView(v) { report2State.view = v; report2Render(); }
function report2OpenCat(c) { report2State.openCat = c; report2State.filter = ''; report2State.view = 'cats'; report2Render(); }
function report2ToggleEmpty() { report2State.showEmpty = !report2State.showEmpty; report2Render(); }
function report2Filter(v) { report2State.filter = v || ''; report2RenderMain(); }
function report2ToggleCat(c) {
  const s = report2State.checkedCats;
  if (s.has(c)) {
    s.delete(c);
    // Drop pivots that belonged to this now-unchecked category.
    for (const k of [...report2State.checkedPivots]) {
      if (k.startsWith(c + '::')) report2State.checkedPivots.delete(k);
    }
  } else { s.add(c); }
  report2Render();
}
function report2TogglePivot(k) { const s = report2State.checkedPivots; s.has(k) ? s.delete(k) : s.add(k); report2Render(); }
function report2PivotFilter(v) {
  report2State.pivotFilter = v || '';
  report2RenderRail();
  // Re-rendering the rail replaced the input; restore focus + caret to the end.
  const inp = document.getElementById('r2-pivfilter');
  if (inp) { inp.focus(); const n = inp.value.length; try { inp.setSelectionRange(n, n); } catch (_) {} }
}

function report2Render() {
  // Sync the view toggle buttons.
  const bc = document.getElementById('r2-vbtn-cats'), ba = document.getElementById('r2-vbtn-activity');
  if (bc && ba) {
    const cats = report2State.view === 'cats';
    bc.classList.toggle('active', cats); bc.setAttribute('aria-selected', String(cats));
    ba.classList.toggle('active', !cats); ba.setAttribute('aria-selected', String(!cats));
  }
  const fi = document.getElementById('r2-filter');
  if (fi && fi.value !== report2State.filter) fi.value = report2State.filter;
  report2RenderRail();
  report2RenderMain();
}

function report2RenderRail() {
  const rail = document.getElementById('r2-rail');
  if (!rail) return;
  if (report2State.view === 'activity') {
    const allPivots = _r2Pivots();
    const pq = (report2State.pivotFilter || '').toLowerCase();
    const pivots = pq ? allPivots.filter(p => String(p.label).toLowerCase().includes(pq)) : allPivots;
    const pivotBody = allPivots.length
      ? (`<div class="r2-pivsearch"><input id="r2-pivfilter" type="text" autocomplete="off" spellcheck="false"
            placeholder="${esc(t('Search pivots…'))}" value="${esc(report2State.pivotFilter || '')}"
            oninput="report2PivotFilter(this.value)"></div>`
         + (pivots.length
            ? pivots.map(p => {
                const on = report2State.checkedPivots.has(p.key);
                return `<div class="r2-chk pv${on ? ' on' : ''}" onclick="report2TogglePivot('${esc(p.key).replace(/'/g, "\\'")}')">
                  <span class="r2-box">${on ? '✓' : ''}</span><span class="r2-pv" title="${esc(p.label)}">${esc(String(p.label))}</span></div>`;
              }).join('')
            : `<div class="r2-pivnote">${t('No pivot matches.')}</div>`))
      : `<div class="r2-pivnote">${t('Tick a category above to pick pivots from its values.')}</div>`;
    rail.innerHTML =
      `<div class="r2-rt"><span>${t('Categories')}</span><span>${t('tick')}</span></div>` +
      _r2.found.map(c => {
        const on = report2State.checkedCats.has(c);
        return `<div class="r2-chk${on ? ' on' : ''}" onclick="report2ToggleCat('${c}')">
          <span class="r2-box">${on ? '✓' : ''}</span><span>${esc(catLabel(c))}</span>
          <span class="r2-c">${_r2.byCat.get(c).length}</span></div>`;
      }).join('') +
      `<div class="r2-rt2">${t('Pivots from ticked categories')}</div>` +
      pivotBody +
      `<div class="r2-rt2">${t('Views')}</div>
       <div class="r2-nav" onclick="report2SetView('cats')"><span class="i">▤</span> ${t('Categories')}</div>`;
    return;
  }
  // Categories view rail
  const link = (c) => {
    const on = report2State.openCat === c;
    return `<div class="r2-rlink${on ? ' on' : ''}" role="button" tabindex="0" onclick="report2OpenCat('${c}')" onkeydown="report2RailKey(event,'${c}')">
      <span>${esc(catLabel(c))}</span><span class="r2-c">${_r2.byCat.get(c).length}</span></div>`;
  };
  const emptyLink = (c) => `<div class="r2-rlink zero${report2State.openCat === c ? ' on' : ''}" role="button" tabindex="0" onclick="report2OpenCat('${c}')" onkeydown="report2RailKey(event,'${c}')">
      <span>${esc(catLabel(c))}</span><span class="r2-c">0</span></div>`;
  rail.innerHTML =
    `<div class="r2-rt"><span>${t('Found')}</span><span>${_r2.found.length}</span></div>` +
    _r2.found.map(link).join('') +
    `<div class="r2-rall${report2State.openCat === '__all__' ? ' on' : ''}" onclick="report2OpenCat('__all__')"><span class="i">▦</span> ${t('Show all')}</div>` +
    `<div class="r2-emptytoggle" onclick="report2ToggleEmpty()"><span>${report2State.showEmpty ? '▾' : '▸'}</span> ${_r2.empty.length} ${t('empty categories (searched)')}</div>` +
    (report2State.showEmpty ? `<div class="r2-emptylist">${_r2.empty.map(emptyLink).join('')}</div>` : '') +
    `<div class="r2-rt2">${t('Views')}</div>
     <div class="r2-nav" onclick="report2SetView('activity')"><span class="i">▚</span> ${t('Activity')}</div>`;
}

function report2RenderMain() {
  const main = document.getElementById('r2-main');
  if (!main) return;
  if (report2State.view === 'activity') { main.innerHTML = report2ActivityHTML(); _r2Fade(main); return; }

  // Whole-scan empty state: nothing was found in any category. Say so plainly
  // (with the scope that was searched) rather than showing a blank panel.
  if (!_r2.found.length) {
    const m = (_r2.info && _r2.info.scanMeta) || {};
    const ana = m.snapshots_analyzed || m.pages_scraped || 0;
    main.innerHTML =
      `<div class="r2-noresults">
        <div class="r2-noresults-title">${t('No findings')}</div>
        <div class="r2-noresults-sub">${t('WayTrace searched all 43 categories across {n} archived pages and found nothing to extract.').replace('{n}', ana)}</div>
      </div>`;
    _r2Fade(main);
    return;
  }

  const cat = report2State.openCat;
  if (cat === '__all__') {
    main.innerHTML = _r2.found.map(c => report2CatBlock(c, false)).join('');
    _r2Fade(main);
    return;
  }
  const isEmpty = !_r2.byCat.has(cat) || !_r2.byCat.get(cat).length;
  main.innerHTML = report2CatBlock(cat, true, isEmpty);
  _r2Fade(main);
}

// Retrigger a short fade-in on the freshly-rendered panel content (respects
// prefers-reduced-motion via the CSS).
function _r2Fade(main) {
  main.classList.remove('r2-anim');
  void main.offsetWidth;   // force reflow so the animation replays
  main.classList.add('r2-anim');
}

function _r2Rows(list) {
  return `<div class="r2-colhead"><span>${t('value')}</span><span class="r">${t('occ.')}</span><span class="r">${t('seen')}</span><span class="r r2-srch">${t('source')}</span></div>` +
    list.map(f => {
      const span = (f.first_seen || f.last_seen)
        ? `${esc(f.first_seen || '?')} <span class="r2-arw">→</span> ${_r2Month(f.last_seen) != null && _r2Month(f.last_seen) >= _r2.hi ? `<span class="r2-now">${esc(f.last_seen)}</span>` : `<span class="r2-end">${esc(f.last_seen || '?')}</span>`}`
        : '<span class="r2-end">·</span>';
      return `<div class="r2-row">
        <span class="r2-val">
          <button class="r2-copy" title="${escAttr(t('Copy') + ': ' + f.value)}" onclick="report2Copy(event)" aria-label="${escAttr(t('Copy'))}">⧉</button>
          <span class="r2-val-text" title="${escAttr(f.value)}">${esc(f.value)}</span>${_r2Chip(f)}
        </span>
        <span class="r2-occ"><b>${f.occurrences || 1}</b></span>
        <span class="r2-span">${span}</span>
        <span class="r2-src">${makeSourceLink(f)}</span>
      </div>`;
    }).join('');
}

function report2CatBlock(cat, withActivity, isEmpty) {
  const all = _r2.byCat.get(cat) || [];
  const q = report2State.filter.toLowerCase();
  const list = q ? all.filter(f => f.value.toLowerCase().includes(q)) : all;
  const desc = CAT_DESCRIPTIONS[cat] ? t(CAT_DESCRIPTIONS[cat]) : '';
  const filtered = q && list.length !== all.length ? `<span class="r2-filtered">${list.length} ${t('shown')}</span>` : '';
  const head = `<div class="r2-dhead"><span class="r2-name">${esc(catLabel(cat))}</span><span class="r2-cnt">${all.length}</span>${filtered}
    <span class="r2-copycol" onclick="report2CopyCol('${cat}', event)">${t('copy column')}</span></div>
    ${desc ? `<p class="r2-ddesc">${esc(desc)}</p>` : ''}`;

  if (isEmpty) {
    return head + `<div class="r2-emptystate">${t('Searched across every snapshot, found nothing in this category.')}</div>`;
  }
  const CAP = 200;
  const rows = _r2Rows(list.slice(0, CAP));
  const more = list.length > CAP ? `<div class="r2-more">${t('Showing first')} ${CAP} ${t('of')} ${list.length}</div>` : '';
  const act = withActivity ? report2CatActivity(cat, all) : '';
  return `<div class="r2-catblock">${head}${rows}${more}${act}</div>`;
}

function report2CatActivity(cat, list) {
  // Axis spans exactly this category's findings (no dead years).
  _r2SetBoundsFrom(list);
  const lanes = list.slice(0, 24).map(f => _r2Lane(f.value, f.first_seen, f.last_seen, { faded: _r2Month(f.last_seen) != null && _r2Month(f.last_seen) < _r2.hi })).join('');
  if (!lanes.trim()) return '';
  return `<div class="r2-act">
    <div class="r2-ah"><span class="i">▚</span> ${t('Activity of')} <b>${esc(catLabel(cat))}</b> · ${t('when each value was visible')}</div>
    <div class="r2-tl"><div class="r2-years">${_r2Years()}</div>${lanes}</div>
    ${_r2Feed(list, 6)}
  </div>`;
}

function report2ActivityHTML() {
  // Build lane descriptors first (checked categories as a category-level span,
  // checked pivots as their own finding span), so we can set the axis bounds to
  // exactly the union of what's shown before rendering — the timeline changes
  // with every tick, never showing dead years.
  const lanes = [];       // {label, first, last, pivot}
  const shownFindings = [];
  for (const c of _r2.found) {
    if (!report2State.checkedCats.has(c)) continue;
    const arr = _r2.byCat.get(c);
    let lo = Infinity, hi = -Infinity;
    for (const f of arr) {
      const a = _r2Month(f.first_seen), b = _r2Month(f.last_seen);
      if (a != null) { lo = Math.min(lo, a); hi = Math.max(hi, a); }
      if (b != null) { hi = Math.max(hi, b); lo = Math.min(lo, b); }
      shownFindings.push(f);
    }
    if (isFinite(lo)) lanes.push({ label: catLabel(c), first: firstMonthStr(lo), last: firstMonthStr(hi), pivot: false });
  }
  const pivots = _r2Pivots();
  for (const p of pivots) {
    if (!report2State.checkedPivots.has(p.key)) continue;
    lanes.push({ label: p.label, first: p.f.first_seen, last: p.f.last_seen, pivot: true });
    shownFindings.push(p.f);
  }

  const nCat = report2State.checkedCats.size, nPv = report2State.checkedPivots.size;
  if (!lanes.length) {
    return `<div class="r2-composer">
      <div class="r2-ch"><span class="i">▚</span> <b>${t('Composed activity')}</b></div>
      <div class="r2-empty-compose">${t('Tick categories or pivots on the left to build a timeline.')}</div>
    </div>`;
  }

  _r2SetBoundsFrom(shownFindings);   // axis spans exactly the checked lanes
  // Dedupe for the change feed: a value can be both a checked category finding
  // and a checked pivot, which would list its appeared/disappeared event twice.
  const feedSeen = new Set();
  const feedFindings = shownFindings.filter(f => {
    const k = f.category + '::' + f.value;
    if (feedSeen.has(k)) return false;
    feedSeen.add(k); return true;
  });
  const laneHTML = lanes.map(l => _r2Lane(l.label, l.first, l.last, { pivot: l.pivot })).join('');
  return `<div class="r2-composer">
    <div class="r2-ch"><span class="i">▚</span> <b>${t('Composed activity')}</b> · ${nCat} ${t('categories')} + ${nPv} ${t('pivots')}<span class="r2-hint">${t('untick to remove a lane')}</span></div>
    <div class="r2-tl"><div class="r2-years">${_r2Years()}</div>${laneHTML}</div>
    <div class="r2-evleg"><span><i class="up"></i> ${t('category')}</span><span><i class="pv"></i> ${t('pivot')}</span><span><i class="down"></i> ${t('disappeared')}</span></div>
    ${report2Favicons()}
    ${_r2Feed(feedFindings, 8)}
  </div>`;
}

function firstMonthStr(mi) {                 // month index -> "YYYY-MM"
  const y = Math.floor(mi / 12), m = (mi % 12) + 1;
  return y + '-' + String(m).padStart(2, '0');
}

/* Favicon evolution gallery — loads each archived favicon image from
   web.archive.org (only archive.org is contacted), falls back to a hash tile. */
function report2Favicons() {
  const favs = _r2.byCat.get('favicons') || [];
  if (!favs.length) return '';
  const cells = favs.slice(0, 6).map(f => {
    const m = f.metadata || {};
    const src = (m.source_url && m.source_url.includes('web.archive.org'))
      ? m.source_url.replace(/(\/web\/\d+)\//, '$1im_/') : '';
    const hash = (m.md5 || m.sha256 || '').slice(0, 8);
    const span = [f.first_seen, f.last_seen].filter(Boolean).join(' → ');
    const img = src
      ? `<img class="r2-favimg" src="${esc(src)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><span class="r2-favph" style="display:none">◆</span>`
      : `<span class="r2-favph">◆</span>`;
    return `<div class="r2-favera"><div class="r2-favico">${img}</div>
      <span class="r2-favm"><b>${esc(span || '·')}</b>${hash ? esc(hash) : ''}</span></div>`;
  }).join('<span class="r2-favarr">→</span>');
  return `<div class="r2-favstrip"><span class="r2-favlbl">${t('Favicon over time')}</span>${cells}</div>`;
}

function report2Copy(ev) {
  ev.stopPropagation();
  const btn = ev.currentTarget;
  // Read the value from the row's text cell so nothing has to be escaped into an
  // inline onclick attribute (a value with a quote used to break the button).
  const cell = btn.parentElement && btn.parentElement.querySelector('.r2-val-text');
  const val = cell ? cell.textContent : '';
  navigator.clipboard.writeText(val).then(() => {
    showToast(t('Copied') + ' ✓');
    // In-place confirmation so it's obvious the click copied: the icon flips to a
    // green check and pulses, then reverts.
    if (btn && btn.classList) {
      btn.classList.add('copied');
      const prev = btn.textContent;
      btn.textContent = '✓';
      setTimeout(() => { btn.classList.remove('copied'); btn.textContent = prev; }, 1100);
    }
  }).catch(() => {});
}
function report2CopyCol(cat, ev) {
  const all = _r2.byCat.get(cat) || [];
  const q = report2State.filter.toLowerCase();
  const list = q ? all.filter(f => f.value.toLowerCase().includes(q)) : all;
  const btn = ev && ev.currentTarget;
  navigator.clipboard.writeText(list.map(f => f.value).join('\n'))
    .then(() => {
      showToast(t('Copied') + ' ' + list.length + ' ' + t('values'));
      if (btn) {
        const prev = btn.textContent;
        btn.classList.add('copied'); btn.textContent = '✓ ' + list.length;
        setTimeout(() => { btn.classList.remove('copied'); btn.textContent = prev; }, 1100);
      }
    }).catch(() => {});
}

window.report2SetView = report2SetView;
window.report2OpenCat = report2OpenCat;
window.report2ToggleEmpty = report2ToggleEmpty;
window.report2Filter = report2Filter;
window.report2ToggleCat = report2ToggleCat;
window.report2TogglePivot = report2TogglePivot;
window.report2Copy = report2Copy;
window.report2CopyCol = report2CopyCol;
window.report2PivotFilter = report2PivotFilter;
window.report2RailKey = report2RailKey;
window.renderReport2 = renderReport2;


function renderV2InLegacyView(job) {
  v2PublicMode = true;
  const findings = v2BuildLegacyFindings(job);
  const info = v2BuildLegacyDomainInfo(job, findings);
  _v2DomainInfoCache = info;

  // Stuff the legacy state vars so existing render fns work
  allFindings = findings;
  filteredFindings = findings.slice();
  activeCategory = null;
  currentDomainId = job.url_id;  // string; we override selectCategory so it never round-trips through the legacy URL
  sortCol = 'occurrences';
  sortDir = 'desc';
  findingsPage = 0;

  // Swap actions: drop Timeline/Export/Re-analyze, inject our v2 buttons.
  const actions = document.querySelector('#view-results .results-actions');
  if (actions) {
    const expires = job.expires_at ? ` <span class="v2-expires-badge">${t('expires')} ${relativeFutureTime(job.expires_at)}</span>` : '';
    // Only the owner may toggle publish on an account-owned scan. For anonymous
    // scans can_publish is true (url_id capability). When a viewer can't publish,
    // show a read-only "public" badge if it is published, otherwise nothing.
    const canPublish = job.can_publish !== false;
    // Users can publish to the feed but not remove a scan from it. Once
    // published, show a read-only badge instead of a "Remove from feed" button.
    const publishCtl = job.is_published
      ? `<span class="v2-expires-badge">${t('public')}</span>`
      : (canPublish ? `<button class="btn" id="v2-publish-btn" onclick="onPublishToggle()">${t('Publish to feed')}</button>` : '');
    const uid = encodeURIComponent(job.url_id);
    actions.innerHTML = `
      <a class="btn btn-accent" id="v2-download-btn"
         href="/api/s/${uid}/export.html" download>${t('Download HTML')}</a>
      <a class="btn" href="/api/s/${uid}/export.json" download>${t('JSON')}</a>
      <a class="btn" href="/api/s/${uid}/export.csv" download>${t('CSV')}</a>
      <button class="btn" type="button" onclick="scanMore('${esc(job.domain)}')" title="${esc(t('Run a denser scan of this domain, reusing what was already found'))}">${t('Scan more')}</button>
      <button class="btn" id="v2-copy-link-btn" type="button" title="Copy the share URL to your clipboard">${t('Copy link')}</button>
      ${publishCtl}
      ${expires}
    `;
    const _pubBtn = document.getElementById('v2-publish-btn');
    if (_pubBtn) _pubBtn.dataset.published = job.is_published ? '1' : '0';
    const _copyBtn = document.getElementById('v2-copy-link-btn');
    if (_copyBtn) {
      _copyBtn.addEventListener('click', async () => {
        const url = window.location.origin + '/s/' + encodeURIComponent(job.url_id);
        try {
          await navigator.clipboard.writeText(url);
          _copyBtn.textContent = t('Copied ✓');
          setTimeout(() => { _copyBtn.textContent = t('Copy link'); }, 1400);
        } catch (_) {
          showToast('Copy failed. URL: ' + url);
        }
      });
    }
  }

  // Header (domain + meta), then the new two-view master-detail report.
  renderResultsHeader(info);
  // Reset per-scan report state so a freshly opened scan starts clean.
  report2State.openCat = null;
  report2State.filter = '';
  report2State.showEmpty = false;
  report2State.checkedCats = null;
  report2State.checkedPivots = null;
  report2State.pivotFilter = '';
  report2State.view = 'cats';
  renderReport2(info, findings, job);

  // Switch active view
  document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
  document.getElementById('view-results').classList.add('active');
}

/* ===== FINDINGS TABLE ===== */

// Tiny generic debounce. avoids depending on lodash/underscore. 150ms
// makes keystroke-by-keystroke search stop thrashing 5000-row filters.
function _debounce(fn, ms = 150) {
  let t = null;
  return (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => { t = null; fn.apply(null, args); }, ms);
  };
}

// Inline timeline ribbon filters. Either a single month is active
// (timelineMonthFilter = "YYYY-MM") OR a range (timelineRangeFilter =
// {start, end}). They're mutually exclusive. setting one clears the
// other. Findings whose [first_seen, last_seen] interval overlaps the
// filter pass through; everything else is filtered out.
let timelineMonthFilter = null;
let timelineRangeFilter = null;

function applyFilters() {
  // The old tabbed findings table was replaced by the two-view report (report2).
  // Its filter controls no longer exist in the DOM, so bail out if they're gone
  // (a leftover caller, e.g. the timeline drawer, must not crash on null.value).
  const sevEl = $('filter-severity'), searchEl = $('filter-search');
  if (!sevEl || !searchEl) return;
  const sevFilter = sevEl.value;
  const searchFilter = (searchEl.value || '').toLowerCase();

  // Reset pagination when the filter changes so the first page reflects the
  // new dataset.
  findingsPage = 0;

  filteredFindings = allFindings.filter(f => {
    if (activeCategory && f.category !== activeCategory) return false;
    // Filter on the normalised OSINT value so legacy scans (CRITICAL/HIGH/...)
    // and new scans (LEAK/PIVOT/...) both respond to the same dropdown.
    if (sevFilter && osintValue(f) !== sevFilter) return false;
    if (searchFilter && !String(f.value || '').toLowerCase().includes(searchFilter)) return false;
    if (timelineMonthFilter && !findingActiveInMonth(f, timelineMonthFilter)) return false;
    if (timelineRangeFilter && !findingActiveInRange(f, timelineRangeFilter)) return false;
    return true;
  });

  // Sort
  filteredFindings.sort((a, b) => {
    let va = a[sortCol], vb = b[sortCol];
    if (sortCol === 'occurrences') { va = Number(va)||0; vb = Number(vb)||0; }
    const cmp = typeof va === 'number' ? va - vb : String(va||'').localeCompare(String(vb||''));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  renderFindingsTable();

  // Re-render side-drawer timeline if open and in synced mode
  if (timelineOpen && timelineSynced) {
    renderTimeline();
  }
}

// Precise platform/prefix labels for ad and tracker IDs, so a bare id like
// "ca-pub-123..." or a pixel number reads as "AdSense" / "Meta Pixel" at a
// glance (RETEX n2: show the exact tag per finding).
const AD_NETWORK_LABELS = {
  adsense_publisher: 'AdSense (ca-pub-)',
  admob: 'AdMob (ca-app-pub-)',
  ad_slot: 'Ad slot',
  GA_Universal: 'Universal Analytics (UA-)',
  GA4: 'GA4 (G-)',
  GTM: 'GTM',
  Google_Ads: 'Google Ads (AW-)',
  Meta_Pixel: 'Meta Pixel',
  Hotjar: 'Hotjar',
  Mixpanel: 'Mixpanel',
  Yandex_Metrica: 'Yandex Metrica',
};

function _adNetworkChip(f) {
  if (!f || !f.metadata) return '';
  if (f.category !== 'adsense_ids' && f.category !== 'analytics_trackers') return '';
  const t = f.metadata.type || '';
  const label = AD_NETWORK_LABELS[t] || (t ? t.replace(/_/g, ' ') : '');
  return label ? `<span class="ad-chip">${esc(label)}</span> ` : '';
}

function renderFindingsTable() {
  const searchFilter = ($('filter-search').value || '').toLowerCase();

  const cols = [
    {key: 'value', label: 'Value', cls: '', sortable: true},
    {key: 'category', label: 'Category', cls: 'col-date', sortable: true},
    {key: 'first_seen', label: 'First', cls: 'col-date', sortable: true},
    {key: 'last_seen', label: 'Last', cls: 'col-date', sortable: true},
    {key: 'occurrences', label: '#', cls: 'col-occ', sortable: true},
    {key: '_src', label: 'Source', cls: 'col-src', sortable: false},
  ];

  // Hide category column if filtering by one
  const visibleCols = activeCategory
    ? cols.filter(c => c.key !== 'category')
    : cols;

  $('res-thead').innerHTML = '<tr>' + visibleCols.map(c => {
    if (!c.sortable) return `<th class="${c.cls}">${c.label}</th>`;
    const sorted = sortCol === c.key;
    const arrow = sorted ? (sortDir === 'asc' ? '\u25b2' : '\u25bc') : '\u25bc';
    return `<th class="${c.cls}${sorted ? ' sorted' : ''}"
                onclick="onSort('${c.key}')">
              ${c.label} <span class="sort-arrow">${arrow}</span>
            </th>`;
  }).join('') + '</tr>';

  // Paginated rendering. keeps the DOM small and the whole dataset reachable.
  // Clamp page if a filter reduces the total below the current page window.
  const pageSize = 200;
  const total = filteredFindings.length;
  const maxPage = Math.max(0, Math.ceil(total / pageSize) - 1);
  if (findingsPage > maxPage) findingsPage = maxPage;
  const start = findingsPage * pageSize;
  const end = Math.min(start + pageSize, total);
  const rows = filteredFindings.slice(start, end);

  $('res-tbody').innerHTML = rows.map(f => {
    // Apply normalised class so old + new scans show the right colour.
    const sevKey = osintValue(f);
    const sevDot = sevKey ? `<span class="sev-dot ${sevKey}" title="${OSINT_VALUE_LABELS[sevKey] || sevKey}"></span>` : '';
    const srcLink = makeSourceLink(f);
    const cells = visibleCols.map(c => {
      if (c.key === '_sev') return `<td class="col-sev">${sevDot}</td>`;
      if (c.key === '_src') return `<td class="col-src">${srcLink}</td>`;
      if (c.key === 'value') {
        // Use data-copy-value + event delegation (wired below) instead of
        // inline onclick. embedding a JSON-stringified value inside an
        // HTML-attribute string mixes three escape contexts (JS string,
        // HTML attribute, JSON) and any one of them will eventually break
        // on a finding value with an odd quote/newline combination.
        const rawVal = String(f.value || '');
        return `<td class="val">${_adNetworkChip(f)}${highlightMatch(rawVal, searchFilter)}<button class="row-copy-btn" data-copy-value="${esc(rawVal)}" title="Copy value" aria-label="Copy value">⎘</button></td>`;
      }
      if (c.key === 'occurrences') return `<td class="col-occ">${f.occurrences || 1}</td>`;
      if (c.key === 'category') return `<td class="col-date">${esc((f.category||'').replace(/_/g,' '))}</td>`;
      return `<td class="${c.cls}">${esc(f[c.key] || '-')}</td>`;
    });
    return `<tr tabindex="0" role="button" data-finding-id="${f.id}" aria-label="Finding ${esc(String(f.value||'').slice(0,80))}" style="cursor:pointer">` + cells.join('') + '</tr>';
  }).join('');

  // Footer: counts + pager. If filter reduces total to ≤ pageSize we skip
  // the pager entirely. no clutter when pagination isn't needed.
  const footer = $('res-footer');
  if (total <= pageSize) {
    footer.innerHTML = `${total} finding${total === 1 ? '' : 's'}`;
  } else {
    const pageCount = Math.ceil(total / pageSize);
    const prevDisabled = findingsPage === 0 ? 'disabled' : '';
    const nextDisabled = findingsPage >= pageCount - 1 ? 'disabled' : '';
    footer.innerHTML = `
      <span>${total} findings · showing ${start + 1}-${end}</span>
      <span class="res-pager">
        <button class="res-page-btn" ${prevDisabled} onclick="goFindingsPage(${findingsPage - 1})">‹ Prev</button>
        <span class="res-page-label">Page ${findingsPage + 1} / ${pageCount}</span>
        <button class="res-page-btn" ${nextDisabled} onclick="goFindingsPage(${findingsPage + 1})">Next ›</button>
      </span>`;
  }
}

function goFindingsPage(p) {
  const pageSize = 200;
  const maxPage = Math.max(0, Math.ceil(filteredFindings.length / pageSize) - 1);
  findingsPage = Math.max(0, Math.min(p, maxPage));
  renderFindingsTable();
  // Scroll the table back into view so the user doesn't lose their place.
  // Respect prefers-reduced-motion. scrollIntoView's behavior option is
  // not covered by the CSS media-query reset.
  const tbl = document.getElementById('res-tbody');
  if (tbl && tbl.scrollIntoView) {
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    tbl.scrollIntoView({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' });
  }
}

function makeSourceLink(f) {
  if (f.metadata && f.metadata.source_url) {
    const url = f.metadata.source_url;
    const isArchive = url.includes('web.archive.org');
    const label = isArchive ? 'archive' : 'view';
    const cls = isArchive ? 'src-link' : 'src-link cc';
    return `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener" title="View source page" onclick="event.stopPropagation()">${label}</a>`;
  }
  return '';
}

function onSort(col) {
  if (sortCol === col) {
    sortDir = sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    sortCol = col;
    sortDir = (col === 'value' || col === 'category') ? 'asc' : 'desc';
  }
  applyFilters();
}

/* ===== EXPORT DRAWER ===== */

let exportFormat = 'json';
let exportSelectedCats = new Set();  // empty = all

function openExportDrawer() {
  _lastFocusBeforeDrawer = document.activeElement;
  const drawer = document.getElementById('export-drawer');
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  renderExportCategories();
  renderExportFiltersHint();
  _trapFocus(drawer);
  setTimeout(() => {
    const firstBtn = drawer.querySelector('.exp-format-opt.active') || drawer.querySelector('button');
    if (firstBtn) firstBtn.focus();
  }, 50);
}

function closeExportDrawer() {
  const drawer = document.getElementById('export-drawer');
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
  _releaseFocusTrap(drawer);
  restoreFocus();
}

function selectExportFormat(fmt) {
  exportFormat = fmt;
  document.querySelectorAll('#exp-format-group .exp-format-opt').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-format') === fmt);
  });
}

function renderExportCategories() {
  const list = document.getElementById('exp-cat-list');
  if (!list) return;
  // Count by category from allFindings
  const counts = {};
  for (const f of allFindings || []) counts[f.category] = (counts[f.category] || 0) + 1;
  const cats = Object.keys(counts).sort();
  if (cats.length === 0) {
    list.innerHTML = '<div class="exp-filters-hint">No findings to export</div>';
    return;
  }
  // Default: if nothing selected, select all
  if (exportSelectedCats.size === 0) {
    cats.forEach(c => exportSelectedCats.add(c));
  }
  list.innerHTML = cats.map(c => `
    <label class="exp-cat-item">
      <input type="checkbox" ${exportSelectedCats.has(c) ? 'checked' : ''}
             onchange="toggleExportCat('${esc(c)}', this.checked)">
      <span>${esc(c.replace(/_/g, ' '))}</span>
      <span class="exp-cat-item-count">${counts[c]}</span>
    </label>
  `).join('');
}

function toggleExportCat(cat, checked) {
  if (checked) exportSelectedCats.add(cat);
  else exportSelectedCats.delete(cat);
}

function renderExportFiltersHint() {
  const sev = document.getElementById('filter-severity').value;
  const search = document.getElementById('filter-search').value;
  const hints = [];
  if (sev) hints.push(`severity=${sev}`);
  if (search) hints.push(`search="${search}"`);
  if (activeCategory) hints.push(`category=${activeCategory}`);
  const el = document.getElementById('exp-filters-hint');
  el.textContent = hints.length
    ? `Applying: ${hints.join(', ')} (will limit export to filtered findings)`
    : 'No filters active (will export all selected categories)';
}

function buildExportData() {
  // Apply current filters if any are active
  const sev = document.getElementById('filter-severity').value;
  const search = (document.getElementById('filter-search').value || '').toLowerCase();
  let source = (allFindings || []).filter(f => exportSelectedCats.has(f.category));
  // Match legacy + new severity labels through osintValue().
  if (sev) source = source.filter(f => osintValue(f) === sev);
  if (search) source = source.filter(f => String(f.value || '').toLowerCase().includes(search));
  return source;
}

function formatExport(findings, format) {
  if (format === 'json') {
    return JSON.stringify(findings, null, 2);
  }
  if (format === 'csv') {
    // Columns ordered for jq / awk pipelines: identity first, then timing,
    // then provenance pointers the finding came from. Extra metadata fields
    // commonly exploited by pentesters (type, provider, service, domain,
    // source_url, source_page_id) are surfaced as flat columns.
    const cols = [
      'category', 'value', 'severity',
      'first_seen', 'last_seen', 'occurrences',
      'type', 'provider', 'service', 'domain',
      'source_url', 'source_page_id', 'md5', 'sha256', 'shodan',
    ];
    const flatten = (f) => {
      const m = f.metadata || {};
      return {
        category: f.category,
        value: f.value,
        // Normalise to LEAK/PIVOT/CONTEXT/BACKGROUND for jq/grep pipelines;
        // old scans carrying CRITICAL/HIGH/MEDIUM/LOW get translated.
        severity: osintValue(f),
        first_seen: f.first_seen,
        last_seen: f.last_seen,
        occurrences: f.occurrences,
        type: m.type || '',
        provider: m.provider || '',
        service: m.service || '',
        domain: m.domain || '',
        source_url: m.source_url || '',
        source_page_id: m.source_page_id || '',
        md5: m.md5 || '',
        sha256: m.sha256 || '',
        shodan: (m.shodan === undefined || m.shodan === null) ? '' : m.shodan,
      };
    };
    const header = cols.join(',');
    const rows = findings.map(f => {
      const flat = flatten(f);
      return cols.map(c => {
        const v = flat[c] == null ? '' : String(flat[c]);
        return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
      }).join(',');
    });
    return [header, ...rows].join('\n');
  }
  if (format === 'markdown') {
    const byCat = {};
    for (const f of findings) (byCat[f.category] = byCat[f.category] || []).push(f);
    const lines = [];
    for (const cat of Object.keys(byCat).sort()) {
      lines.push(`## ${cat.replace(/_/g, ' ')}`);
      lines.push('');
      for (const f of byCat[cat]) {
        const sevKey = osintValue(f);
        const sev = sevKey ? ` \`${sevKey}\`` : '';
        const range = f.first_seen ? ` _(${f.first_seen}→${f.last_seen})_` : '';
        lines.push(`- ${f.value}${sev}${range}`);
      }
      lines.push('');
    }
    return lines.join('\n');
  }
  return '';
}

async function copyExport() {
  const data = buildExportData();
  const text = formatExport(data, exportFormat);
  try {
    await navigator.clipboard.writeText(text);
    showToast(`Copied ${data.length} findings (${exportFormat})`);
  } catch (e) {
    showToast('Copy failed');
  }
}

function downloadExport() {
  const data = buildExportData();
  const text = formatExport(data, exportFormat);
  const ext = exportFormat === 'markdown' ? 'md' : exportFormat;
  const mime = exportFormat === 'json' ? 'application/json'
    : exportFormat === 'csv' ? 'text/csv'
    : 'text/markdown';
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const domain = document.getElementById('res-domain').textContent || 'waytrace';
  a.download = `${domain}-findings.${ext}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast(`Downloaded ${data.length} findings`);
}

// Close on Escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.getElementById('export-drawer').classList.contains('open')) {
    closeExportDrawer();
  }
});

/* ===== PER-ROW COPY HELPER ===== */
async function copyFindingValue(value, btn) {
  try {
    await navigator.clipboard.writeText(value);
    if (btn) {
      btn.classList.add('copied');
      btn.textContent = '✓';
      setTimeout(() => {
        btn.classList.remove('copied');
        btn.textContent = '⎘';
      }, 1200);
    }
  } catch (e) {
    showToast('Copy failed');
  }
}

/* ===== FINDING DETAIL DRAWER ===== */

// Shared focus-trap infrastructure for modal drawers. A single keydown
// listener per drawer grabs Tab/Shift-Tab and rotates focus within the
// drawer's tabbable descendants. Escape hands focus back to the trigger.
const _focusTraps = new WeakMap();

function _getTabbables(root) {
  return Array.from(
    root.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]),' +
      ' input:not([disabled]):not([type="hidden"]), select:not([disabled]),' +
      ' [tabindex]:not([tabindex="-1"])'
    )
  ).filter(el => el.offsetParent !== null || el.getClientRects().length);
}

function _trapFocus(drawerEl) {
  if (_focusTraps.has(drawerEl)) return; // already trapped
  const handler = (ev) => {
    if (ev.key !== 'Tab') return;
    const tabbables = _getTabbables(drawerEl);
    if (!tabbables.length) return;
    const first = tabbables[0], last = tabbables[tabbables.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  };
  drawerEl.addEventListener('keydown', handler);
  _focusTraps.set(drawerEl, handler);
  drawerEl.setAttribute('aria-modal', 'true');
}

function _releaseFocusTrap(drawerEl) {
  const handler = _focusTraps.get(drawerEl);
  if (handler) {
    drawerEl.removeEventListener('keydown', handler);
    _focusTraps.delete(drawerEl);
  }
  drawerEl.removeAttribute('aria-modal');
}

function openFindingDrawer(findingId) {
  const finding = (allFindings || []).find(f => f.id === findingId);
  if (!finding) return;
  // Only capture focus the FIRST time we open. recursive navigation via
  // co-occurrence clicks keeps the same parent focus target.
  if (!document.getElementById('finding-drawer').classList.contains('open')) {
    _lastFocusBeforeDrawer = document.activeElement;
  }
  renderFindingDrawer(finding);
  const drawer = document.getElementById('finding-drawer');
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  document.getElementById('fd-backdrop')?.classList.add('open');
  _trapFocus(drawer);
  setTimeout(() => {
    const closeBtn = drawer.querySelector('.tl-close-btn');
    if (closeBtn) closeBtn.focus();
  }, 50);
}

function closeFindingDrawer() {
  const drawer = document.getElementById('finding-drawer');
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
  document.getElementById('fd-backdrop')?.classList.remove('open');
  _releaseFocusTrap(drawer);
  restoreFocus();
}

function renderFindingDrawer(finding) {
  const body = document.getElementById('fd-body');
  // Display the new OSINT-value label (Leak/Pivot/…) while keeping the CSS
  // class backward-compat for legacy scans that still carry old severities.
  const sevKey = osintValue(finding);
  const sev = sevKey ? (OSINT_VALUE_LABELS[sevKey] || sevKey) : '';
  const meta = finding.metadata || {};
  const sourcePageId = meta.source_page_id;
  const sourceUrl = meta.source_url || '';

  // Hero section: the clicked finding itself
  const sevBadge = sev
    ? `<span class="fd-hero-sev ${esc(sevKey)}">${esc(sev)}</span>`
    : '';
  const rangeLine = (finding.first_seen || finding.last_seen)
    ? `${esc(finding.first_seen || '-')} → ${esc(finding.last_seen || '-')} · ${finding.occurrences || 1}×`
    : `${finding.occurrences || 1}×`;

  let hero = `
    <div class="fd-hero">
      <div class="fd-hero-cat">${esc(catLabel(finding.category || ''))}${sevBadge}</div>
      <div class="fd-hero-value">${esc(String(finding.value || ''))}</div>
      <div class="fd-hero-meta">${rangeLine}</div>
    </div>
  `;

  // Source page link
  let sourceSection = '';
  if (sourceUrl) {
    sourceSection = `
      <div class="fd-section-label">${t('Source page')}</div>
      <div class="fd-link-row">
        <a href="${esc(sourceUrl)}" target="_blank" rel="noopener">${esc(sourceUrl)}</a>
      </div>
    `;
  }

  // Co-occurrences: other findings that share the same source_page_id
  let coSection = '';
  if (sourcePageId) {
    const coFindings = (allFindings || []).filter(f =>
      f.id !== finding.id
      && f.metadata
      && f.metadata.source_page_id === sourcePageId
    );
    if (coFindings.length > 0) {
      // Group by category
      const byCat = {};
      for (const f of coFindings) {
        (byCat[f.category] = byCat[f.category] || []).push(f);
      }
      const catKeys = Object.keys(byCat).sort();
      const groups = catKeys.map(cat => {
        const items = byCat[cat].slice(0, 10);  // cap preview per category
        const itemHtml = items.map(f => `
          <div class="fd-cooccur-item" onclick="openFindingDrawer(${f.id})">${esc(String(f.value || '').slice(0, 120))}</div>
        `).join('');
        const extra = byCat[cat].length > 10 ? ` <span class="fd-cooccur-count">(+${byCat[cat].length - 10} more)</span>` : '';
        return `
          <div class="fd-cooccur-group">
            <div class="fd-cooccur-cat">${esc(catLabel(cat))}<span class="fd-cooccur-count"> · ${byCat[cat].length}</span>${extra}</div>
            <div class="fd-cooccur-list">${itemHtml}</div>
          </div>
        `;
      }).join('');
      coSection = `
        <div class="fd-section-label">${t('Co-occurring on same page')} (${coFindings.length})</div>
        ${groups}
      `;
    } else {
      coSection = `
        <div class="fd-section-label">${t('Co-occurring on same page')}</div>
        <div class="fd-empty-note">${t('No other findings share this source page.')}</div>
      `;
    }
  } else {
    coSection = `
      <div class="fd-section-label">${t('Co-occurring on same page')}</div>
      <div class="fd-empty-note">${t('No source page recorded for this finding (mined from the archive index, or an older scan).')}</div>
    `;
  }

  // Hashes (e.g. favicon MD5/SHA-256 + Shodan mmh3) for cross-site pivoting.
  let hashSection = '';
  const md5 = meta.md5, sha256 = meta.sha256;
  const shodan = (meta.shodan !== undefined && meta.shodan !== null && meta.shodan !== '') ? meta.shodan : null;
  if (md5 || sha256 || shodan !== null) {
    const shodanUrl = shodan !== null
      ? 'https://www.shodan.io/search?query=' + encodeURIComponent('http.favicon.hash:' + shodan)
      : '';
    hashSection = `
      <div class="fd-section-label">${t('Hashes')}</div>
      ${md5 ? `<div class="fd-hash-row"><span class="fd-hash-k">MD5</span><code class="fd-hash-v">${esc(md5)}</code></div>` : ''}
      ${sha256 ? `<div class="fd-hash-row"><span class="fd-hash-k">SHA256</span><code class="fd-hash-v">${esc(sha256)}</code></div>` : ''}
      ${shodan !== null ? `<div class="fd-hash-row"><span class="fd-hash-k">Shodan</span><code class="fd-hash-v">${esc(String(shodan))}</code><a class="fd-hash-pivot" href="${esc(shodanUrl)}" target="_blank" rel="noopener" title="${esc(t('Search this favicon on Shodan'))}">${t('pivot')} ↗</a></div>` : ''}
    `;
  }

  body.innerHTML = hero + hashSection + sourceSection + coSection;
}

// Escape key closes the finding drawer
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.getElementById('finding-drawer').classList.contains('open')) {
    closeFindingDrawer();
  }
});

/* ===== TIMELINE ===== */

// Shared focus state: remember the element that was focused when a drawer opened
// so we can restore focus to it on close. One slot, since drawers aren't nested.
let _lastFocusBeforeDrawer = null;

function openTimeline() {
  _lastFocusBeforeDrawer = document.activeElement;
  timelineOpen = true;
  const drawer = document.getElementById('timeline-drawer');
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  document.getElementById('tl-backdrop').classList.add('open');
  document.body.style.overflow = 'hidden';
  renderTimeline();
  _trapFocus(drawer);
  // Move focus into the drawer for keyboard users
  setTimeout(() => {
    const firstBtn = drawer.querySelector('.tl-btn');
    if (firstBtn) firstBtn.focus();
  }, 50);
}

function closeTimeline() {
  timelineOpen = false;
  const drawer = document.getElementById('timeline-drawer');
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
  _releaseFocusTrap(drawer);
  document.getElementById('tl-backdrop').classList.remove('open');
  document.body.style.overflow = '';
  clearTimelineFilter();
  hideTooltip();
  restoreFocus();
}

function restoreFocus() {
  if (_lastFocusBeforeDrawer && typeof _lastFocusBeforeDrawer.focus === 'function') {
    try { _lastFocusBeforeDrawer.focus(); } catch (e) {}
  }
  _lastFocusBeforeDrawer = null;
}

function renderTimeline() {
  const tracks = buildTimelineData();
  const timeAxis = computeTimeAxis(tracks);

  if (!timeAxis) {
    document.getElementById('tl-axis').innerHTML = '';
    document.getElementById('tl-tracks').innerHTML =
      '<div class="tl-empty-msg">No findings to display.<br>Try toggling Synced off or adjusting your filters.</div>';
    document.getElementById('tl-stats').textContent = '0 tracks · 0 pivots · -';
    return;
  }

  const groupedPivots = computeGroupedPivots(tracks);
  renderTimeAxis(timeAxis);
  renderTracks(tracks, timeAxis, groupedPivots);
  renderTimelineStats(tracks, timeAxis, groupedPivots);
}

function computeGroupedPivots(tracks) {
  // A "grouped pivot" is a month where ≥2 non-hidden, non-empty tracks have
  // at least one pivot. These are the moments when "multiple things changed"
  //. the most interesting pivots for discovery.
  const monthToCats = new Map();  // date_ms -> Set<category>
  for (const track of tracks) {
    if (track.empty || timelineHiddenTracks.has(track.category)) continue;
    for (const p of track.pivots) {
      if (!monthToCats.has(p.date_ms)) monthToCats.set(p.date_ms, new Set());
      monthToCats.get(p.date_ms).add(track.category);
    }
  }
  const grouped = [];
  for (const [date_ms, cats] of monthToCats.entries()) {
    if (cats.size >= 2) {
      grouped.push({
        date_ms,
        categories: [...cats].sort(),
        count: cats.size,
      });
    }
  }
  grouped.sort((a, b) => a.date_ms - b.date_ms);
  return grouped;
}

function renderTracks(tracks, timeAxis, groupedPivots) {
  const container = document.getElementById('tl-tracks');
  const groupedMarkersSvg = renderGroupedPivotOverlay(groupedPivots, timeAxis, tracks);
  const tracksHtml = tracks.map(track => renderTrackRow(track, timeAxis)).join('');
  container.innerHTML = `<div class="tl-tracks-inner">${groupedMarkersSvg}${tracksHtml}</div>`;
  attachTrackListeners();
  reapplyActiveFilterClass();
}

function renderGroupedPivotOverlay(groupedPivots, timeAxis, tracks) {
  if (!groupedPivots || groupedPivots.length === 0) return '';
  const visibleCount = tracks.filter(t => !t.empty && !timelineHiddenTracks.has(t.category)).length;
  if (visibleCount === 0) return '';

  const viewBoxW = 1000;
  const scale = ms => ((ms - timeAxis.min) / (timeAxis.max - timeAxis.min)) * viewBoxW;

  // We render the overlay as an absolutely positioned SVG layer above the
  // label gutter. The lines span 0..1000 of the viewBox and the container
  // uses CSS to position it over the track area (right of the 140px label).
  const lines = groupedPivots.map(gp => {
    const x = scale(gp.date_ms).toFixed(1);
    const month = formatMonth(gp.date_ms);
    return `<line class="tl-grouped-pivot"
                  x1="${x}" y1="0" x2="${x}" y2="100"
                  data-date="${month}"
                  data-count="${gp.count}"
                  data-categories="${escapeHtml(gp.categories.join(','))}"/>`;
  }).join('');

  return `
    <svg class="tl-grouped-overlay" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
      ${lines}
    </svg>
  `;
}

function reapplyActiveFilterClass() {
  // After a re-render, restore the visual highlight for the currently active
  // filter (segment for category clicks, tick for single-pivot clicks).
  const f = timelineActiveFilter;
  if (!f) return;
  if (f.type === 'category') {
    const el = document.querySelector(
      `#tl-tracks .tl-segment[data-category="${CSS.escape(f.category)}"]`
    );
    if (el) el.classList.add('active');
  } else if (f.type === 'tick') {
    const el = document.querySelector(
      `#tl-tracks .tl-tick[data-category="${CSS.escape(f.category)}"][data-pivot-date="${CSS.escape(f.pivotMonth)}"]`
    );
    if (el) el.classList.add('active');
  }
}

function renderTrackRow(track, timeAxis) {
  const label = categoryLabel(track.category);
  const hiddenClass = timelineHiddenTracks.has(track.category) ? ' hidden' : '';

  if (track.empty) {
    return `
      <div class="tl-track${hiddenClass}" data-category="${escapeHtml(track.category)}">
        <button class="tl-label" data-category="${escapeHtml(track.category)}" data-action="toggle-track" type="button">
          <span class="tl-label-name">${escapeHtml(label)}</span>
          <span class="tl-label-count">-</span>
        </button>
        <svg class="tl-svg" viewBox="0 0 1000 28" preserveAspectRatio="none">
          <line class="tl-segment-empty" x1="0" y1="14" x2="1000" y2="14"/>
        </svg>
      </div>
    `;
  }

  const viewBoxW = 1000;
  const scale = ms => ((ms - timeAxis.min) / (timeAxis.max - timeAxis.min)) * viewBoxW;

  const segX = scale(track.segment.start);
  let segW = scale(track.segment.end) - segX;
  if (segW < 3) segW = 3;  // minimum visible width for point-in-time findings

  const sevClass = `sev-${track.severity || 'BACKGROUND'}`;
  const clustered = clusterPivots(track.pivots, timeAxis, viewBoxW);

  let ticksSvg = '';
  for (const p of clustered) {
    const x = scale(p.date_ms);
    if (p.type === 'cluster') {
      // Render as a circle with a badge
      ticksSvg += `<circle class="tl-tick-cluster ${sevClass}" cx="${x.toFixed(1)}" cy="14" r="5"
                           data-pivot-date="${formatMonth(p.date_ms)}"
                           data-pivot-end="${formatMonth(p.dateEnd_ms)}"
                           data-category="${escapeHtml(track.category)}"
                           data-cluster-count="${p.memberCount}"
                           data-cluster-added="${p.added.length}"
                           data-cluster-removed="${p.removed.length}"/>`;
    } else {
      ticksSvg += `<rect class="tl-tick ${sevClass}" x="${(x - 1).toFixed(1)}" y="4" width="2" height="20"
                         data-pivot-date="${formatMonth(p.date_ms)}"
                         data-category="${escapeHtml(track.category)}"
                         data-added="${escapeHtml(JSON.stringify(p.added))}"
                         data-removed="${escapeHtml(JSON.stringify(p.removed))}"/>`;
    }
  }

  return `
    <div class="tl-track ${sevClass}${hiddenClass}" data-category="${escapeHtml(track.category)}">
      <button class="tl-label" data-category="${escapeHtml(track.category)}" data-action="toggle-track" type="button">
        <span class="tl-label-sev"></span>
        <span class="tl-label-name">${escapeHtml(label)}</span>
        <span class="tl-label-count">${track.valueCount}</span>
      </button>
      <svg class="tl-svg" viewBox="0 0 1000 28" preserveAspectRatio="none">
        <rect class="tl-segment ${sevClass}" x="${segX.toFixed(1)}" y="11" width="${segW.toFixed(1)}" height="6" rx="3"
              data-category="${escapeHtml(track.category)}"
              data-segment-start="${formatMonth(track.segment.start)}"
              data-segment-end="${formatMonth(track.segment.end)}"
              data-value-count="${track.valueCount}"/>
        ${ticksSvg}
      </svg>
    </div>
  `;
}

function renderTimelineStats(tracks, timeAxis, groupedPivots) {
  const nonEmpty = tracks.filter(t => !t.empty);
  const totalPivots = nonEmpty.reduce((s, t) => s + t.pivots.length, 0);
  const groupedCount = groupedPivots ? groupedPivots.length : 0;
  const range = timeAxis
    ? `${formatMonth(timeAxis.min)} → ${formatMonth(timeAxis.max)}`
    : '-';
  const parts = [`${nonEmpty.length} tracks`, `${totalPivots} pivots`];
  if (groupedCount > 0) parts.push(`${groupedCount} grouped`);
  parts.push(range);
  document.getElementById('tl-stats').textContent = parts.join(' · ');
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function attachTrackListeners() {
  const container = document.getElementById('tl-tracks');
  if (!container || container._tlListenersAttached) return;

  // Event delegation for hover (segment, tick, cluster)
  container.addEventListener('mouseover', onTimelineHover);
  container.addEventListener('mouseout', onTimelineHoverOut);
  container.addEventListener('mousemove', onTimelineMouseMove);

  // Event delegation for click (segment, tick, cluster, and label toggle)
  container.addEventListener('click', onTimelineClickDispatch);

  container._tlListenersAttached = true;
}

function onTimelineClickDispatch(e) {
  // Label click → toggle track visibility (handled here for Unit 2 compat)
  const btn = e.target.closest('[data-action="toggle-track"]');
  if (btn) {
    const cat = btn.getAttribute('data-category');
    if (cat) toggleTrackVisibility(cat);
    return;
  }
  // Segment/tick/cluster click → delegated to onTimelineClick (stub for now, implemented in Unit 4)
  onTimelineClick(e);
}

function onTimelineHover(e) {
  const target = e.target;
  if (!target) return;
  if (target.classList.contains('tl-segment')) {
    showTooltip(tooltipForSegment(target), e);
  } else if (target.classList.contains('tl-tick')) {
    showTooltip(tooltipForTick(target), e);
  } else if (target.classList.contains('tl-tick-cluster')) {
    showTooltip(tooltipForCluster(target), e);
  } else if (target.classList.contains('tl-grouped-pivot')) {
    showTooltip(tooltipForGroupedPivot(target), e);
  }
}

function onTimelineHoverOut(e) {
  const target = e.target;
  if (!target) return;
  if (target.classList.contains('tl-segment') ||
      target.classList.contains('tl-tick') ||
      target.classList.contains('tl-tick-cluster') ||
      target.classList.contains('tl-grouped-pivot')) {
    hideTooltip();
  }
}

function tooltipForGroupedPivot(el) {
  const date = el.getAttribute('data-date');
  const count = el.getAttribute('data-count');
  const cats = (el.getAttribute('data-categories') || '').split(',');
  const labels = cats.map(c => escapeHtml(categoryLabel(c))).join('<br>');
  return `
    <strong>${escapeHtml(date)}</strong>
    <div class="tl-tt-detail" style="color:var(--yellow)">${escapeHtml(count)} categories pivoted</div>
    <div class="tl-tt-detail">${labels}</div>
  `;
}

function onTimelineMouseMove(e) {
  const tt = document.getElementById('tl-tooltip');
  if (tt.classList.contains('visible')) {
    positionTooltip(tt, e);
  }
}

function onTimelineClick(e) {
  const target = e.target;
  if (!target) return;

  // Clear previous active state
  document.querySelectorAll('#tl-tracks .tl-segment.active, #tl-tracks .tl-tick.active')
    .forEach(el => el.classList.remove('active'));

  if (target.classList.contains('tl-segment')) {
    const category = target.getAttribute('data-category');
    target.classList.add('active');
    setTimelineFilter({ type: 'category', category });
  } else if (target.classList.contains('tl-tick')) {
    const category = target.getAttribute('data-category');
    const pivotMonth = target.getAttribute('data-pivot-date');
    target.classList.add('active');
    setTimelineFilter({ type: 'tick', category, pivotMonth });
  } else if (target.classList.contains('tl-tick-cluster')) {
    // Click on cluster: filter to findings in this category that pivot within the range
    const category = target.getAttribute('data-category');
    const pivotMonth = target.getAttribute('data-pivot-date');
    const endMonth = target.getAttribute('data-pivot-end');
    setTimelineFilter({ type: 'cluster', category, pivotMonth, endMonth });
  } else if (target.classList.contains('tl-grouped-pivot')) {
    // Click on a grouped pivot: filter table to findings from ANY category
    // that pivoted at this exact month (across all categories).
    const pivotMonth = target.getAttribute('data-date');
    setTimelineFilter({ type: 'grouped', pivotMonth });
  }
}

function setTimelineFilter(filter) {
  timelineActiveFilter = filter;

  if (filter.type === 'category') {
    // Navigate to the category view (uses existing routing)
    selectCategory(filter.category);
  } else if (filter.type === 'tick') {
    // Filter to findings in this category whose first_seen or last_seen matches pivotMonth
    activeCategory = filter.category;
    // We use a dedicated filter hook in applyFilters; we also set search to a marker
    // so users see that a timeline filter is active. Simpler approach: just set activeCategory
    // and apply an inline post-filter via a flag.
    applyFilters();
    // Now post-filter filteredFindings to only keep those matching the pivot month.
    // Reset paging. the timeline narrowed the dataset, so page 0 is the new "top."
    filteredFindings = filteredFindings.filter(f =>
      f.first_seen === filter.pivotMonth || f.last_seen === filter.pivotMonth
    );
    findingsPage = 0;
    renderFindingsTable();
    showToast(`Filtered to ${categoryLabel(filter.category)} pivoting at ${filter.pivotMonth}`);
  } else if (filter.type === 'cluster') {
    activeCategory = filter.category;
    applyFilters();
    filteredFindings = filteredFindings.filter(f => {
      return (f.first_seen >= filter.pivotMonth && f.first_seen <= filter.endMonth) ||
             (f.last_seen >= filter.pivotMonth && f.last_seen <= filter.endMonth);
    });
    findingsPage = 0;
    renderFindingsTable();
    showToast(`Filtered to ${categoryLabel(filter.category)} cluster ${filter.pivotMonth} → ${filter.endMonth}`);
  } else if (filter.type === 'grouped') {
    activeCategory = null;
    applyFilters();
    filteredFindings = filteredFindings.filter(f =>
      f.first_seen === filter.pivotMonth || f.last_seen === filter.pivotMonth
    );
    findingsPage = 0;
    renderFindingsTable();
    showToast(`Filtered to all findings pivoting at ${filter.pivotMonth}`);
  }
}

function clearTimelineFilter() {
  timelineActiveFilter = null;
  document.querySelectorAll('#tl-tracks .tl-segment.active, #tl-tracks .tl-tick.active')
    .forEach(el => el.classList.remove('active'));
}

function toggleTrackVisibility(category) {
  if (timelineHiddenTracks.has(category)) {
    timelineHiddenTracks.delete(category);
  } else {
    timelineHiddenTracks.add(category);
  }
  renderTimeline();
}

function showTooltip(content, event) {
  const tt = document.getElementById('tl-tooltip');
  tt.innerHTML = content;
  tt.classList.add('visible');
  positionTooltip(tt, event);
}

function positionTooltip(tt, event) {
  const x = event.clientX;
  const y = event.clientY;
  const rect = tt.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Default: above and right of cursor
  let left = x + 12;
  let top = y - rect.height - 12;

  // Flip if going off right edge
  if (left + rect.width > vw - 8) left = x - rect.width - 12;
  // Flip if going off top edge
  if (top < 8) top = y + 16;
  // Flip if going off bottom edge
  if (top + rect.height > vh - 8) top = vh - rect.height - 8;
  // Clamp left
  if (left < 8) left = 8;

  tt.style.left = `${left}px`;
  tt.style.top = `${top}px`;
}

function tooltipForSegment(el) {
  const cat = el.getAttribute('data-category');
  const start = el.getAttribute('data-segment-start');
  const end = el.getAttribute('data-segment-end');
  const count = el.getAttribute('data-value-count');
  return `
    <strong>${escapeHtml(categoryLabel(cat))}</strong>
    <div class="tl-tt-detail">${escapeHtml(count)} value${count === '1' ? '' : 's'}</div>
    <div class="tl-tt-detail">${escapeHtml(start)} → ${escapeHtml(end)}</div>
  `;
}

function tooltipForTick(el) {
  const date = el.getAttribute('data-pivot-date');
  let added = [];
  let removed = [];
  try { added = JSON.parse(el.getAttribute('data-added') || '[]'); } catch (e) {}
  try { removed = JSON.parse(el.getAttribute('data-removed') || '[]'); } catch (e) {}
  const addedPreview = added.slice(0, 3).map(v => escapeHtml(String(v).slice(0, 40))).join('<br>');
  const removedPreview = removed.slice(0, 3).map(v => escapeHtml(String(v).slice(0, 40))).join('<br>');
  let html = `<strong>${escapeHtml(date)}</strong>`;
  if (added.length) {
    html += `<div class="tl-tt-detail" style="color:var(--green)">+ ${added.length} added</div>`;
    if (addedPreview) html += `<div class="tl-tt-detail">${addedPreview}</div>`;
  }
  if (removed.length) {
    html += `<div class="tl-tt-detail" style="color:var(--red)">− ${removed.length} removed</div>`;
    if (removedPreview) html += `<div class="tl-tt-detail">${removedPreview}</div>`;
  }
  return html;
}

function tooltipForCluster(el) {
  const date = el.getAttribute('data-pivot-date');
  const end = el.getAttribute('data-pivot-end');
  const count = el.getAttribute('data-cluster-count');
  const added = el.getAttribute('data-cluster-added');
  const removed = el.getAttribute('data-cluster-removed');
  const range = date === end ? date : `${date} → ${end}`;
  return `
    <strong>${escapeHtml(range)}</strong>
    <div class="tl-tt-detail">${escapeHtml(count)} pivots clustered</div>
    <div class="tl-tt-detail" style="color:var(--green)">+ ${escapeHtml(added)} added</div>
    <div class="tl-tt-detail" style="color:var(--red)">− ${escapeHtml(removed)} removed</div>
  `;
}

function toggleTimelineSync() {
  timelineSynced = !timelineSynced;
  const btn = document.getElementById('tl-sync-btn');
  btn.setAttribute('aria-pressed', String(timelineSynced));
  btn.textContent = timelineSynced ? 'Synced' : 'Free';
  renderTimeline();
}

function toggleTimelineShowAll() {
  timelineShowAll = !timelineShowAll;
  const btn = document.getElementById('tl-show-all-btn');
  btn.setAttribute('aria-pressed', String(timelineShowAll));
  btn.textContent = timelineShowAll ? 'Show pivot subset' : 'Show all tracks';
  renderTimeline();
}

/* ===== TIMELINE DIFF A/B ===== */

let timelineDiffMode = false;

function toggleTimelineDiff() {
  timelineDiffMode = !timelineDiffMode;
  const drawer = document.getElementById('timeline-drawer');
  const btn = document.getElementById('tl-diff-btn');
  btn.setAttribute('aria-pressed', String(timelineDiffMode));
  drawer.classList.toggle('diff-mode', timelineDiffMode);

  if (timelineDiffMode) {
    // Pre-fill the inputs with sensible defaults based on the data range
    const tracks = buildTimelineData();
    const timeAxis = computeTimeAxis(tracks);
    const a = document.getElementById('tl-diff-a');
    const b = document.getElementById('tl-diff-b');
    if (timeAxis) {
      if (!a.value) a.value = formatMonth(timeAxis.min);
      if (!b.value) b.value = formatMonth(timeAxis.max);
    }
    renderTimelineDiff();
  }
}

function renderTimelineDiff() {
  const view = document.getElementById('tl-diff-view');
  const a = document.getElementById('tl-diff-a').value;
  const b = document.getElementById('tl-diff-b').value;

  if (!a || !b) {
    view.innerHTML = '<div class="tl-diff-empty">Pick two months to compare.</div>';
    return;
  }

  const [from, to] = a <= b ? [a, b] : [b, a];

  // Source findings: respect sync toggle + active filters
  const source = timelineSynced
    ? getTimelineSourceFindings()
    : (allFindings || []);

  // For each category, compute the set of "active values" at month `from`
  // and at month `to`. A value is active at month X if its first_seen <= X
  // and its last_seen >= X.
  const byCat = {};
  for (const f of source) {
    const cat = f.category;
    if (!cat) continue;
    if (!byCat[cat]) byCat[cat] = [];
    byCat[cat].push(f);
  }

  // Filter to the pivot-worthy subset unless showAll is on
  const categories = timelineShowAll
    ? Object.keys(byCat).sort()
    : TIMELINE_DEFAULT_CATEGORIES.filter(c => byCat[c]);

  const groups = [];
  let totalAdded = 0, totalRemoved = 0, totalUnchanged = 0;

  for (const cat of categories) {
    const findings = byCat[cat] || [];
    const activeAt = month => new Set(
      findings
        .filter(f => f.first_seen && f.last_seen && f.first_seen <= month && f.last_seen >= month)
        .map(f => String(f.value || ''))
    );
    const setA = activeAt(from);
    const setB = activeAt(to);

    const added = [...setB].filter(v => !setA.has(v)).sort();
    const removed = [...setA].filter(v => !setB.has(v)).sort();
    const unchanged = [...setA].filter(v => setB.has(v)).length;

    if (added.length === 0 && removed.length === 0 && unchanged === 0) continue;

    totalAdded += added.length;
    totalRemoved += removed.length;
    totalUnchanged += unchanged;

    groups.push({ cat, added, removed, unchanged });
  }

  if (groups.length === 0) {
    view.innerHTML = `<div class="tl-diff-empty">No findings active at either date. Try a different range.</div>`;
    return;
  }

  // Sort: categories with the most changes first
  groups.sort((g1, g2) => (g2.added.length + g2.removed.length) - (g1.added.length + g1.removed.length));

  const html = groups.map(g => {
    const hdr = `
      <div class="tl-diff-cat">
        ${escapeHtml(categoryLabel(g.cat))}
        <span class="tl-diff-counts">
          <span class="tl-diff-added">+${g.added.length}</span>
          <span class="tl-diff-removed">−${g.removed.length}</span>
          <span class="tl-diff-unchanged">=${g.unchanged}</span>
        </span>
      </div>
    `;
    const items = [
      ...g.added.slice(0, 20).map(v => `<div class="tl-diff-item added">${escapeHtml(String(v).slice(0, 200))}</div>`),
      ...g.removed.slice(0, 20).map(v => `<div class="tl-diff-item removed">${escapeHtml(String(v).slice(0, 200))}</div>`),
    ].join('');
    const extra = (g.added.length > 20 || g.removed.length > 20)
      ? `<div class="tl-diff-item" style="color:var(--text-dim)">…capped at 20 per side</div>`
      : '';
    return `<div class="tl-diff-group">${hdr}<div class="tl-diff-list">${items}${extra}</div></div>`;
  }).join('');

  // Stats footer
  document.getElementById('tl-stats').textContent =
    `Diff ${from} → ${to} · +${totalAdded} −${totalRemoved} =${totalUnchanged}`;

  view.innerHTML = html;
}

function hideTooltip() {
  const tt = document.getElementById('tl-tooltip');
  if (tt) tt.classList.remove('visible');
}

// Escape key closes the drawer
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && timelineOpen) closeTimeline();
});

function parseMonth(str) {
  // Parse "YYYY-MM" into UTC milliseconds at the 1st of the month.
  if (!str || typeof str !== 'string') return null;
  const m = str.match(/^(\d{4})-(\d{2})$/);
  if (!m) return null;
  const year = parseInt(m[1], 10);
  const month = parseInt(m[2], 10);
  if (month < 1 || month > 12) return null;
  return Date.UTC(year, month - 1, 1);
}

function formatMonth(ms) {
  if (ms == null || isNaN(ms)) return '-';
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

function categoryLabel(cat) {
  return t(TIMELINE_CATEGORY_LABELS[cat] || cat.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()));
}

function buildTimelineData() {
  // Decide the source list: filtered if synced, all otherwise
  const source = timelineSynced ? getTimelineSourceFindings() : (allFindings || []);

  // Decide which categories to show
  let categories;
  if (timelineShowAll) {
    categories = [...new Set(source.map(f => f.category).filter(Boolean))].sort();
  } else {
    categories = TIMELINE_DEFAULT_CATEGORIES.slice();
  }

  // Build a track for each category
  const tracks = categories.map(cat => {
    const catFindings = source.filter(f => f.category === cat);

    if (catFindings.length === 0) {
      return { category: cat, empty: true, segment: null, pivots: [], valueCount: 0 };
    }

    // Compute segment
    const starts = [];
    const ends = [];
    const pivotMap = new Map();  // ms -> { added: [], removed: [] }

    for (const f of catFindings) {
      const fs = parseMonth(f.first_seen);
      const ls = parseMonth(f.last_seen);
      if (fs == null || ls == null) continue;
      starts.push(fs);
      ends.push(ls);

      if (!pivotMap.has(fs)) pivotMap.set(fs, { added: [], removed: [] });
      pivotMap.get(fs).added.push(f.value);
      if (!pivotMap.has(ls)) pivotMap.set(ls, { added: [], removed: [] });
      pivotMap.get(ls).removed.push(f.value);
    }

    if (starts.length === 0) {
      return { category: cat, empty: true, segment: null, pivots: [], valueCount: 0 };
    }

    const segmentStart = starts.reduce((a, b) => a < b ? a : b);
    const segmentEnd = ends.reduce((a, b) => a > b ? a : b);

    const pivots = [...pivotMap.entries()]
      .map(([date_ms, delta]) => ({ date_ms, ...delta }))
      .sort((a, b) => a.date_ms - b.date_ms);

    // Highest-severity finding wins for the track tint. Tie-break by tier so
    // a single LEAK in a CONTEXT-heavy category still flags the track red.
    let severity = 'BACKGROUND';
    for (const f of catFindings) {
      const v = osintValue(f);
      if (v === 'LEAK') { severity = 'LEAK'; break; }
      if (v === 'PIVOT' && severity !== 'LEAK') severity = 'PIVOT';
      else if (v === 'CONTEXT' && severity !== 'LEAK' && severity !== 'PIVOT') severity = 'CONTEXT';
    }

    return {
      category: cat,
      empty: false,
      segment: { start: segmentStart, end: segmentEnd },
      pivots,
      valueCount: catFindings.length,
      severity,
    };
  });

  return tracks;
}

function getTimelineSourceFindings() {
  // Apply same filter logic as applyFilters() but without requiring activeCategory.
  // Respect severity filter and text search, but NOT the category tile (we want
  // all categories to show in the timeline even if the user is focused on one in the table).
  const sevFilter = document.getElementById('filter-severity').value;
  const searchFilter = (document.getElementById('filter-search').value || '').toLowerCase();

  return (allFindings || []).filter(f => {
    if (sevFilter && osintValue(f) !== sevFilter) return false;
    if (searchFilter && !String(f.value || '').toLowerCase().includes(searchFilter)) return false;
    return true;
  });
}

function computeTimeAxis(tracks) {
  const nonEmpty = tracks.filter(t => !t.empty);
  if (nonEmpty.length === 0) return null;
  const allStarts = nonEmpty.map(t => t.segment.start);
  const allEnds = nonEmpty.map(t => t.segment.end);
  const min = allStarts.reduce((a, b) => a < b ? a : b);
  const max = allEnds.reduce((a, b) => a > b ? a : b);
  const span = max - min;
  const padding = span > 0 ? span * 0.02 : 2592000000;  // 2% or 30 days if single-point
  return { min: min - padding, max: max + padding };
}

function renderTimeAxis(timeAxis) {
  const container = document.getElementById('tl-axis');
  if (!timeAxis) {
    container.innerHTML = '';
    return;
  }

  const spanMs = timeAxis.max - timeAxis.min;
  const spanDays = spanMs / 86400000;
  const spanYears = spanDays / 365.25;

  // Decide granularity
  let granularity;
  if (spanYears >= 3) granularity = 'year';
  else if (spanYears >= 0.5) granularity = 'quarter';
  else granularity = 'month';

  // Generate tick dates
  const ticks = [];
  const startDate = new Date(timeAxis.min);
  let cursor;

  if (granularity === 'year') {
    cursor = new Date(Date.UTC(startDate.getUTCFullYear(), 0, 1));
    while (cursor.getTime() <= timeAxis.max) {
      if (cursor.getTime() >= timeAxis.min) {
        ticks.push({ ms: cursor.getTime(), label: String(cursor.getUTCFullYear()) });
      }
      cursor = new Date(Date.UTC(cursor.getUTCFullYear() + 1, 0, 1));
    }
  } else if (granularity === 'quarter') {
    const startYear = startDate.getUTCFullYear();
    const startQuarter = Math.floor(startDate.getUTCMonth() / 3);
    cursor = new Date(Date.UTC(startYear, startQuarter * 3, 1));
    while (cursor.getTime() <= timeAxis.max) {
      if (cursor.getTime() >= timeAxis.min) {
        const q = Math.floor(cursor.getUTCMonth() / 3) + 1;
        ticks.push({ ms: cursor.getTime(), label: `Q${q} ${cursor.getUTCFullYear()}` });
      }
      cursor = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 3, 1));
    }
  } else {
    cursor = new Date(Date.UTC(startDate.getUTCFullYear(), startDate.getUTCMonth(), 1));
    while (cursor.getTime() <= timeAxis.max) {
      if (cursor.getTime() >= timeAxis.min) {
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        ticks.push({ ms: cursor.getTime(), label: `${months[cursor.getUTCMonth()]} ${cursor.getUTCFullYear()}` });
      }
      cursor = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1));
    }
  }

  // Build SVG string
  const viewBoxW = 1000;
  const scale = v => ((v - timeAxis.min) / (timeAxis.max - timeAxis.min)) * viewBoxW;

  // Offset for the label gutter (140px in layout, but axis doesn't have a gutter. it spans full width)
  let svg = `<svg viewBox="0 0 ${viewBoxW} 24" preserveAspectRatio="none">`;
  // Baseline
  svg += `<line class="tl-axis-tick" x1="0" y1="16" x2="${viewBoxW}" y2="16"/>`;
  for (const t of ticks) {
    const x = scale(t.ms);
    svg += `<line class="tl-axis-tick" x1="${x.toFixed(1)}" y1="12" x2="${x.toFixed(1)}" y2="20"/>`;
    svg += `<text class="tl-axis-label" x="${x.toFixed(1)}" y="9" text-anchor="middle">${t.label}</text>`;
  }
  svg += '</svg>';

  container.innerHTML = svg;
}

function clusterPivots(pivots, timeAxis, svgWidth) {
  // Merge pivots that would be closer than 4 pixels from each other when rendered.
  // Returns a new array where some entries may be clusters (multiple original pivots merged).
  if (pivots.length === 0) return [];

  const scale = ms => ((ms - timeAxis.min) / (timeAxis.max - timeAxis.min)) * svgWidth;
  const MIN_PX = 4;

  const clustered = [];
  let currentCluster = null;

  for (const p of pivots) {
    const px = scale(p.date_ms);
    // Compare against the rightmost member of the current cluster (not its moving
    // average) so the "within 4px" rule is intuitive: each new pivot is merged only
    // if it's within MIN_PX of the previous member.
    if (currentCluster && (px - currentCluster.pxRight) < MIN_PX) {
      currentCluster.members.push(p);
      currentCluster.pxRight = px;
      currentCluster.dateEnd_ms = p.date_ms;
      // Keep a center for rendering position (average of extremes)
      currentCluster.pxCenter = (currentCluster.pxLeft + currentCluster.pxRight) / 2;
    } else {
      currentCluster = {
        pxLeft: px,
        pxRight: px,
        pxCenter: px,
        date_ms: p.date_ms,
        dateEnd_ms: p.date_ms,
        members: [p],
      };
      clustered.push(currentCluster);
    }
  }

  // Expand: single-member clusters become plain ticks, multi-member become clusters
  return clustered.map(c => {
    if (c.members.length === 1) {
      return { type: 'tick', ...c.members[0] };
    }
    // Aggregate added/removed across members
    const added = [];
    const removed = [];
    for (const m of c.members) {
      added.push(...m.added);
      removed.push(...m.removed);
    }
    return {
      type: 'cluster',
      date_ms: c.date_ms,
      dateEnd_ms: c.dateEnd_ms,
      pxCenter: c.pxCenter,
      memberCount: c.members.length,
      added,
      removed,
    };
  });
}

/* ===== HISTORY ===== */
async function loadHistory() {
  // v2: the History view is now "My scans" backed by the account. The legacy
  // v1 domains table is hidden in public mode.
  await renderMyScans();
}

async function renderMyScans() {
  const host = $('my-scans');
  if (!host) return;

  // Solo / self-hosted build: list EVERY scan this instance has run (published
  // or not) - it's a single-user install, so they are all yours.
  host.innerHTML = '<div class="myscans-note">' + t('Loading your scans…') + '</div>';
  let items = [];
  try { const r = await fetch(API + '/api/local-scans?limit=50'); const d = await r.json(); items = d.scans || []; } catch (_) {}
  if (!items.length) {
    host.innerHTML = '<div class="myscans-note">' + t('No scans yet.')
      + '<div><button class="btn btn-accent" onclick="location.hash=\'#/\';document.getElementById(\'domain-input\').focus()">' + t('Run a scan') + '</button></div></div>';
    return;
  }
  host.innerHTML = '<div class="myscans-list">' + items.map(s => `
    <div class="myscans-row" onclick="location.hash='#/s/${encodeURIComponent(s.url_id)}'">
      <span class="myscans-domain">${esc(s.domain)}</span>
      <span class="myscans-status st-${esc(s.status)}">${esc(t(s.status))}</span>
      <span class="myscans-pub ${s.is_published ? 'on' : 'off'}">${s.is_published ? t('Public') : t('Private')}</span>
      <span class="myscans-date">${esc((s.created_at || '').slice(0, 10))}</span>
      <button class="myscans-del" title="${esc(t('Delete scan'))}" aria-label="${esc(t('Delete scan'))}" onclick="deleteMyScan('${esc(s.url_id)}', event)">&times;</button>
    </div>`).join('') + '</div>';
}

// Delete a scan the current visitor owns (the url_id is the capability). Used
// from the My scans list; the row itself navigates, so stop propagation.
async function deleteMyScan(urlId, ev) {
  if (ev) ev.stopPropagation();
  if (!confirm(t('Delete this scan permanently? This cannot be undone.'))) return;
  try {
    const r = await fetch(API + '/api/s/' + encodeURIComponent(urlId), { method: 'DELETE' });
    if (!r.ok && r.status !== 404) {
      const d = await r.json().catch(() => ({}));
      showToast('Error: ' + (d.detail || r.statusText));
      return;
    }
    renderMyScans();
  } catch (e) {
    showToast('Network error: ' + e.message);
  }
}


// Full-text search across the scanned page CONTENT (not just the pivots).
// Snippets come from the server wrapped in <mark>; the underlying page text is
// stored tag-stripped, so we escape everything and re-allow only <mark>.
function _sanitizeSnippet(s) {
  return esc(String(s || '')).split('&lt;mark&gt;').join('<mark>').split('&lt;/mark&gt;').join('</mark>');
}

async function runPageSearch() {
  const box = document.getElementById('pagesearch-results');
  const q = (document.getElementById('pagesearch-input').value || '').trim();
  if (!box) return;
  if (!q || !publicScanUrlId) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="ps-note">${esc(t('Searching…'))}</div>`;
  try {
    const r = await fetch(API + '/api/s/' + encodeURIComponent(publicScanUrlId) + '/search?q=' + encodeURIComponent(q));
    if (!r.ok) { box.innerHTML = `<div class="ps-note">${esc(t('Search failed.'))}</div>`; return; }
    const d = await r.json();
    if (!d.results || !d.results.length) {
      box.innerHTML = `<div class="ps-note">${esc(t('No pages matched.'))}</div>`;
      return;
    }
    const head = `<div class="ps-count">${d.results.length} ${esc(t('pages'))}</div>`;
    box.innerHTML = head + d.results.map((res) => {
      const ts = String(res.timestamp || '');
      const wb = 'https://web.archive.org/web/' + encodeURIComponent(ts) + '/' + encodeURIComponent(res.url || '');
      const date = ts.slice(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
      return `<a class="ps-hit" href="${esc(wb)}" target="_blank" rel="noopener">`
        + `<div class="ps-hit-head"><span class="ps-hit-url">${esc(res.url || '')}</span><span class="ps-hit-date">${esc(date)}</span></div>`
        + `<div class="ps-hit-snippet">${_sanitizeSnippet(res.snippet)}</div></a>`;
    }).join('');
  } catch (e) {
    box.innerHTML = `<div class="ps-note">${esc(t('Search failed.'))}</div>`;
  }
}

const debouncedPageSearch = _debounce(() => runPageSearch(), 300);








/* ===== CROSS-DOMAIN COMPARE ===== */



/* ===== KEYBOARD SHORTCUTS ===== */

function showKbHelp() {
  document.getElementById('kb-overlay').classList.add('visible');
}

function closeKbHelp() {
  document.getElementById('kb-overlay').classList.remove('visible');
}

document.addEventListener('keydown', (e) => {
  // Skip shortcuts while typing in an input/textarea/select. except Esc
  const tag = (document.activeElement && document.activeElement.tagName) || '';
  const isTyping = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

  if (e.key === 'Escape') {
    // Esc always handled: closes any open drawer/overlay
    if (document.getElementById('kb-overlay').classList.contains('visible')) {
      closeKbHelp();
      return;
    }
    // Timeline / export / finding drawers all register their own Esc handler
    // separately. nothing to do here.
    return;
  }

  if (isTyping) return;

  // Don't interfere with modifier-combos (Ctrl+R, Cmd+K, etc.)
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const key = e.key;
  const currentView = (location.hash || '#/').replace('#/', '').split('/')[0] || 'home';
  const onResults = currentView === 'results';

  if (key === '?') {
    e.preventDefault();
    showKbHelp();
    return;
  }

  if (key === 't' && onResults) {
    e.preventDefault();
    // Switch the report to its Activity view (the old timeline drawer was
    // replaced by report2's Activity view).
    if (typeof report2SetView === 'function') report2SetView('activity');
    return;
  }

  if (key === 'e' && onResults) {
    e.preventDefault();
    const expOpen = document.getElementById('export-drawer').classList.contains('open');
    if (expOpen) closeExportDrawer();
    else openExportDrawer();
    return;
  }

  if (key === '/' && onResults) {
    e.preventDefault();
    // Focus the report's findings filter (severity filter/tiers were removed).
    const input = document.getElementById('r2-filter');
    if (input) { input.focus(); input.select(); }
    return;
  }

  if (key === 'h') {
    e.preventDefault();
    location.hash = '#/history';
    return;
  }

  if (key === 'n') {
    e.preventDefault();
    location.hash = '#/';
    setTimeout(() => {
      const input = document.getElementById('domain-input');
      if (input) input.focus();
    }, 50);
    return;
  }
});

/* ===== INIT ===== */
(async () => {
  try {
    const r = await fetch(API + '/api/health');
    if (r.ok) console.log('WayTrace backend connected');
  } catch (e) {
    console.warn('Backend unreachable:', e.message);
  }
})();
