/* ===== STATE ===== */
const API = '';
let currentDomainId = null;

/* ===== v2 PUBLIC STATE ===== */
let publicScanUrlId = null;
let _lastScanPayload = null;   // the scan the results view is showing
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

// Poll the unified service status and surface a single banner with priority:
// maintenance > high traffic > admin notice.
// A run of genuine
// NETWORK failures (the API unreachable, fetch throws) flips to a maintenance
// notice; a mere non-200 (429/502/503 blip under launch load) does NOT, so a
// busy moment never masquerades as an outage.
//
// The banner no longer reports on archive.org's health. It used to show "slow"
// or "paused" from archive_health, and two failed CDX attempts inside two
// minutes were enough to light it, which is exactly what a failing scan
// produces. So a scan that failed lit a banner blaming archive.org, next to a
// failure message blaming archive.org, for what was measured on 2026-09-06 to
// be our own deadline running out. The breaker itself is untouched and still
// protects the server: when it is open, POST /api/scan answers 503 with the
// reason, at the moment the person acts, which is where that belongs.
let _statusNetFailStreak = 0;
let _lastSvc = null;          // last /api/service-status service object
let _setupRedirected = false; // one-shot guard for the first-run wizard gate
async function checkServiceStatus() {
  const el = $('archive-banner');
  if (!el) return;
  let r;
  try {
    r = await fetch(API + '/api/service-status');
  } catch (_) {
    // True network error: the server is unreachable, not just busy.
    _statusNetFailStreak += 1;
    if (_statusNetFailStreak >= 3) {
      _showStatusBanner('maintenance', t('Maintenance in progress. WayTrace will be back shortly.'));
    }
    return;
  }
  _statusNetFailStreak = 0;
  if (!r.ok) {
    // Transient HTTP error (rate limit / proxy hiccup): leave the current
    // banner as-is rather than declaring an outage.
    return;
  }
  let d;
  try { d = await r.json(); } catch (_) { return; }
  const svc = (d && d.service) || {};
  const arc = (d && d.archive) || {};
  _lastSvc = svc;
  // First-run gate (self-host only): a fresh instance with the config panel on
  // and setup not yet completed lands the operator in the wizard once. The
  // guard makes this a one-shot so a later manual navigation never bounces.
  if (svc.config_panel && svc.setup_completed === false && !_setupRedirected) {
    _setupRedirected = true;
    const h = location.hash || '#/';
    if (h !== '#/setup' && h.indexOf('#/s/') !== 0) { location.hash = '#/setup'; return; }
  }
  renderHomeStatus(svc, arc);
  if (svc.state === 'maintenance') {
    _showStatusBanner('maintenance', svc.maintenance_message ||
      t('Maintenance in progress. Scanning may be unavailable for a short while.'));
  } else if (svc.state === 'busy') {
    _showStatusBanner('busy',
      t('WayTrace is a victim of its own success right now. New scans are queued and start as soon as a slot frees up.'));
  } else if (svc.notice) {
    // Admin-set informational banner: the service is fully available, this
    // only sets expectations (e.g. slower scans under high traffic).
    _showStatusBanner('notice', svc.notice);
  } else {
    el.hidden = true;
  }
}

function _showStatusBanner(kind, msg) {
  const el = $('archive-banner');
  if (!el) return;
  el.className = 'archive-banner ' + kind;
  el.textContent = msg;
  el.hidden = false;
}

// _archiveStatusMessage was removed with the archive.org banner. The breaker
// still answers at the point of action: POST /api/scan returns 503 with
// archive_health's own message when it is open, and a failed scan carries a
// cause and a sentence of its own. Nothing needs a standing diagnostic on a
// third party's health.

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
/* The old button label had to mean both the state and the action: "Light"
   could be read as "you are in light" or "click for light". The menu states
   both modes and ticks the one in use, so there is nothing to interpret. */
function applyThemeLabel() { if (!$('pref-menu')?.hidden) renderPrefMenu(); }

function setMode(mode) {
  const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  if (mode !== cur) toggleTheme();
  renderPrefMenu();
}

function renderPrefMenu() {
  const el = $('pref-menu');
  if (!el) return;
  const mode = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  const tick = (on) => `<span class="nav-tick">${on ? '✓' : ''}</span>`;
  el.innerHTML =
    `<div class="nav-drop-head">${esc(t('Mode'))}</div>`
    + `<button class="nav-drop-item" role="menuitemradio" aria-checked="${mode === 'dark'}" onclick="setMode('dark')">${tick(mode === 'dark')}${esc(t('mode.dark'))}</button>`
    + `<button class="nav-drop-item" role="menuitemradio" aria-checked="${mode === 'light'}" onclick="setMode('light')">${tick(mode === 'light')}${esc(t('mode.light'))}</button>`
    + `<div class="nav-drop-sep"></div>`
    + `<div class="nav-drop-head">${esc(t('Language'))}</div>`
    + `<button class="nav-drop-item" role="menuitemradio" aria-checked="${LANG === 'fr'}" onclick="setLang('fr');renderPrefMenu()">${tick(LANG === 'fr')}Français</button>`
    + `<button class="nav-drop-item" role="menuitemradio" aria-checked="${LANG === 'en'}" onclick="setLang('en');renderPrefMenu()">${tick(LANG === 'en')}English</button>`
    + `<div class="nav-drop-sep"></div>`
    + `<button class="nav-drop-item" role="menuitem" onclick="hidePrefMenu();location.hash='#/themes'">`
    + `<span class="nav-tick"></span>${esc(t('All palettes'))}</button>`;
}

function togglePrefMenu() {
  const el = $('pref-menu');
  if (!el) return;
  const open = el.hidden;
  if (open) renderPrefMenu();
  el.hidden = !open;
  $('pref-btn')?.setAttribute('aria-expanded', String(open));
}
function hidePrefMenu() {
  const el = $('pref-menu');
  if (el) el.hidden = true;
  $('pref-btn')?.setAttribute('aria-expanded', 'false');
}
function toggleTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  if (next === 'light') document.documentElement.setAttribute('data-theme', 'light');
  else document.documentElement.removeAttribute('data-theme');
  try { localStorage.setItem('wt_theme', next); } catch (_) {}
  applyThemeLabel();
  // Re-emit the palette for the new mode and persist the preference.
  applyThemeVars();
  const p = currentThemePref();
  if (p) { p.mode = next; setThemePref(p); }
}
applyThemeLabel();

/* ===== THEMES (presets + custom palette) =====
   A theme is {preset, mode, custom?}. Preset seeds hold only a background HSL
   and an accent hex per mode; every other color (surfaces, borders, accent
   ramp) is derived so any seed yields a coherent UI. The computed variables
   are cached in localStorage (wt_theme_vars) so the boot script can restore
   them before first paint. */

const THEME_MANAGED_VARS = [
  '--bg', '--surface', '--surface2', '--surface3', '--border', '--border-hover',
  '--accent', '--accent-dim', '--accent-soft', '--accent-glow', '--accent-glow2',
  '--orange', '--orange-dim', '--pivot', '--pivot-soft',
  '--text', '--text-dim', '--text-faint', '--text-bright',
];

// Seeds: d/l = dark/light, bg = [h,s,l], ac = accent hex, x = extra overrides.
// Several themes retint the text ramp too, so switching really changes the
// whole mood of the app, not just the accent. Names stay accent-free.
const THEME_PRESETS = [
  {id: 'truffe',   name: 'Truffe',   d: {bg: [24, 12, 7.5],  ac: '#E87A48'}, l: {bg: [220, 12, 95.5], ac: '#C7541F', x: {'--pivot': '#33566B', '--pivot-soft': '#D8E2EA'}}},
  {id: 'encre',    name: 'Encre',    d: {bg: [222, 18, 8],   ac: '#5B9DF0'}, l: {bg: [220, 24, 94.5], ac: '#2360C0'}},
  {id: 'fjord',    name: 'Fjord',    d: {bg: [220, 16, 13],  ac: '#88C0D0', x: {'--text': '#E5E9F0', '--text-dim': '#AEB6C3', '--text-faint': '#939CAB', '--text-bright': '#ECEFF4'}}, l: {bg: [219, 28, 95],  ac: '#46698E'}},
  {id: 'retro',    name: 'Retro',    d: {bg: [0, 0, 13],     ac: '#FE8019', x: {'--text': '#EBDBB2', '--text-dim': '#BDAE93', '--text-faint': '#A89984', '--text-bright': '#FBF1C7'}}, l: {bg: [48, 70, 89],   ac: '#AF3A03', x: {'--text': '#3C3836', '--text-dim': '#665C54', '--text-faint': '#7C6F64', '--text-bright': '#282828'}}},
  {id: 'vampire',  name: 'Vampire',  d: {bg: [231, 15, 12],  ac: '#BD93F9', x: {'--text': '#F8F8F2', '--text-dim': '#B6B9C7', '--text-faint': '#9A9DB0', '--text-bright': '#FFFFFF'}}, l: {bg: [232, 24, 95],  ac: '#6E3BC4'}},
  {id: 'tokyo',    name: 'Tokyo',    d: {bg: [235, 19, 11],  ac: '#7AA2F7', x: {'--text': '#C0CAF5', '--text-dim': '#8A94C4', '--text-faint': '#767FA8', '--text-bright': '#E4EAFF'}}, l: {bg: [230, 30, 94.5], ac: '#34548A'}},
  {id: 'pastel',   name: 'Pastel',   d: {bg: [240, 21, 13],  ac: '#CBA6F7', x: {'--text': '#CDD6F4', '--text-dim': '#9AA3C7', '--text-faint': '#8188AB', '--text-bright': '#E6ECFF'}}, l: {bg: [220, 23, 95],  ac: '#7A2EC4'}},
  {id: 'mousse',   name: 'Mousse',   d: {bg: [160, 10, 11],  ac: '#A7C080', x: {'--text': '#D3C6AA', '--text-dim': '#A29B84', '--text-faint': '#8C866F', '--text-bright': '#E8DFC5'}}, l: {bg: [90, 20, 93],   ac: '#4A7A46'}},
  {id: 'estampe',  name: 'Estampe',  d: {bg: [240, 13, 12],  ac: '#7FB4CA', x: {'--text': '#DCD7BA', '--text-dim': '#A8A48D', '--text-faint': '#918D77', '--text-bright': '#EFEAD2'}}, l: {bg: [43, 30, 92],   ac: '#2D5F7A'}},
  {id: 'solaire',  name: 'Solaire',  d: {bg: [192, 80, 10],  ac: '#CB4B16', x: {'--text': '#B9C4C4', '--text-dim': '#8CA0A0', '--text-faint': '#7A9090', '--text-bright': '#EEE8D5'}}, l: {bg: [44, 70, 94],   ac: '#1E6FA8'}},
  {id: 'horizon',  name: 'Horizon',  d: {bg: [345, 14, 10],  ac: '#E95678', x: {'--text': '#EDE3E6', '--text-dim': '#BBA8AE', '--text-faint': '#A08D93', '--text-bright': '#FBF3F5'}}, l: {bg: [20, 55, 94],   ac: '#C4304E'}},
  {id: 'sakura',   name: 'Sakura',   d: {bg: [340, 12, 8.5], ac: '#F08AAE'}, l: {bg: [345, 38, 95],   ac: '#B83A64'}},
  {id: 'miel',     name: 'Miel',     d: {bg: [40, 18, 8.5],  ac: '#E8B420'}, l: {bg: [44, 52, 92.5],  ac: '#8A6410'}},
  {id: 'terminal', name: 'Terminal', d: {bg: [145, 10, 6],   ac: '#4ADE80', x: {'--text': '#D8F0DC', '--text-dim': '#8FB89A', '--text-faint': '#78A183', '--text-bright': '#F0FFF4'}}, l: {bg: [140, 10, 95.5], ac: '#15803D'}},
  {id: 'neon',     name: 'Neon',     d: {bg: [0, 0, 7],      ac: '#FF5C8A'}, l: {bg: [340, 8, 95.5],  ac: '#C42360'}},
  {id: 'lagon',    name: 'Lagon',    d: {bg: [187, 28, 8.5], ac: '#4BC8BE'}, l: {bg: [180, 35, 94],   ac: '#0A7A70'}},
  {id: 'rouille',  name: 'Rouille',  d: {bg: [204, 12, 9],   ac: '#DE7A44'}, l: {bg: [210, 14, 94.5], ac: '#B0521C'}},
  {id: 'bordeaux', name: 'Bordeaux', d: {bg: [350, 16, 8.5], ac: '#E07A8A'}, l: {bg: [8, 28, 94.5],   ac: '#A03048'}},
  {id: 'brume',    name: 'Brume',    d: {bg: [220, 3, 10.5], ac: '#C2C8D0'}, l: {bg: [220, 5, 95],    ac: '#3E4650'}},
  {id: 'galerie',  name: 'Galerie',  d: {bg: [40, 5, 8],     ac: '#E8E2D2'}, l: {bg: [40, 12, 95.5],  ac: '#26221C'}},
];

function _hslHex(h, s, l) {
  s = Math.max(0, Math.min(100, s)) / 100; l = Math.max(0, Math.min(100, l)) / 100;
  const k = n => (n + ((h % 360) + 360) % 360 / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = n => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))));
  const to2 = v => v.toString(16).padStart(2, '0').toUpperCase();
  return '#' + to2(f(0)) + to2(f(8)) + to2(f(4));
}
function _hexHsl(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
  if (mx === mn) return [0, 0, l * 100];
  const d = mx - mn;
  const s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
  let h;
  if (mx === r) h = ((g - b) / d + (g < b ? 6 : 0)) * 60;
  else if (mx === g) h = ((b - r) / d + 2) * 60;
  else h = ((r - g) / d + 4) * 60;
  return [h, s * 100, l * 100];
}
function _hexRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function _darkRamp(bg) {
  const [h, s, l] = bg;
  return {
    '--bg': _hslHex(h, s, l), '--surface': _hslHex(h, s + 1, l + 2.5),
    '--surface2': _hslHex(h, s + 2, l + 6), '--surface3': _hslHex(h, s + 3.5, l + 10),
    '--border': _hslHex(h, s + 3.5, l + 10), '--border-hover': _hslHex(h, s + 8, l + 20),
  };
}
function _lightRamp(bg) {
  const [h, s, l] = bg;
  return {
    '--bg': _hslHex(h, s, l), '--surface': _hslHex(h, s, Math.min(l + 2.5, 97)),
    '--surface2': _hslHex(h, s + 6, l - 5.5), '--surface3': _hslHex(h, s + 8, l - 12),
    '--border': _hslHex(h, s + 6, l - 14), '--border-hover': _hslHex(h, s + 4, l - 28),
  };
}
function _accentVars(ac, mode) {
  const [h, s, l] = _hexHsl(ac);
  const [r, g, b] = _hexRgb(ac);
  if (mode === 'dark') {
    return {
      '--accent': ac, '--accent-dim': _hslHex(h, s, l - 9),
      '--accent-soft': _hslHex(h, 30, 17),
      '--accent-glow': `rgba(${r},${g},${b},.06)`, '--accent-glow2': `rgba(${r},${g},${b},.14)`,
      '--orange': ac, '--orange-dim': `rgba(${r},${g},${b},.14)`,
    };
  }
  return {
    '--accent': ac, '--accent-dim': _hslHex(h, s, Math.max(l - 8, 20)),
    '--accent-soft': _hslHex(h, 55, 88),
    '--accent-glow': `rgba(${r},${g},${b},.06)`, '--accent-glow2': `rgba(${r},${g},${b},.12)`,
    '--orange': ac, '--orange-dim': `rgba(${r},${g},${b},.10)`,
  };
}

// -> {dark: {...}, light: {...}} or null when the pref is empty/unknown.
function computeThemeVars(pref) {
  if (!pref) return null;
  if (pref.preset === 'custom' && pref.custom) {
    const c = pref.custom;
    if (!c.darkBg || !c.darkAccent || !c.lightBg || !c.lightAccent) return null;
    return {
      dark: Object.assign(_darkRamp(_hexHsl(c.darkBg)), _accentVars(c.darkAccent, 'dark')),
      light: Object.assign(_lightRamp(_hexHsl(c.lightBg)), _accentVars(c.lightAccent, 'light')),
    };
  }
  const p = THEME_PRESETS.find(x => x.id === pref.preset);
  if (!p) return null;
  return {
    dark: Object.assign(_darkRamp(p.d.bg), _accentVars(p.d.ac, 'dark'), p.d.x || {}),
    light: Object.assign(_lightRamp(p.l.bg), _accentVars(p.l.ac, 'light'), p.l.x || {}),
  };
}

function currentThemePref() {
  try { return JSON.parse(localStorage.getItem('wt_theme_pref') || 'null'); } catch (_) { return null; }
}

function applyThemeVars() {
  const root = document.documentElement;
  THEME_MANAGED_VARS.forEach(v => root.style.removeProperty(v));
  // Precedence: a saved localStorage choice wins; otherwise the instance
  // default theme injected by the server (self-host) applies transiently so it
  // survives light/dark toggles without ever becoming a persisted user choice.
  let pref = currentThemePref();
  if (!pref) {
    const d = (typeof window.__WT_DEFAULTS__ === 'object' && window.__WT_DEFAULTS__) || null;
    if (d && d.theme) pref = {preset: d.theme};
  }
  const vars = computeThemeVars(pref);
  if (vars) {
    const mode = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const set = vars[mode];
    for (const k in set) root.style.setProperty(k, set[k]);
  }
  syncThemeColor();
}

/* The browser paints its own chrome (the Android address bar, the iOS status
   bar, a PWA splash) with whatever <meta name="theme-color"> says. That tag
   held #1C1B18, a colour the palette stopped using, so the bar sat a shade
   off the page it framed. It cannot be a static value either: the mode
   toggles and the themes page swaps whole palettes. Read the ground the page
   is actually painted on and follow it. */
function syncThemeColor() {
  const bg = getComputedStyle(document.documentElement)
    .getPropertyValue('--bg').trim();
  if (!bg) return;
  let m = document.querySelector('meta[name="theme-color"]');
  if (!m) {
    m = document.createElement('meta');
    m.setAttribute('name', 'theme-color');
    document.head.appendChild(m);
  }
  m.setAttribute('content', bg);
}

// Persist locally (pref + precomputed vars for the boot script), apply, and
// persist the chosen theme. `pref === null` resets to the default.
function setThemePref(pref, opts) {
  try {
    if (pref) {
      localStorage.setItem('wt_theme_pref', JSON.stringify(pref));
      const vars = computeThemeVars(pref);
      if (vars) localStorage.setItem('wt_theme_vars', JSON.stringify(vars));
      else localStorage.removeItem('wt_theme_vars');
    } else {
      localStorage.removeItem('wt_theme_pref');
      localStorage.removeItem('wt_theme_vars');
    }
  } catch (_) {}
  applyThemeVars();
  _markActiveThemeCard();
  if (!(opts && opts.localOnly) && typeof _syncThemeToServer === 'function') _syncThemeToServer(pref);
}


/* --- Instance defaults (self-host) ---
   The server injects window.__WT_DEFAULTS__ = {theme, name} before paint when
   an instance name / default theme is set. The theme is handled by the boot
   script (mode) + applyThemeVars() (full vars); here we apply the name and
   expose small helpers reused by the setup wizard. */
function setInstanceName(name) {
  const brand = document.querySelector('.nav-brand');
  const n = (name || '').trim();
  if (!n) return;
  if (brand) brand.textContent = n;
  document.title = n;
}

// Apply a preset's full palette right now WITHOUT persisting it, so it stays a
// preview / default and any later explicit user choice still wins.
function _applyPresetTransient(pref) {
  const root = document.documentElement;
  const vars = computeThemeVars(pref);
  THEME_MANAGED_VARS.forEach(v => root.style.removeProperty(v));
  if (!vars) return;
  const mode = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  const set = vars[mode];
  for (const k in set) root.style.setProperty(k, set[k]);
}

// Read the running version from the footer string so the UA preview stays in
// sync with the shipped version without a second hardcoded constant.
let WT_VERSION = '';   // filled from /api/health at boot, single source

function _instanceVersion() { return WT_VERSION; }

// Apply the injected instance defaults on load. Theme vars are already handled
// by applyThemeVars()'s __WT_DEFAULTS__ fallback; here we only set the name.
function applyInstanceDefaults() {
  const d = (typeof window.__WT_DEFAULTS__ === 'object' && window.__WT_DEFAULTS__) || null;
  if (d && d.name) setInstanceName(d.name);
}

/* --- Themes page --- */
function _themeSeedHexes(p) {
  return {
    darkBg: _hslHex(p.d.bg[0], p.d.bg[1], p.d.bg[2]), darkAccent: p.d.ac,
    lightBg: _hslHex(p.l.bg[0], p.l.bg[1], p.l.bg[2]), lightAccent: p.l.ac,
  };
}

function _markActiveThemeCard() {
  const grid = $('themes-grid');
  if (!grid) return;
  const pref = currentThemePref();
  const active = pref ? (pref.preset || '') : 'truffe';
  grid.querySelectorAll('.theme-card').forEach(c =>
    c.classList.toggle('active', c.dataset.theme === active));
  const cc = $('theme-custom-card');
  if (cc) cc.classList.toggle('active', active === 'custom');
}

function renderThemesPage() {
  const grid = $('themes-grid');
  if (!grid) return;
  grid.innerHTML = THEME_PRESETS.map(p => {
    const v = computeThemeVars({preset: p.id});
    const d = v.dark, l = v.light;
    return `<button type="button" class="theme-card" data-theme="${p.id}" onclick="pickThemePreset('${p.id}')">
      <span class="theme-prev">
        <span class="theme-prev-half" style="background:${d['--bg']}">
          <span class="theme-prev-bar" style="background:${d['--surface2']}"></span>
          <span class="theme-prev-dot" style="background:${d['--accent']}"></span>
        </span>
        <span class="theme-prev-half" style="background:${l['--bg']}">
          <span class="theme-prev-bar" style="background:${l['--surface2']}"></span>
          <span class="theme-prev-dot" style="background:${l['--accent']}"></span>
        </span>
      </span>
      <span class="theme-card-name">${p.name}</span>
    </button>`;
  }).join('');
  _initCustomEditor();
  _markActiveThemeCard();
}

function pickThemePreset(id) {
  const mode = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  setThemePref({preset: id, mode});
  _fillCustomInputsFromCurrent();
}

function _fillCustomInputsFromCurrent() {
  const pref = currentThemePref();
  let seeds;
  if (pref && pref.preset === 'custom' && pref.custom) seeds = pref.custom;
  else {
    const p = THEME_PRESETS.find(x => x.id === ((pref && pref.preset) || 'truffe')) || THEME_PRESETS[0];
    seeds = _themeSeedHexes(p);
  }
  [['cust-dark-bg', 'darkBg'], ['cust-dark-ac', 'darkAccent'],
   ['cust-light-bg', 'lightBg'], ['cust-light-ac', 'lightAccent']].forEach(([id, k]) => {
    const el = $(id); if (el && seeds[k]) el.value = seeds[k];
  });
}

function _readCustomInputs() {
  return {
    darkBg: $('cust-dark-bg').value, darkAccent: $('cust-dark-ac').value,
    lightBg: $('cust-light-bg').value, lightAccent: $('cust-light-ac').value,
  };
}

function _initCustomEditor() {
  _fillCustomInputsFromCurrent();
  ['cust-dark-bg', 'cust-dark-ac', 'cust-light-bg', 'cust-light-ac'].forEach(id => {
    const el = $(id);
    if (!el || el._themeWired) return;
    el._themeWired = true;
    const mode = () => document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    // Live preview while dragging the picker; persist (and sync) on release.
    el.addEventListener('input', () => setThemePref({preset: 'custom', mode: mode(), custom: _readCustomInputs()}, {localOnly: true}));
    el.addEventListener('change', () => setThemePref({preset: 'custom', mode: mode(), custom: _readCustomInputs()}));
  });
}

function resetThemePref() {
  setThemePref(null);
  _fillCustomInputsFromCurrent();
}

applyThemeVars();

/* ===== i18n (FR / EN) =====
   EN is the literal text in the HTML; only FR overrides live in the dict.
   On first apply each annotated node's English original is cached so the
   toggle can restore it. Static surfaces (nav, home, legal) are covered;
   JS-built strings use t(). */
// A downloaded export is a file on someone's disk with no server under it.
// The preload script the exporter injects into <head> runs before this file,
// so the flag is already true by the time anything reads it.
const IS_EXPORT = typeof window !== 'undefined' && !!window.__WAYTRACE_PRELOAD__;

let LANG = 'en';
/* Most keys in this file ARE their English text, so t() can return the key
   itself. Keys written as identifiers cannot: they have no English anywhere,
   and t() shipped "adm.scans_per_day" to screen for anyone reading in English.
   This map is the English side of those. */
const I18N_EN = {
  'cfg.default': '(default)',
  // Auth sentences that carry a number, rebuilt from a key.
  'auth.rate_signup':
    "Account creation from your network is temporarily limited to keep the service stable for everyone. Nothing is wrong with your request, please try again in {n}.",
  'auth.rate_login':
    "Too many sign-in attempts from your network. To protect accounts, please wait {n} and try again.",
  'auth.rate_magic':
    "Several sign-in links were requested recently. Check your inbox (and spam folder), or request a new link in {n}.",
  'auth.rate_generic':
    "Too many attempts. Try again in {n}.",
  'auth.locked':
    "Too many failed attempts. Locked for {n}s.",
  'auth.short_password':
    "Password must be at least {n} characters.",
  // Refusals the API returns, keyed by its machine code.
    'api.per_user_limit':
      "You already have a scan in flight. Track it in My scans; a new one can start once it finishes.",
    'api.per_ip_limit':
      "You already have the maximum number of scans in flight from this connection.",
    'api.service_full':
      "Service is full. Try again in a few minutes.",
    'api.archive_paused':
      "Scanning is paused for a moment so archive.org is not overloaded. Try again shortly.",
    'api.unexpected':
      "That request did not go through. Try again in a moment.",
  // Failure causes, keyed by the code the backend records.
    'err.archive_paused':
      "Scanning is paused so this server does not get blocked by archive.org. Try again in a few minutes.",
    'err.cdx_malformed':
      "Archive.org returned a snapshot index for this domain that we could not read.",
    'err.cdx_timeout':
      "Archive.org's snapshot index for this domain did not answer within the time we allow. This is not about the size of the site, that index varies in speed from one hour to the next. Try again a little later.",
    'err.cdx_error':
      "Archive.org refused the snapshot index request. That is temporary on their side, try again in a few minutes.",
    'err.scan_timeout':
      "The scan ran past the time limit and was stopped.",
    'err.unexpected':
      "The scan failed for a reason we have not identified yet. The details are recorded on our side.",
    'legal.contents': 'Contents',
    'mode.dark': 'Dark',
    'mode.light': 'Light',
    'ms.all': 'All',
    'ms.completed': 'Completed',
    'ms.failed': 'Failed',
    'ms.failed.word': 'failed',
    'ms.findings': 'findings',
    'ms.pages': 'pages',
    'ms.shared': 'Shared',
};

const I18N = {
  fr: {
    'nav.history': 'Historique',
    'nav.settings': 'Réglages',
    // --- 404, export drawer, scope intro (added i18n pass) ---
    'notfound.title': 'Page introuvable',
    'notfound.sub': "Cette page n'existe pas ou a été déplacée.",
    'exp.format': 'Format',
    'exp.filters': 'Filtres actifs',
    'exp.nofilters': 'Aucun filtre actif',
    'Markdown': 'Markdown', 'JSON': 'JSON', 'CSV': 'CSV', 'pages': 'pages',
    'Timeline &amp; density': 'Chronologie et densité',
    'scope.intro': "WayTrace ne lit que ce que la <b>Wayback Machine</b> a déjà archivé pour ce domaine. Une sélection plus large veut dire un scan plus long.",
    // --- messages d'échec de scan, rédigés côté serveur (routers/scan.py) ---
    // La clé est la phrase anglaise exacte que renvoie classify_failure. Si
    // elle change là-bas sans être changée ici, t() renvoie l'anglais, ce qui
    // dégrade sans casser.
    "Archive.org's snapshot index for this domain did not answer within the time we allow. This is not about the size of the site, that index varies in speed from one hour to the next. Try again a little later.":
      "L'index des captures d'archive.org pour ce domaine n'a pas répondu dans le temps que nous lui accordons. Ce n'est pas une question de taille du site, la vitesse de cet index varie d'une heure à l'autre. Réessayez un peu plus tard.",
    'Archive.org refused the snapshot index request. That is temporary on their side, try again in a few minutes.':
      "Archive.org a refusé la requête d'index. C'est passager de leur côté, réessayez dans quelques minutes.",
    'Archive.org returned a snapshot index for this domain that we could not read.':
      "Archive.org a renvoyé pour ce domaine un index que nous n'arrivons pas à lire.",
    'Scanning is paused so this server does not get blocked by archive.org. Try again in a few minutes.':
      'Nos requêtes vers archive.org sont en pause pour éviter un blocage de notre serveur. Réessayez dans quelques minutes.',
    'The scan failed for a reason we have not identified yet. The details are recorded on our side.':
      "Le scan a échoué pour une raison que nous n'avons pas encore identifiée. Le détail est enregistré de notre côté.",
    // Le nombre suit settings.scan_timeout_seconds. S'il change côté serveur,
    // cette clé ne correspond plus et la phrase reste en anglais.
    'The scan passed 60 minutes and was stopped.':
      'Le scan a dépassé 60 minutes et a été arrêté.',
    'distinct addresses': 'adresses distinctes',
    'Scan failed': 'Échec du scan',
    'Scan cancelled': 'Scan annulé',
    'Back to homepage': "Retour à l'accueil",
    // --- préflight qui n'a pas pu lire l'index ---
    'We could not read the archive index for this domain.':
      "Nous n'avons pas pu lire l'index d'archive.org pour ce domaine.",
    'A scan started now would send the very same request, and would most likely fail the same way after a couple of minutes.':
      'Un scan lancé maintenant enverrait exactement la même requête, et échouerait très probablement de la même façon au bout de deux minutes.',
    'The speed of that index varies through the day, so the usual fix is simply to try again a little later.':
      "La vitesse de cet index varie au fil de la journée, donc le remède habituel est simplement de réessayer un peu plus tard.",
    'Try reading the index again': "Réessayer de lire l'index",
    'or launch the scan anyway, below': 'ou lancer quand même le scan, ci-dessous',
    // --- toasts / confirms / errors that were hardcoded in JS ---
    'Copy failed': 'Échec de la copie',
    'Copy failed. URL: ': 'Échec de la copie. URL : ',
    'Copied': 'Copié',
    'Press Ctrl/Cmd-C to copy': 'Appuyez sur Ctrl/Cmd-C pour copier',
    'Scan deleted': 'Scan supprimé',
    'Deleted': 'Supprimé',
    'Secret copied': 'Secret copié',
    'URI copied': 'URI copiée',
    'Type a domain first.': "Saisissez d'abord un domaine.",
    'Error: ': 'Erreur : ',
    'Failed: ': 'Échec : ',
    'Network error: ': 'Erreur réseau : ',
    'No screenshot': 'Aucune capture',
    'Cancel this scan?': 'Annuler ce scan ?',
    'Delete this scan permanently?': 'Supprimer définitivement ce scan ?',
    'themes.title': 'Apparence',
    'themes.sub': "Choisissez un thème, ou composez le vôtre. Chaque thème a un visage sombre et un visage clair; le bouton Light/Dark de la barre de navigation bascule entre les deux. Votre choix est enregistré dans ce navigateur.",
    'themes.custom': 'Palette personnalisée',
    'themes.customsub': "Choisissez un fond et un accent pour chaque mode; surfaces, bordures et surlignages sont dérivés automatiquement pour que le résultat reste cohérent.",
    'themes.darkface': 'Visage sombre',
    'themes.lightface': 'Visage clair',
    'themes.bg': 'Fond',
    'themes.bg2': 'Fond',
    'themes.accent': 'Accent',
    'themes.accent2': 'Accent',
    'themes.reset': 'Revenir au thème par défaut',
    'config.title': 'Réglages',
    'config.sub': "Ces valeurs par défaut viennent de nombreux tests, mais tout l'intérêt est que vous pouvez tout régler : chaque paramètre de scan et d'archive.org de cette installation, enregistré localement et appliqué quand vous cliquez sur Enregistrer. La zone orange est agressive; la zone rouge comporte un risque réel qu'archive.org bloque votre IP. Votre machine, vos règles. Si vous trouvez un meilleur équilibre, partagez votre expérience.",
    'config.disabled': "Le panneau de réglages est désactivé sur cette instance.",
    'config.safe': 'Revenir aux valeurs sûres',
    'config.runsetup': 'Relancer la configuration',
    // First-run setup wizard
    'setup.title': 'Configurez votre instance WayTrace',
    'setup.lead': "Quelques choix facultatifs pour faire de cette instance la vôtre. Vous pourrez tout modifier plus tard dans les Réglages.",
    'setup.skip': 'Passer pour l’instant',
    'setup.name.title': 'Nom de l’instance',
    'setup.name.desc': "Affiché dans la barre de navigation et l’onglet du navigateur. Laissez vide pour garder WayTrace tel quel.",
    'setup.name.ph': 'WayTrace',
    'setup.name.preview': 'Aperçu',
    'setup.theme.title': 'Apparence',
    'setup.theme.desc': "Choisissez le thème par défaut que verront les nouveaux visiteurs. Chaque thème a une face claire et une face sombre.",
    'setup.theme.dark': 'Sombre',
    'setup.theme.light': 'Clair',
    'setup.identity.title': 'Identité archive.org',
    'setup.identity.desc': "Chaque requête envoyée à archive.org porte un identifiant stable propre à cette instance, pour que votre trafic ne soit jamais confondu avec celui d'une autre installation. C’est une attribution honnête, pas un déguisement, et il ne change jamais.",
    'setup.identity.id': 'ID d’instance',
    'setup.identity.contact': 'Contact de l’opérateur (facultatif)',
    'setup.identity.contact.ph': 'vous@example.com',
    'setup.identity.contact.hint': "Fortement recommandé. Sans contact, la seule chose qu'archive.org puisse faire de votre trafic est le bloquer ; avec, ils peuvent d'abord vous demander de ralentir. Laissez vide pour utiliser l'URL du projet.",
    'setup.identity.ua': 'User-Agent',
    'setup.cats.title': 'Catégories par défaut',
    'setup.cats.desc': "Les catégories de renseignement que WayTrace extrait par défaut. Toutes sont activées au départ ; vous pouvez aussi les restreindre pour un scan précis.",
    'setup.back': 'Retour',
    'setup.save': 'Enregistrer et démarrer',
    // Category picker (shared)
    'setup.cat.all': 'Tout sélectionner',
    'setup.cat.none': 'Tout désélectionner',
    'setup.grp.sensitive': 'Expositions sensibles',
    'setup.grp.identity': 'Identité et pivots',
    'setup.grp.tech': 'Technique et infrastructure',
    'setup.grp.analytics': 'Analytics et traqueurs',
    'setup.grp.content': 'Contenu et métadonnées',
    'setup.grp.other': 'Autres',
    '{n} of {m}': '{n} sur {m}',
    'generated on first scan': 'généré au premier scan',
    'Could not save your settings.': "Impossible d’enregistrer vos réglages.",
    // Per-scan category control (advanced scan step)
    'scope.cats.title': 'Catégories',
    'all {m} categories': 'les {m} catégories',
    '{n} of {m} categories': '{n} catégories sur {m}',
    // Config panel: instance group title + tunable descriptions
    'Instance & identity': 'Instance et identité',
    'Display name for this instance, shown in the header and page title. Empty uses plain WayTrace.': "Nom affiché de cette instance, dans l’en-tête et le titre de la page. Vide = WayTrace simple.",
    'Optional email or URL sent to archive.org in the User-Agent so they can reach you. Empty uses the project URL.': "E-mail ou URL facultatif envoyé à archive.org dans le User-Agent pour qu’ils puissent vous joindre. Vide = URL du projet.",
    'Theme applied on first visit when the browser has no saved choice. Empty uses the default dark theme.': "Thème appliqué à la première visite quand le navigateur n’a pas de choix enregistré. Vide = thème sombre par défaut.",
    'Extraction categories run by default. Empty runs all of them; a subset makes this instance a focused scanner.': "Catégories d’extraction exécutées par défaut. Vide = toutes ; un sous-ensemble fait de cette instance un scanner ciblé.",
    'Save': 'Enregistrer',
    'Set to unlimited': 'Définir sur illimité',
    'Some changed settings need a restart to take effect.': 'Certains réglages modifiés nécessitent un redémarrage pour prendre effet.',
    'Restart now': 'Redémarrer maintenant',
    'Restarting…': 'Redémarrage…',
    'Restart is taking longer than expected. Reload the page manually.': 'Le redémarrage prend plus de temps que prévu. Rechargez la page manuellement.',
    'Archive.org politeness': 'Politesse archive.org',
    'Snapshot selection': 'Sélection des snapshots',
    'Scans & queue': "Scans et file d'attente",
    'Advanced': 'Avancé',
    'recommended': 'recommandé',
    'reset': 'réinitialiser',
    'restart required': 'redémarrage requis',
    'days': 'jours',
    'bytes': 'octets',
    'Real risk that archive.org blocks your IP.': "Risque réel qu'archive.org bloque votre IP.",
    'Starting request rate of the adaptive governor.': 'Débit de requêtes de départ du gouverneur adaptatif.',
    'Floor the adaptive rate never drops below.': 'Plancher sous lequel le débit adaptatif ne descend jamais.',
    'Ceiling the adaptive rate may probe up to.': 'Plafond que le débit adaptatif peut atteindre.',
    'Additive rate increase after a clean interval.': 'Hausse du débit après un intervalle sans erreur.',
    'Seconds of clean responses before a rate bump.': 'Secondes de réponses saines avant une hausse de débit.',
    'Multiplier applied to the rate on a refusal.': 'Multiplicateur appliqué au débit après un refus.',
    'Token-bucket burst allowance.': 'Tolérance de rafale du seau à jetons.',
    'Simultaneous archive.org connections, all scans combined.': 'Connexions archive.org simultanées, tous scans confondus.',
    'Parallel downloads within a single scan.': "Téléchargements parallèles au sein d'un même scan.",
    'Low bound of the per-request jitter delay.': 'Borne basse du délai aléatoire entre requêtes.',
    'High bound of the per-request jitter delay.': 'Borne haute du délai aléatoire entre requêtes.',
    'Timeout of a single archive.org request.': "Délai maximal d'une requête archive.org.",
    'Retries on a failed CDX index request.': "Nouvelles tentatives après un échec d'index CDX.",
    'Retries on a failed page download.': 'Nouvelles tentatives après un échec de téléchargement.',
    'First pause after archive.org refuses connections.': 'Première pause quand archive.org refuse les connexions.',
    'Ceiling of the escalating refusal pause.': "Plafond de la pause qui s'allonge à chaque refus.",
    'Quiet gap that resets the refusal escalation.': "Accalmie qui remet l'escalade des refus à zéro.",
    'Hard cap on snapshots per scan. 0 removes the cap and scans the domain in full.': 'Plafond dur de snapshots par scan. 0 retire le plafond et scanne le domaine en entier.',
    'Scales the adaptive snapshot cap before depth presets. Above 1.0 scans fetch more pages and take longer.': 'Multiplie le plafond adaptatif de snapshots avant les préréglages de profondeur. Au-delà de 1.0, les scans récupèrent plus de pages et durent plus longtemps.',
    'Scans running at the same time; the rest wait in queue.': 'Scans exécutés en même temps; les autres patientent en file.',
    'Hard cap on running plus waiting scans.': 'Plafond dur des scans en cours et en attente.',
    'Hard wall-clock limit of a single scan.': "Durée maximale d'un scan.",
    'Download-phase budget; past it the scan analyzes what it already has. 0 disables the budget.': "Budget de la phase de téléchargement; au-delà, le scan analyse ce qu'il a déjà. 0 désactive le budget.",
    'How long finished scans are kept and reused. 0 keeps them forever.': 'Durée de conservation et de réutilisation des scans terminés. 0 les conserve indéfiniment.',
    'Pause between expired-scan cleanup passes.': 'Pause entre deux passes de nettoyage des scans expirés.',
    'Serve the interactive Swagger docs at /docs.': 'Servir la documentation interactive Swagger sur /docs.',
    'Verbosity of the server logs.': 'Verbosité des journaux du serveur.',
    'Comma-separated origins allowed to call the API.': "Origines autorisées à appeler l'API, séparées par des virgules.",
    'Largest accepted request body.': "Taille maximale d'un corps de requête accepté.",
    'nav.scan': 'Analyser',
    'Operational': 'Opérationnel',
    'Maintenance': 'Maintenance',
    'Scanning paused': 'Scans en pause',
    'Slower than usual': 'Plus lent que d\'habitude',
    'scan running': 'scan en cours',
    'scans running': 'scans en cours',
    'queued': 'en file',
    'last scan': 'dernier scan',
    'just now': "à l'instant",
    '{n} min ago': 'il y a {n} min',
    '{n}h ago': 'il y a {n} h',
    '{n}d ago': 'il y a {n} j',
    'nav.themes': 'Th\u00e8mes',
    'nav.source': 'Code source',
    'home.foot.public': 'Donn\u00e9es publiques uniquement',
    'home.foot.legal': 'Mentions l\u00e9gales',
    'home.foot.source': 'Code source',
    'home.foot.themes': 'Th\u00e8mes',
    'home.tagline': "Internet <span class=\"dotmark\">n'oublie jamais.</span>",
    'home.sub': "Outil d'OSINT pour chercheurs et professionnels. Révélez ce qu'un domaine a exposé au fil du temps (e-mails, sous-domaines, technos, fuites) depuis les archives de la <a href=\"https://web.archive.org\" target=\"_blank\" rel=\"noopener\">Wayback Machine</a>.",
    'home.scan': 'Analyser',
    'home.adv.summary': 'Pré-filtres (optionnel)',
    'home.adv.exclude': 'Exclure les URL contenant',
    'home.adv.daterange': 'Plage de dates',
    'home.adv.hint': "Les sous-domaines et la densité des snapshots se choisissent à l'étape suivante, une fois archive.org interrogé pour ce domaine.",
    'home.hint': 'Appuyez sur <kbd>Entrée</kbd> pour choisir les sous-domaines, les dates et la densité avant de lancer.',
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
    'Filter by presence': 'Filtrer par présence',
    'All': 'Tout',
    'Still present': 'Encore présent',
    'Disappeared': 'Disparu',
    'none still present': 'rien n\'est encore présent',
    'none disappeared': 'rien n\'a disparu',
    'nothing matches the filter': 'rien ne correspond au filtre',
    'Other findings on the same archived page': 'Autres résultats sur la même page archivée',
    'Seen together on the same archived page': 'Vus ensemble sur la même page archivée',
    'view page': 'voir la page',
    'WayTrace searched all {c} categories across {n} archived pages and found nothing to extract.': 'WayTrace a cherché dans les {c} catégories sur {n} pages archivées et n\'a rien trouvé à extraire.',
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
    'Retry': 'Réessayer',
    'Scan complete': 'Scan terminé',
    'Filter extracted results': 'Filtrer les résultats extraits',
    'Search the archived pages': 'Chercher dans les pages archivées',
    'Copied': 'Copié',
    'home.ethic': "Conçu pour les chercheurs en sécurité, les équipes, les journalistes et les professionnels curieux. Utilisez ce que vous trouvez de façon responsable : signalez les risques aux personnes qui possèdent les données, jamais contre elles.",
    'home.historybtn': 'Historique des scans',
    'home.mrp.all': 'Toutes les dates',
    'mrp.all': 'Tout',
    'mrp.12m': '12 derniers mois',
    'mrp.24m': '24 derniers mois',
    'mrp.ytd': 'Cette année',
    // Legal page
    'scope.terms': "En lan\u00e7ant un scan, vous acceptez les <a href=\"#/legal\">conditions d'utilisation</a>, y compris ce qui est conserv\u00e9 au sujet du scan.",
    'legal.title': 'Mentions légales, licence et usage acceptable',
    'legal.updated': 'Dernière mise à jour 2026-07 · WayTrace',
    'legal.note': "WayTrace est un outil de reconnaissance OSINT. Il lit uniquement ce que l'Internet Archive (Wayback Machine) a <strong>déjà</strong> archivé publiquement. Il n'effectue <strong>aucun scan actif, sondage ou connexion</strong> sur un site cible, n'envoie aucun trafic vers la cible, et n'ajoute rien qui n'était pas déjà public. Cette page est rédigée en langage clair par souci de transparence ; elle ne constitue pas un avis juridique.",
    'legal.accept': "<strong>En accédant à WayTrace ou en l'utilisant, vous acceptez les présentes conditions ainsi que les <a href=\"https://archive.org/about/terms.php\" target=\"_blank\" rel=\"noopener\">conditions d'utilisation de l'Internet Archive</a>, qui régissent les données source.</strong> Si vous n'êtes pas d'accord, n'utilisez pas WayTrace.",
    'legal.h1': '1. Ce que fait WayTrace',
    'legal.p1': "WayTrace reconstruit l'histoire publique d'un domaine à partir des snapshots stockés par <a href=\"https://web.archive.org\" target=\"_blank\" rel=\"noopener\">archive.org</a>. Il récupère un échantillon représentatif de ces pages archivées et en extrait des signaux (technologies, endpoints, liens, identifiants, etc.) avec les dates où ils ont été vus. Toutes les données source étaient déjà publiques et archivées par un tiers avant tout scan. WayTrace est indépendant et non affilié à l'Internet Archive.",
    'legal.h2': '2. Usage autorisé',
    'legal.p2': 'WayTrace est destiné à des usages licites uniquement, notamment :',
    'legal.p2.li1': "l'éducation et l'apprentissage du renseignement en sources ouvertes ;",
    'legal.p2.li2': 'la recherche en sécurité autorisée et la sécurité défensive (vos propres actifs, ou avec autorisation) ;',
    'legal.p2.li3': "le journalisme, l'enquête et la recherche anti-fraude / menace que vous êtes en droit de mener ;",
    'legal.p2.li4': 'la recherche historique et académique sur le web public.',
    'legal.h3': '3. Usage interdit',
    'legal.p3': "Vous ne devez <strong>pas</strong> utiliser WayTrace pour :",
    'legal.p3.li1': 'traquer, harceler, intimider, divulguer (doxxing) ou mettre en danger une personne ;',
    'legal.p3.li2': "tenter un accès non autorisé, un abus d'identifiants, ou contourner une authentification ;",
    'legal.p3.li3': 'violer les lois sur la vie privée, la protection des données ou le piratage dans toute juridiction applicable ;',
    'legal.p3.li4': "mener une surveillance ou un profilage portant atteinte aux droits d'autrui ;",
    'legal.p3.li5': 'toute autre activité illégale ou abusive.',
    'legal.p3b': "Tout accès utilisé pour faciliter ce qui précède peut être bloqué, et les abus peuvent être signalés aux autorités compétentes.",
    'legal.h4': '4. Votre responsabilité',
    'legal.p4': "Vous seul décidez quoi analyser et quoi faire des résultats, et <strong>vous en portez l'entière responsabilité.</strong> Vous devez respecter toutes les lois qui vous sont applicables et applicables au sujet de votre recherche, y compris dans la juridiction du sujet. Lorsque les résultats contiennent des données personnelles, <strong>vous</strong> agissez en tant que responsable du traitement pour tout traitement ultérieur. WayTrace ne fait que révéler des données qu'un tiers avait déjà rendues publiques ; cela ne rend pas leur usage licite entre vos mains.",
    'legal.h5': '5. Données personnelles (RGPD)',
    'legal.p5': "Les pages archivées peuvent contenir des données personnelles (par exemple des adresses e-mail ou des noms). Il n'existe pas d'exemption générale pour les données personnelles publiquement disponibles au titre du RGPD. WayTrace minimise l'exposition par conception : il ne traite que des données déjà archivées publiquement, n'effectue aucun enrichissement au-delà de ces pages, conserve les scans terminés pour une durée limitée sur le service hébergé, et s'appuie sur l'<strong>intérêt légitime</strong> (recherche en sécurité et transparence du web), mis en balance avec les droits des personnes concernées. Les personnes concernées peuvent demander le retrait d'un scan (voir Contact).",
    'legal.h6': '6. Ce que cette instance conserve, et pourquoi',
    'legal.p6': "Deux choses distinctes sont conserv\u00e9es ici, et elles n'ont pas la m\u00eame dur\u00e9e de vie.",
    'legal.p6b': "<strong>Les r\u00e9sultats.</strong> Supprim\u00e9s de cette instance au terme de la dur\u00e9e de conservation indiqu\u00e9e sur la page du scan. Les r\u00e9sultats, et le contenu des pages archiv\u00e9es qui les sous-tend, disparaissent d\u00e9finitivement et le lien du scan cesse de fonctionner.",
    // Ces libelles servent aussi a la version auto-hebergee: ils doivent rester
    // hors des blocs serveur, sinon build_public.py les emporte et l interface
    // locale repasse en anglais.
    'menu.myscans': 'Mes scans',
    'Cancelled': 'Annul\u00e9',
    'No reason was recorded for this one.': "Aucune cause n'a \u00e9t\u00e9 enregistr\u00e9e pour celui-ci.",
    // Why a scan failed. The backend records a short code; the sentence
    // belongs here, with the rest of the user-facing copy.
    'err.archive_paused':
      "Les scans sont en pause pour que ce serveur ne se fasse pas bloquer par archive.org. Réessayez dans quelques minutes.",
    'err.cdx_malformed':
      "Archive.org a renvoyé pour ce domaine un index de snapshots que nous n'avons pas su lire.",
    'err.cdx_timeout':
      "L'index des snapshots d'archive.org n'a pas répondu pour ce domaine dans le délai que nous nous accordons. Cela ne tient pas à la taille du site, la vitesse de cet index varie d'une heure à l'autre. Réessayez un peu plus tard.",
    'err.cdx_error':
      "Archive.org a refusé la requête vers l'index des snapshots. C'est temporaire de leur côté, réessayez dans quelques minutes.",
    'err.scan_timeout':
      "Le scan a dépassé la durée maximale autorisée et a été arrêté.",
    'err.unexpected':
      "Le scan a échoué pour une raison que nous n'avons pas encore identifiée. Les détails sont enregistrés de notre côté.",
    'Try again': 'R\u00e9essayer',
    'archive.org did not answer the index in time': "archive.org n'a pas r\u00e9pondu \u00e0 l'index \u00e0 temps",
    'Postal addresses': 'Adresses postales',
    'Cloud buckets': 'Buckets cloud',
    'kb.hint': 'raccourcis clavier',
    'kb.title': 'Raccourcis clavier',
    'kb.t': "Basculer vers la vue Activit\u00e9",
    'kb.e': "Ouvrir ou fermer le tiroir d'export",
    'kb.slash': 'Placer le curseur dans la recherche',
    'kb.h': "Aller \u00e0 l'historique des scans",
    'kb.n': 'Nouveau scan (accueil)',
    'kb.esc': 'Fermer tout tiroir ou calque ouvert',
    'kb.help': 'Afficher cette aide',
    'kb.foot': "Les raccourcis sont ignor\u00e9s pendant la saisie dans un champ.",
    'Mode': 'Mode',
    'mode.dark': 'Sombre',
    'mode.light': 'Clair',
    'Language': 'Langue',
    'All palettes': 'Toutes les palettes',
    'snapshot': 'snapshot',
    'selected': 's\u00e9lectionn\u00e9s',
    'No filters active (will export all selected categories)':
      "Aucun filtre actif (toutes les cat\u00e9gories coch\u00e9es seront export\u00e9es)",
    'Download': 'T\u00e9l\u00e9charger',
    'Report a bug': 'Signaler un bug',
    // Balisage statique : sans data-i18n, rien ne les voyait.
    'Cancel': 'Annuler',
    'Tell us what went wrong. We capture the current page automatically.':
      "Dites-nous ce qui n'a pas marché. La page en cours est capturée automatiquement.",
    'Attach screenshot':
      "Joindre une capture",
    'Send report':
      "Envoyer",
    'Screenshot file':
      "Fichier de capture",
    'WayTrace · OSINT recon through the Wayback Machine':
      "WayTrace · reconnaissance OSINT via la Wayback Machine",
    'skip.main': 'Aller au contenu principal',
    'cfg.default': '(par défaut)',
    'Offline copy.': 'Copie hors ligne.',
    // Sign-in and account errors: the auth router answers with a sentence,
    // not a code, so these are keyed by the sentence itself.
    'auth.rate_signup':
      "La création de compte depuis votre réseau est temporairement limitée pour garder le service stable pour tout le monde. Votre demande n'a rien d'anormal, réessayez dans {n}.",
    'auth.rate_login':
      "Trop de tentatives de connexion depuis votre réseau. Pour protéger les comptes, patientez {n} avant de réessayer.",
    'auth.rate_magic':
      "Plusieurs liens de connexion ont été demandés récemment. Vérifiez votre boîte de réception, et les indésirables, ou demandez un nouveau lien dans {n}.",
    'auth.rate_generic':
      "Trop de tentatives. Réessayez dans {n}.",
    'auth.locked':
      "Trop d'échecs de connexion. Compte bloqué pendant {n} s.",
    'auth.short_password':
      "Le mot de passe doit faire au moins {n} caractères.",
    'about {n} minutes':
      "environ {n} minutes",
    '{n}s':
      "{n} s",
    'Captcha check failed. Please try again.':
      "La vérification anti-robot a échoué. Réessayez.",
    'Sign in to save a theme.':
      "Connectez-vous pour enregistrer un thème.",
    'Invalid email address.':
      "Adresse e-mail invalide.",
    'This email provider is not allowed. Use a real address.':
      "Ce fournisseur d'e-mail n'est pas accepté. Utilisez une adresse réelle.",
    'Access denied.':
      "Accès refusé.",
    'That email is already registered. Try signing in.':
      "Cette adresse est déjà enregistrée. Essayez de vous connecter.",
    'Wrong email or password.':
      "E-mail ou mot de passe incorrect.",
    'This sign-in link is invalid or expired.':
      "Ce lien de connexion est invalide ou expiré.",
    'This verification link is invalid or expired.':
      "Ce lien de vérification est invalide ou expiré.",
    'Account not found.':
      "Compte introuvable.",
    'Sign in to see your scans.':
      "Connectez-vous pour voir vos scans.",
    '2FA already enabled. Disable it first.':
      "L'authentification à deux facteurs est déjà active. Désactivez-la d'abord.",
    'Start setup first.':
      "Lancez d'abord la configuration.",
    'Invalid code. Check your authenticator.':
      "Code invalide. Vérifiez votre application d'authentification.",
    '2FA is not set up.':
      "L'authentification à deux facteurs n'est pas configurée.",
    'Invalid code.':
      "Code invalide.",
    'Too many sign-in links requested. Please try again in a few minutes.':
      "Trop de liens de connexion demandés. Réessayez dans quelques minutes.",
    'Wrong password for this account.':
      "Mot de passe incorrect pour ce compte.",
    'Could not create your account.':
      "La création du compte a échoué.",
    // Refusals the API returns, keyed by its machine code.
    'api.per_user_limit':
      "Vous avez déjà un scan en cours. Suivez-le dans Mes scans, un nouveau pourra démarrer dès qu'il sera terminé.",
    'api.per_ip_limit':
      "Vous avez déjà le nombre maximum de scans en cours depuis cette connexion.",
    'api.service_full':
      "Le service est saturé. Réessayez dans quelques minutes.",
    'api.archive_paused':
      "Les scans sont en pause un moment pour ne pas surcharger archive.org. Réessayez dans un instant.",
    'api.unexpected':
      "La requête n'a pas abouti. Réessayez dans un instant.",
    'finding': 'résultat',
    'category': 'catégorie',
    'scan of': 'scan du',
    'Downloaded {n} findings': '{n} résultats téléchargés',
    'This scan was taken on {d} and nothing in this file updates.':
      'Ce scan date du {d}, rien dans ce fichier ne se met à jour.',
    'Nothing in this file updates.': 'Rien dans ce fichier ne se met à jour.',
    'you@email.com': 'vous@email.com',
    'What happened? What did you expect?': "Que s'est-il pass\u00e9 ? \u00c0 quoi vous attendiez-vous ?",
    'Scan not found': 'Scan introuvable',
    'The URL is incorrect or the scan has already expired.':
      "L'adresse est incorrecte, ou le scan a déjà expiré.",
    'This scan has expired': 'Ce scan a expiré',
    'A finished scan is kept for a limited time, then deleted with everything it contained. If you downloaded the HTML report, you can still open it.':
      "Un scan terminé est conservé un temps limité, puis supprimé avec tout ce qu'il contenait. Si vous avez téléchargé le rapport HTML, vous pouvez toujours l'ouvrir.",
    'Run a new scan': 'Lancer un nouveau scan',
    'Your share link': 'Votre lien de partage',
    'secret key': 'cl\u00e9 secr\u00e8te',
    'public key': 'cl\u00e9 publique',
    'Sort': 'Trier',
    'kept': 'conserv\u00e9s',
    'ms.failed.word': 'en \u00e9chec',
    'next to expire': 'prochain \u00e0 expirer',
    'notfound.note': "Vous cherchiez un rapport ? Les rapports sont conserv\u00e9s un temps limit\u00e9 puis supprim\u00e9s avec tout ce qu'ils contenaient, un ancien lien cesse donc de fonctionner.",
    'too many for one scan': 'trop pour un seul scan',
    'Fit it for me': 'Ajuster pour moi',
    'expired': 'expir\u00e9',
    'in {n} min': 'dans {n} min',
    'in {n}h': 'dans {n} h',
    'in {n}d': 'dans {n} j',
    'ms.completed': 'Termin\u00e9s',
    'ms.failed': '\u00c9checs',
    'ms.all': 'Tous',
    'Most recent': 'Les plus r\u00e9cents',
    'Most findings': 'Le plus de r\u00e9sultats',
    'No scan matches that filter.': 'Aucun scan ne correspond \u00e0 ce filtre.',
    'Today': "Aujourd'hui",
    'Yesterday': 'Hier',
    'Earlier': 'Avant',
    'today': "aujourd'hui",
    'in 1 day': 'dans 1 jour',
    'in {n} days': 'dans {n} jours',
    'ms.findings': 'r\u00e9sultats',
    'ms.pages': 'pages',
    'no reason recorded': 'aucune cause enregistr\u00e9e',
    'Re-run': 'Relancer',
    'Link copied': 'Lien copi\u00e9',
    'legal.contents': 'Sommaire',
    'legal.print': 'Imprimer',
    'legal.tldr.title': 'En bref',
    'legal.tldr.1': "Il lit <strong>uniquement ce qu'archive.org a d\u00e9j\u00e0 archiv\u00e9</strong>, et ne contacte jamais le domaine analys\u00e9.",
    'legal.tldr.2': "Votre rapport est <strong>priv\u00e9</strong>, sauf si vous rendez son lien lisible par tous.",
    'legal.tldr.3': "Les r\u00e9sultats sont <strong>supprim\u00e9s au terme de la dur\u00e9e de conservation</strong>. La trace du scan, elle, est conserv\u00e9e.",
    'legal.tldr.4': "\u00c0 usage de recherche, de journalisme et de s\u00e9curit\u00e9 autoris\u00e9e. <strong>Pas contre des personnes.</strong>",
    'legal.tldr.5': "Rien de tout cela ne vous convient ? <strong>Faites tourner votre propre copie</strong>, rien ne quitte votre machine.",
    'legal.outro': "Vous pr\u00e9f\u00e9reriez que rien de tout cela ne vous concerne ?",
    'legal.outro.btn': 'Faites tourner votre propre copie',
    'legal.h7': "7. Données source et Internet Archive",
    'legal.p7': "Chaque snapshot provient de l'Internet Archive. WayTrace n'héberge ni ne contrôle ces données, et leur disponibilité, leur exactitude et leur exhaustivité échappent à son contrôle ; les résultats peuvent être partiels ou périmés (lacunes d'archive). Votre usage des données archivées est également régi par les <a href=\"https://archive.org/about/terms.php\" target=\"_blank\" rel=\"noopener\">conditions d'utilisation de l'Internet Archive</a>, que vous acceptez en utilisant WayTrace : n'inondez pas de requêtes et ne cherchez pas à contourner les limites. Pour faire retirer une page de l'archive elle-même, contactez directement l'Internet Archive.",
    'legal.h8': '8. Licence',
    'legal.p8': "WayTrace est open source sous <strong>licence MIT</strong>. Vous pouvez l'auto-héberger ; la version auto-hébergée n'a pas de plafond de snapshots et peut analyser un domaine en intégralité. Le logiciel est fourni <strong>« EN L'ÉTAT », sans aucune garantie</strong>, expresse ou implicite ; voir le fichier LICENSE du dépôt.",
    'legal.h9': '9. Non-responsabilité et limitation de responsabilité',
    'legal.p9': "WayTrace est fourni comme une aide à la recherche, <strong>« en l'état » et « selon disponibilité », sans aucune garantie.</strong> Dans toute la mesure permise par la loi, l'auteur et l'opérateur déclinent toute responsabilité pour tout dommage direct, indirect, accessoire ou consécutif résultant de l'usage, du mésusage ou de la confiance accordée à l'outil ou à ses résultats, ainsi que pour le contenu des pages archivées. <strong>Vous utilisez WayTrace à vos propres risques.</strong>",
    'legal.h10': '10. Contact / abus / retrait',
    'legal.p10': "Signalements d'abus et demandes de retrait : <a href=\"mailto:housset.thomas@pm.me\">housset.thomas@pm.me</a>. Les demandes légitimes sont examinées, et un scan hébergé peut être supprimé sur demande.",
    'legal.back': 'Retour à WayTrace',
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
    'cancelled': 'annulé', 'pending': 'en attente',
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
    // Progress steps, written by the backend and shown verbatim on the one
    // screen people sit and watch.
    'Starting scan...': 'Démarrage du scan…',
    'Fetching snapshots from CDX API...': 'Interrogation de l’index des snapshots d’archive.org…',
    'Selecting diverse snapshots...': 'Sélection de snapshots variés…',
    'Using selected snapshots...': 'Utilisation des snapshots choisis…',
    'Scraping {n} archived pages…': 'Récupération de {n} pages archivées…',
    'No HTML snapshots found': 'Aucun snapshot HTML trouvé',
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
    'That address rejected our previous emails, so we cannot send a sign-in link to it. Write to us and we will sort it out.':
      "Cette adresse a rejeté nos messages précédents, nous ne pouvons plus lui envoyer de lien de connexion. Écrivez nous et on règle ça.",
    'Maintenance in progress. Scanning may be unavailable for a short while.': 'Maintenance en cours. Le scan peut être indisponible quelques instants.',
    'Maintenance in progress. WayTrace will be back shortly.': 'Maintenance en cours. WayTrace revient très vite.',
    'WayTrace is a victim of its own success right now. New scans are queued and start as soon as a slot frees up.': "WayTrace est victime de son succès en ce moment. Les nouveaux scans sont mis en file et démarrent dès qu'une place se libère.",
    'This domain is being scanned right now. Attaching you to the live scan.': 'Ce domaine est déjà en cours de scan. Vous rejoignez le scan en direct.',
    'Already scanned recently. Scans are kept {n} days, so the results open instantly. Use Scan more for a fresh scan.': "Déjà scanné récemment. Les scans sont conservés {n} jours, les résultats s'ouvrent donc instantanément. Utilisez Scan more pour un scan frais.",
    'You already have a scan in flight. Find it in My scans.': 'Vous avez déjà un scan en cours. Retrouvez-le dans Mes scans.',
    'Running': 'En cours',

    'position {n}': 'position {n}',
    'starts in about {eta}': 'démarre dans environ {eta}',
    'Cancel scan': 'Annuler le scan',
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
    'Filter the table to': 'Filtrer la table sur',
    'expires': 'expire',
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
    'Delete scan': 'Supprimer le scan',
    'Delete this scan permanently? This cannot be undone.': 'Supprimer définitivement ce scan ? Cette action est irréversible.',
    'Delete?': 'Supprimer ?', 'Delete': 'Supprimer', 'Keep': 'Conserver', 'took': 'a pris',
    // --- Scope dynamic (density labels/hints) ---
    'Light': 'Léger', 'Fast': 'Rapide', 'Balanced': 'Équilibré', 'Dense': 'Dense', 'Deep': 'Profond', 'Max': 'Max',
    '~2 snapshots/year, quick skim': '~2 snapshots/an, survol rapide',
    '~6/year, fast overview': '~6/an, aperçu rapide',
    '~12/year, recommended': '~12/an, recommandé',
    '~24/year, thorough': '~24/an, approfondi',
    '~50/year, heavy': '~50/an, lourd',
    'every archived capture in scope': 'toutes les captures archiv\u00e9es du p\u00e9rim\u00e8tre',
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
    'No archived pages for this domain': "Aucune page archivée pour ce domaine",
    'The Wayback Machine has no archived HTML pages for this domain, so there is nothing to scan. Check the spelling, try it without a subdomain, or scan a different domain.': "La Wayback Machine n’a aucune page HTML archivée pour ce domaine, il n’y a donc rien à analyser. Vérifiez l’orthographe, essayez sans sous-domaine, ou analysez un autre domaine.",
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
  if (I18N_EN[key] !== undefined) return I18N_EN[key];
  return key;  // most keys are their own English text
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
  // The contents rail copies the heading text, so it has to be rebuilt after a
  // language switch or it keeps the previous language.
  if (document.getElementById('view-legal')?.classList.contains('active')) buildLegalToc();
}

function setLang(l) {
  LANG = (l === 'fr') ? 'fr' : 'en';
  try { localStorage.setItem('wt_lang', LANG); } catch (_) {}
  document.documentElement.lang = LANG;
  applyI18n();
  // The browser tab kept its English title in a French interface. An instance
  // that set its own name owns the title, so only touch the default one.
  try {
    const d = (typeof window.__WT_DEFAULTS__ === 'object' && window.__WT_DEFAULTS__) || null;
    if (!(d && (d.name || '').trim())) {
      document.title = t('WayTrace · OSINT recon through the Wayback Machine');
    }
  } catch (_) {}
  // Re-render the few dynamic strings that JS sets directly.
  try {
    const adv = document.getElementById('scope-adv');
    if (adv && adv.style.display !== 'none' && typeof onScopeDensity === 'function') {
      onScopeDensity();        // density label/hint + estimate
    }
  } catch (_) {}
  // Category pickers carry JS-built labels + count summaries: refresh them so
  // a language switch relabels the categories and the "N of M" counts.
  try {
    const setupView = document.getElementById('view-setup');
    if (setupView && setupView.classList.contains('active') && _setupState.choices) {
      renderCatPicker($('setup-cats'), _setupState.catsSet, _setupState.choices, function () {});
      setupOnContact();
    }
    if (_scopeCatState && $('scope-cats')) {
      renderCatPicker($('scope-cats'), _scopeCatState.set, _scopeCatState.choices, _updateScopeCatSummary);
      _updateScopeCatSummary();
    }
  } catch (_) {}
  try { relocalizeActiveView(); } catch (_) {}
}
/* applyI18n only repaints elements carrying data-i18n, so a view that builds
   its strings in JS keeps whatever language it was first rendered in. The
   list above grew one entry per view somebody happened to notice, and the two
   largest, the report and the history, were never on it: switching to English
   on a report left every category label, every description and every tab in
   French. Re-render whatever is on screen instead of naming widgets one by
   one. The scope form is deliberately not in here, rebuilding it would throw
   away a configuration the person is in the middle of. */
function relocalizeActiveView() {
  const active = document.querySelector('.view.active');
  if (!active) return;
  // A scan lands in view-results when it completed and in view-scan-public
  // while it is queued, running, cancelled or failed. Both are renderPublicScan.
  if (active.id === 'view-scan-public' && !_lastScanPayload) {
    // Nothing behind this view but the "no such scan" copy, which is built in
    // JS like the rest and so kept the language it first rendered in.
    renderPublicScanNotFound();
    return;
  }
  if ((active.id === 'view-results' || active.id === 'view-scan-public')
      && _lastScanPayload) {
    // The running scaffold is deliberately built once and kept alive so the
    // spinner and the bar do not restart on every poll. That also means its
    // phase chips and its Wayback credit keep the language they were built
    // in, so a switch has to drop it and let renderPublicScan rebuild. A
    // one-off stutter on an explicit language change is a fair price.
    const runLive = document.querySelector('#public-scan-body .pub-run-live');
    if (runLive) runLive.remove();
    // Put the reader back exactly where they were: same open category, same
    // filter, same presence tab. A relabel must not also lose their place.
    const keep = Object.assign({}, report2State);
    renderPublicScan(_lastScanPayload);
    if (_r2 && _r2.job) {
      Object.assign(report2State, keep);
      renderReport2(_r2.info, _r2.findings, _r2.job);
    }
    return;
  }
  if (active.id === 'view-history' && typeof renderMyScans === 'function') {
    renderMyScans();
  }
}

function toggleLang() { setLang(LANG === 'fr' ? 'en' : 'fr'); }
function initLang() {
  let l = null;
  try { l = localStorage.getItem('wt_lang'); } catch (_) {}
  if (!l) l = (navigator.language || '').toLowerCase().startsWith('fr') ? 'fr' : 'en';
  setLang(l);
}

/* ===== SELF-HOST CONFIG PANEL ===== */
/* The panel edits live Settings through /api/config. Nothing is applied until
   the user clicks the per-field Save button that appears when a value changes,
   so an edit is never committed by accident. */
let _cfgData = null;
let _cfgFlat = {};            // key -> spec (original server value)
let _cfgRestartPending = false;

function _cfgRiskLevel(spec, val) {
  if (!spec.risk || typeof val !== 'number' || !isFinite(val)) return 'ok';
  const above = spec.risk.direction === 'above';
  if (above ? val > spec.risk.red : val < spec.risk.red) return 'down';
  if (above ? val > spec.risk.orange : val < spec.risk.orange) return 'warn';
  return 'ok';
}

// Human, translated rendering of a value (∞ for the infinite sentinel).
function _cfgFmtValue(spec, val) {
  if (spec.infinite_at != null && val === spec.infinite_at) return '∞';
  return String(val);
}

function _cfgInput(s) {
  const id = 'cfg-' + s.key;
  if (s.type === 'bool') {
    return `<input type="checkbox" id="${id}" ${s.value ? 'checked' : ''}>`;
  }
  if (s.type === 'choice') {
    // An empty choice means "leave it to the built-in default", and it used to
    // render as an option with no text: the control looked unpopulated, which
    // on a settings page reads as broken rather than as a deliberate blank.
    // The values themselves stay exactly as they are typed in .env, because
    // that is the whole point of naming the env key above them.
    return `<select id="${id}" class="config-select">` +
      s.choices.map(c => `<option value="${esc(c)}" ${c === s.value ? 'selected' : ''}>`
        + (c === '' ? esc(t('cfg.default')) : esc(c)) + `</option>`).join('') +
      `</select>`;
  }
  if (s.type === 'str') {
    return `<input type="text" id="${id}" class="config-text" value="${escAttr(String(s.value))}">`;
  }
  if (s.type === 'multichoice') {
    // Populated by renderConfigPage() via renderCatPicker once in the DOM.
    return `<div id="${id}" class="catpick config-multichoice"></div>`;
  }
  const step = s.step != null ? s.step : (s.type === 'float' ? 0.1 : 1);
  return `<input type="number" id="${id}" class="config-num" value="${s.value}"` +
    ` min="${s.min}" max="${s.max}" step="${step}">`;
}

function _cfgRow(s) {
  const risk = _cfgRiskLevel(s, s.value);
  const infBtn = (s.infinite_at != null)
    ? `<button class="btn btn-sm config-inf" type="button" data-key="${s.key}" title="${escAttr(t('Set to unlimited'))}">∞</button>` : '';
  const recVal = s.recommended != null ? _cfgFmtValue(s, s.recommended) : '';
  const rec = recVal && s.type !== 'bool'
    ? `<small class="config-rec">${esc(t('recommended'))} ${esc(recVal)}${s.unit ? ' ' + esc(t(s.unit)) : ''}</small>` : '';
  const warn = `<small class="config-warn" ${risk === 'down' ? '' : 'hidden'}>${esc(t('Real risk that archive.org blocks your IP.'))}</small>`;
  const restartBadge = s.restart ? `<span class="badge config-badge">${esc(t('restart required'))}</span>` : '';
  const resetBtn = `<button class="btn btn-sm config-reset" type="button" data-key="${s.key}" ${s.overridden ? '' : 'hidden'}>${esc(t('reset'))}</button>`;
  const saveBtn = `<button class="btn btn-sm btn-accent config-save" type="button" data-key="${s.key}" hidden>${esc(t('Save'))}</button>`;
  return `
    <div class="config-row${s.type === 'multichoice' ? ' config-row-block' : ''}" data-key="${s.key}">
      <div class="config-info">
        <span class="config-key">${esc(s.key.toUpperCase())}</span>
        <small class="config-desc">${esc(t(s.desc))}</small>
        ${warn}
      </div>
      <div class="config-ctl">
        <span class="config-ctl-line">
          ${s.risk ? `<span class="hs-dot ${risk}"></span>`
                   : `<span class="hs-dot hs-dot-none"></span>`}
          ${_cfgInput(s)}
          <span class="config-unit">${s.unit ? esc(t(s.unit)) : ''}</span>
        </span>
        ${rec}${restartBadge}${infBtn}${saveBtn}${resetBtn}
      </div>
    </div>`;
}

function _cfgReadInput(spec, el) {
  if (spec.type === 'bool') return el.checked;
  if (spec.type === 'int') return parseInt(el.value, 10);
  if (spec.type === 'float') return parseFloat(el.value);
  if (spec.type === 'multichoice') return _cfgReadMulti(spec, el);
  return el.value;
}

// The multichoice control (categories) stores its Set on the container. Empty
// == all == canonical [] so the saved value keeps "all, future-proof" meaning.
function _cfgReadMulti(spec, container) {
  const sel = (container && container._catSelected) || new Set();
  const choices = (container && container._catChoices) || spec.choices || [];
  const list = choices.filter(c => sel.has(c));
  return list.length === choices.length ? [] : list;
}

// Normalise a stored multichoice value to compare against a live read: an empty
// list and a full list are both "all". Returns choices-ordered array.
function _normCats(v, choices) {
  const arr = Array.isArray(v) ? v : [];
  if (!arr.length || arr.length === choices.length) return [];
  return choices.filter(c => arr.includes(c));
}

// The row's editable control, by type (multichoice uses a container, not <input>).
function _cfgControlEl(row, spec) {
  if (spec && spec.type === 'multichoice') return row.querySelector('.config-multichoice');
  return row.querySelector('input, select');
}

// A row is dirty when its input differs from the server value. Toggles the
// per-field Save button and refreshes the live risk dot.
function _cfgRefreshRow(row) {
  const key = row.dataset.key;
  const spec = _cfgFlat[key];
  const el = _cfgControlEl(row, spec);
  const save = row.querySelector('.config-save');
  const val = _cfgReadInput(spec, el);
  let dirty;
  if (spec.type === 'multichoice') {
    dirty = JSON.stringify(val) !== JSON.stringify(_normCats(spec.value, spec.choices || []));
  } else {
    const numBad = (spec.type === 'int' || spec.type === 'float') && !isFinite(val);
    dirty = !numBad && val !== spec.value;
  }
  if (save) save.hidden = !dirty;
  row.classList.toggle('dirty', dirty);
  const dot = row.querySelector('.hs-dot');
  if (dot) dot.className = 'hs-dot ' + _cfgRiskLevel(spec, val);
  const warn = row.querySelector('.config-warn');
  if (warn) warn.hidden = _cfgRiskLevel(spec, val) !== 'down';
}

async function renderConfigPage() {
  const groupsEl = $('config-groups');
  const disabledEl = $('config-disabled');
  if (!groupsEl) return;
  try {
    const r = await fetch(API + '/api/config');
    if (!r.ok) throw new Error();
    _cfgData = await r.json();
  } catch (_) {
    groupsEl.innerHTML = '';
    disabledEl.hidden = false;
    return;
  }
  disabledEl.hidden = true;
  _cfgFlat = {};
  _cfgData.groups.forEach(g => g.settings.forEach(s => { _cfgFlat[s.key] = s; }));
  groupsEl.innerHTML = _cfgData.groups.map(g => `
    <section class="scope-card config-group">
      <div class="scope-card-title">${esc(t(g.title))}</div>
      <div class="config-rows">${g.settings.map(_cfgRow).join('')}</div>
    </section>`).join('');
  groupsEl.querySelectorAll('.config-row').forEach(row => {
    const spec = _cfgFlat[row.dataset.key];
    if (spec && spec.type === 'multichoice') {
      // Grouped checklist; empty stored value means "all", so seed accordingly.
      const cont = _cfgControlEl(row, spec);
      const choices = spec.choices || [];
      const seed = (Array.isArray(spec.value) && spec.value.length) ? spec.value : choices;
      renderCatPicker(cont, new Set(seed), choices, () => _cfgRefreshRow(row));
      return;
    }
    const el = row.querySelector('input, select');
    if (el) el.addEventListener('input', () => _cfgRefreshRow(row));
    if (el) el.addEventListener('change', () => _cfgRefreshRow(row));
  });
  groupsEl.querySelectorAll('.config-save').forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.key;
    const row = btn.closest('.config-row');
    const val = _cfgReadInput(_cfgFlat[key], _cfgControlEl(row, _cfgFlat[key]));
    _cfgSave({ [key]: val });
  }));
  groupsEl.querySelectorAll('.config-inf').forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.key;
    _cfgSave({ [key]: _cfgFlat[key].infinite_at });
  }));
  groupsEl.querySelectorAll('.config-reset').forEach(btn => btn.addEventListener('click', async () => {
    await fetch(API + '/api/config/reset', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ keys: [btn.dataset.key] }),
    });
    renderConfigPage();
  }));
  _cfgRenderRestartBar();
}

async function _cfgSave(values) {
  try {
    const r = await fetch(API + '/api/config', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(values),
    });
    if (r.ok) {
      const d = await r.json();
      if (d.restart_required && d.restart_required.length) _cfgRestartPending = true;
    } else {
      const d = await r.json().catch(() => ({}));
      if (d.detail) showToast(d.detail);
    }
  } catch (_) {}
  renderConfigPage();
}

// Restart affordance: shown only once a saved setting needs a restart. The
// server re-execs itself; we poll health and reload when it is back.
function _cfgRenderRestartBar() {
  const bar = $('config-restart-note');
  if (!bar) return;
  bar.hidden = !_cfgRestartPending;
  bar.innerHTML = _cfgRestartPending
    ? `<span>${esc(t('Some changed settings need a restart to take effect.'))}</span>`
      + `<button class="btn btn-sm btn-accent" type="button" id="config-restart-btn">${esc(t('Restart now'))}</button>`
    : '';
  const btn = $('config-restart-btn');
  if (btn) btn.addEventListener('click', _cfgRestartNow);
}

async function _cfgRestartNow() {
  const bar = $('config-restart-note');
  if (bar) bar.innerHTML = `<span>${esc(t('Restarting…'))}</span>`;
  try {
    await fetch(API + '/api/config/restart', { method: 'POST' });
  } catch (_) {}
  // The server is re-execing; wait for it to answer health again, then reload.
  let tries = 0;
  const poll = async () => {
    tries += 1;
    try {
      const r = await fetch(API + '/api/health', { cache: 'no-store' });
      if (r.ok) { location.reload(); return; }
    } catch (_) {}
    if (tries < 40) setTimeout(poll, 500);
    else if (bar) bar.innerHTML = `<span>${esc(t('Restart is taking longer than expected. Reload the page manually.'))}</span>`;
  };
  setTimeout(poll, 1200);
}

async function configSafeValues() {
  if (!_cfgData) return;
  const safe = {};
  _cfgData.groups.forEach(g => g.settings.forEach(s => {
    if (s.recommended != null && s.value !== s.recommended) safe[s.key] = s.recommended;
  }));
  if (Object.keys(safe).length) await _cfgSave(safe);
}

/* ===== FIRST-RUN SETUP WIZARD (self-host) ===== */
let _setupState = {choices: null, catsSet: null, instanceId: '', theme: ''};

async function renderSetupWizard() {
  let cfg = null;
  try {
    const r = await fetch(API + '/api/config');
    if (r.ok) cfg = await r.json();
  } catch (_) {}
  // Hosted (panel off) never routes here; if a user lands on #/setup anyway and
  // the config endpoint is unavailable, fall back to sensible client defaults.
  const flat = {};
  if (cfg && cfg.groups) cfg.groups.forEach(g => g.settings.forEach(s => { flat[s.key] = s; }));

  const choices = (flat.default_categories && flat.default_categories.choices) || REPORT2_SCOPE.slice();
  const curCats = (flat.default_categories && flat.default_categories.value) || [];
  const selected = new Set(curCats.length ? curCats : choices); // all on by default
  _setupState.choices = choices;
  _setupState.catsSet = selected;
  _setupState.instanceId = (cfg && cfg.instance_id) || '';

  const nameInput = $('setup-name');
  if (nameInput) nameInput.value = (flat.instance_name && flat.instance_name.value) || '';
  const contactInput = $('setup-contact');
  if (contactInput) contactInput.value = (flat.operator_contact && flat.operator_contact.value) || '';

  const curPref = currentThemePref();
  _setupState.theme = (flat.default_theme && flat.default_theme.value)
    || (curPref && curPref.preset) || '';

  const idEl = $('setup-instance-id');
  if (idEl) idEl.textContent = _setupState.instanceId || t('generated on first scan');

  _setupRenderThemes();
  _setupSyncModeButtons();
  renderCatPicker($('setup-cats'), selected, choices, function () {});
  setupOnName();
  setupOnContact();
  applyI18n();
}

function _setupRenderThemes() {
  const grid = $('setup-themes');
  if (!grid) return;
  grid.innerHTML = THEME_PRESETS.map(p => {
    const v = computeThemeVars({preset: p.id});
    const d = v.dark, l = v.light;
    return `<button type="button" class="theme-card" data-theme="${p.id}" onclick="setupPickTheme('${p.id}')">
      <span class="theme-prev">
        <span class="theme-prev-half" style="background:${d['--bg']}"><span class="theme-prev-bar" style="background:${d['--surface2']}"></span><span class="theme-prev-dot" style="background:${d['--accent']}"></span></span>
        <span class="theme-prev-half" style="background:${l['--bg']}"><span class="theme-prev-bar" style="background:${l['--surface2']}"></span><span class="theme-prev-dot" style="background:${l['--accent']}"></span></span>
      </span>
      <span class="theme-card-name">${p.name}</span>
    </button>`;
  }).join('');
  _setupMarkTheme();
}

function _setupMarkTheme() {
  const grid = $('setup-themes');
  if (!grid) return;
  grid.querySelectorAll('.theme-card').forEach(c =>
    c.classList.toggle('active', c.dataset.theme === _setupState.theme));
}

function setupPickTheme(id) {
  _setupState.theme = id;
  const mode = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  _applyPresetTransient({preset: id, mode});   // live preview, not persisted until Save
  _setupMarkTheme();
}

function setupSetMode(mode) {
  if (mode === 'light') document.documentElement.setAttribute('data-theme', 'light');
  else document.documentElement.removeAttribute('data-theme');
  try { localStorage.setItem('wt_theme', mode); } catch (_) {}
  applyThemeLabel();
  if (_setupState.theme) _applyPresetTransient({preset: _setupState.theme, mode});
  else applyThemeVars();
  _setupSyncModeButtons();
}

function _setupSyncModeButtons() {
  const light = document.documentElement.getAttribute('data-theme') === 'light';
  document.querySelectorAll('#view-setup .setup-mode-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === (light ? 'light' : 'dark')));
}

function setupOnName() {
  const el = $('setup-name');
  const v = (el && el.value || '').trim();
  const prev = $('setup-brand-preview');
  if (prev) prev.innerHTML = v ? esc(v) : 'Way<span>Trace</span>';
}

function _buildUAPreview(contact, id) {
  const ver = _instanceVersion();
  const idPart = id || 'xxxxxx';
  const contactPart = contact ? '+' + contact : '+https://github.com/thomashousset/WayTrace';
  return 'WayTrace/' + ver + ' (' + contactPart + '; id:' + idPart + ')';
}

function setupOnContact() {
  const el = $('setup-contact');
  const c = (el && el.value || '').trim();
  const ua = $('setup-ua-preview');
  if (ua) ua.textContent = _buildUAPreview(c, _setupState.instanceId);
}

async function _setupCompleteAndGoHome() {
  try { await fetch(API + '/api/config/complete-setup', {method: 'POST'}); } catch (_) {}
  _setupRedirected = true;
  location.hash = '#/';
}

async function setupSave() {
  const name = ($('setup-name') && $('setup-name').value || '').trim();
  const contact = ($('setup-contact') && $('setup-contact').value || '').trim();
  const choices = _setupState.choices || [];
  const selected = _setupState.catsSet || new Set();
  const cats = choices.filter(c => selected.has(c));
  // All selected = canonical empty ("all categories", future-proof).
  const catsPayload = (cats.length === choices.length) ? [] : cats;
  const payload = {
    instance_name: name,
    operator_contact: contact,
    default_theme: _setupState.theme || '',
    default_categories: catsPayload,
  };
  try {
    const r = await fetch(API + '/api/config', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showToast((d && d.detail) || t('Could not save your settings.'));
      return;
    }
  } catch (_) {
    showToast(t('Could not save your settings.'));
    return;
  }
  // Apply live for this browser: persist the picked theme locally (an explicit
  // choice now) and set the name immediately.
  if (_setupState.theme) {
    const mode = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    setThemePref({preset: _setupState.theme, mode}, {localOnly: true});
  }
  if (name) setInstanceName(name);
  await _setupCompleteAndGoHome();
}

async function setupSkip() {
  await _setupCompleteAndGoHome();
}

/* The contents rail is generated from the headings, never hand-written, so a
   new section or a language switch cannot leave it stale. */
function buildLegalToc() {
  const toc = document.getElementById('legal-toc');
  const text = document.querySelector('#view-legal .legal-text');
  if (!toc || !text) return;
  const heads = [...text.querySelectorAll('h2')];
  heads.forEach((h, i) => { h.id = h.id || 'legal-s' + i; });
  toc.innerHTML = '<div class="legal-toc-head">' + t('legal.contents') + '</div>'
    + heads.map(h => `<a href="#${h.id}" onclick="event.preventDefault();`
      + `document.getElementById('${h.id}').scrollIntoView({behavior:'smooth',block:'start'})">`
      + `${h.textContent.trim()}</a>`).join('');
  _legalTocSpy(heads);
}

let _legalSpy = null;
function _legalTocSpy(heads) {
  if (_legalSpy) _legalSpy.disconnect();
  const links = [...document.querySelectorAll('#legal-toc a')];
  if (!links.length) return;
  _legalSpy = new IntersectionObserver(entries => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const i = heads.indexOf(e.target);
      if (i < 0) continue;
      links.forEach((l, j) => l.classList.toggle('on', j === i));
    }
  }, { rootMargin: '0px 0px -72% 0px', threshold: 0 });
  heads.forEach(h => _legalSpy.observe(h));
}

/* ===== ROUTER ===== */
function navigate(hash) {
  const parts = (hash || '#/').replace('#/', '').split('/').filter(Boolean);
  // v2 public scan route: #/s/{url_id}
  let view = parts[0] === 's' ? 'scan-public' : (parts[0] || 'home');
  const valid = new Set(['home', 'scope', 'history', 'scan-public', 'legal', 'themes', 'config', 'setup']);
  if (!valid.has(view)) view = 'notfound';
  // 'results' stays here (not in `valid`): the public flow reuses view-results,
  // so navigate() must still deactivate it when leaving, even though there is no
  // longer a /#/results route.
  const views = ['home', 'scope', 'results', 'history', 'scan-public', 'legal', 'themes', 'config', 'setup', 'notfound'];

  views.forEach(v => {
    const el = $('view-' + v);
    if (el) el.classList.toggle('active', v === view);
  });

  $('history-btn').classList.toggle('active', view === 'history');
  stopPublicScanPolling();
  // Clear v2 public mode when navigating away from /s/{url_id}.
  if (view !== 'scan-public') v2PublicMode = false;

  if (view === 'home') {
    $('domain-input').focus();
    checkServiceStatus();   // refresh the status strip on every return home
  } else if (view === 'scan-public' && parts[1]) {
    const newUrlId = decodeURIComponent(parts[1]);
    publicScanUrlId = newUrlId;
    publicScanLastStatus = null;
    showScanSkeleton();
    pollPublicScan();
  } else if (view === 'scope' && parts[1]) {
    loadScope(decodeURIComponent(parts[1]));
  } else if (view === 'history') {
    loadHistory();
  } else if (view === 'legal') {
    buildLegalToc();
  } else if (view === 'themes') {
    renderThemesPage();
  } else if (view === 'config') {
    renderConfigPage();
  } else if (view === 'setup') {
    renderSetupWizard();
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
    showToast(t('Type a domain first.'));
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
    showToast(t('Type a domain first.'));
    document.querySelector('.home-search-input')?.focus();
    return;
  }
  // Minimalist homepage: no pre-filters here. Subdomains, pages, exact dates
  // and density are all set on the next (scope) step, which keeps its own
  // defaults.
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
      // Transient backend hiccup (502/503/429 from the proxy under load, etc.).
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
/* The backend writes its progress steps as English prose, and this line used
   to print them verbatim. The frontend rewrites the two phases it recognises
   by shape, the extraction and the N/M scrape, so what leaked through was the
   whole opening stretch of a scan: starting, querying the CDX index, choosing
   snapshots. On a large domain that is minutes of watching an English
   sentence in a French interface. Translating by the string itself means a
   step this build has never seen still reads as the English the server sent,
   never as a key. */
function _stepText(step) {
  if (!step) return '';
  const m = step.match(/^Scraping (\d+) archived pages/);
  if (m) return t('Scraping {n} archived pages…').replace('{n}', m[1]);
  return t(step);
}

/* Every refusal the API returns carries both a machine code and an English
   sentence. The code is the stable thing, so the sentence belongs here where
   it can be translated; the server's own wording stays as the fallback, which
   means a refusal this build has never heard of still reads as prose rather
   than as a key. */
/* The auth router answers with a bare English sentence rather than a code,
   two dozen of them, and that is the first screen anyone new meets. Matching
   on the sentence keeps the server untouched and cannot regress: an unknown
   one falls through t() back to itself, which is exactly today's behaviour.
   The three rate-limit sentences carry a duration the server already
   formatted, so it is lifted out, translated, and put back. */
const _WAIT_PATTERNS = [
  [/^about (\d+) minutes?$/, 'about {n} minutes'],
  [/^(\d+)s$/, '{n}s'],
];

function _waitText(raw) {
  for (const [re, key] of _WAIT_PATTERNS) {
    const m = String(raw || '').match(re);
    if (m) return t(key).replace('{n}', m[1]);
  }
  return raw;
}

const _SENTENCE_PATTERNS = [
  [/^Account creation from your network .* in (.+)\.$/, 'auth.rate_signup'],
  [/^Too many sign-in attempts .* wait (.+) and try again\.$/, 'auth.rate_login'],
  [/^Several sign-in links were requested .* a new link in (.+)\.$/, 'auth.rate_magic'],
  [/^Too many attempts\. Try again in (.+)\.$/, 'auth.rate_generic'],
  [/^Too many failed attempts\. Locked for (\d+)s\.$/, 'auth.locked'],
  [/^Password must be at least (\d+) characters\.$/, 'auth.short_password'],
];

function _sentenceText(raw) {
  for (const [re, key] of _SENTENCE_PATTERNS) {
    const m = raw.match(re);
    if (m) {
      const txt = t(key);
      if (txt === key) return raw;
      const n = /^\d+$/.test(m[1]) ? m[1] : _waitText(m[1]);
      return txt.replace('{n}', n);
    }
  }
  return t(raw);
}

function _apiErrorText(detail, fallback) {
  const code = detail && detail.error;
  if (code) {
    const key = 'api.' + code;
    const txt = t(key);
    if (txt !== key) return txt;
  }
  if (typeof detail === 'string') return _sentenceText(detail);
  return (detail && detail.message) || fallback || t('api.unexpected');
}


/* ===== DIALOGS =====
   Three overlays declare role="dialog" aria-modal="true", which tells
   assistive technology the rest of the page is inert. None of them kept that
   promise: Tab walked straight out into the content the overlay was covering,
   Escape closed only the shortcuts panel although the shortcuts panel itself
   documents "Esc closes any open drawer or overlay", and none handed focus
   back to whatever opened it.

   One implementation, registered by whoever opens a dialog, so a fourth
   overlay added later cannot get it wrong on its own. Visibility stays with
   the existing open/close functions; this owns focus only. */
const _dialogStack = [];

function _focusables(root) {
  return [...root.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]),' +
    ' select:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter(e => e.offsetWidth || e.offsetHeight || e.getClientRects().length);
}

function wtDialogOpened(el, close, firstFocusSel) {
  if (!el || _dialogStack.some(d => d.el === el)) return;
  _dialogStack.push({ el, close, opener: document.activeElement });
  const first = (firstFocusSel && el.querySelector(firstFocusSel)) || _focusables(el)[0];
  if (first) { first.focus(); return; }
  // A dialog with nothing focusable in it, the shortcuts panel is one, still
  // has to hold focus: otherwise Tab walks straight into the page behind it.
  el.tabIndex = -1;
  el.focus();
}

function wtDialogClosed(el) {
  const i = _dialogStack.findIndex(d => d.el === el);
  if (i === -1) return;
  const [d] = _dialogStack.splice(i, 1);
  // Hand focus back, but only if it is still somewhere sensible to put it.
  try {
    if (d.opener && document.contains(d.opener)) d.opener.focus();
  } catch (_) {}
}

document.addEventListener('keydown', (e) => {
  const top = _dialogStack[_dialogStack.length - 1];
  if (!top) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    try { top.close && top.close(); } catch (_) {}
    return;
  }
  if (e.key !== 'Tab') return;
  const items = _focusables(top.el);
  if (!items.length) { e.preventDefault(); top.el.focus(); return; }
  const first = items[0], last = items[items.length - 1];
  const here = document.activeElement;
  if (!top.el.contains(here)) { e.preventDefault(); first.focus(); return; }
  if (e.shiftKey && here === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && here === last) { e.preventDefault(); first.focus(); }
}, true);

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

/* classify_failure writes three or four careful sentences per failure and
   _failure_meta stores only the code beside them, so the page used to render
   the code itself: a scan that timed out told you "cdx_timeout" and nothing
   more. The sentences live in the i18n maps now, keyed by that code, which is
   also what makes them translatable. A code this build has never heard of
   falls back to whatever the server said, then to the generic line, never to
   the token. */
function _failureText(code, step) {
  if (code) {
    const key = 'err.' + code;
    const txt = t(key);
    if (txt !== key) return txt;
  }
  return step || t('err.unexpected');
}

function renderPublicScan(job) {
  _lastScanPayload = job;      // so a language switch can rebuild this view
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
      ${typeof renderPrivacyCard === 'function' ? renderPrivacyCard(job) : ''}
    `;
    if (typeof wireCopyShareLink === 'function') wireCopyShareLink();
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
        stepTxt = _stepText(job.step) || t('Preparing scan…');
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
        ${typeof renderPrivacyCard === 'function' ? renderPrivacyCard(job) : ''}
      `;
      if (typeof wireCopyShareLink === 'function') wireCopyShareLink();
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
    meta.textContent = status === 'cancelled' ? t('Cancelled') : t('Failed');
    const why = (job.meta && job.meta.error) || '';
    body.innerHTML = `
      <div class="pub-error">
        ${_PUB_ERROR_ICON}
        <h2>${status === 'cancelled' ? t('Scan cancelled') : t('Scan failed')}</h2>
        <p>${esc(_failureText(why, job.step))}</p>
        <div class="pub-error-acts">
          <a href="#/scope/${encodeURIComponent(job.domain || '')}" class="btn btn-accent">${t('Try again')}</a>
          <a href="#/" class="btn">${t('Back to homepage')}</a>
        </div>
      </div>
    `;
  }
}

const CAT_LABELS = {
  emails: 'Emails',
  addresses: 'Postal addresses',
  cloud_buckets: 'Cloud buckets',
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
  support_chat: 'Chat & support widgets',
  email_marketing: 'Marketing & CRM',
  payment_processors: 'Payment & commerce',
  mobile_apps: 'Mobile apps',
  cdn_accounts: 'Media & content CDNs',
};

function catLabel(key) {
  return t(CAT_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()));
}

/* ===== SHARED CATEGORY PICKER =====
   A grouped, checkable list of the extraction categories, reused by the setup
   wizard, the per-scan advanced step, and the config panel's multichoice row.
   Groups are thematic; any category the backend reports that is not mapped here
   falls into "Other" so the picker always covers the full choice set. */
const CAT_GROUPS = [
  {key: 'setup.grp.sensitive', en: 'Sensitive exposure',   cats: ['api_keys', 'cloud_buckets', 'connection_strings', 'jwt_tokens', 'internal_ips', 'hidden_fields', 'directory_listings', 'crypto_addresses', 'pgp_keys']},
  {key: 'setup.grp.identity',  en: 'Identity & pivots',     cats: ['emails', 'subdomains', 'persons', 'phones', 'organizations', 'addresses', 'social_profiles', 'github_repos', 'french_business_ids', 'endpoints', 'auth_providers', 'mobile_apps']},
  {key: 'setup.grp.tech',      en: 'Tech & infrastructure', cats: ['technologies', 'hosting', 'http_headers', 'favicons', 'js_urls', 'assets', 'iframe_sources', 'captcha_providers', 'status_pages', 'cdn_accounts', 'payment_processors']},
  {key: 'setup.grp.analytics', en: 'Analytics & tracking',  cats: ['analytics_trackers', 'analytics_ids', 'adsense_ids', 'cookie_consent', 'verification_tags', 'support_chat', 'email_marketing']},
  {key: 'setup.grp.content',   en: 'Content & metadata',    cats: ['meta_info', 'html_titles', 'html_comments', 'outgoing_links', 'linked_documents', 'rss_feeds', 'sitemaps_and_robots', 'bug_bounty_programs', 'job_boards']},
];

// Group the given choices, preserving CAT_GROUPS order and dropping any group
// with no available category. Unmapped categories collapse into "Other".
function _catGroupsFor(choices) {
  const set = new Set(choices);
  const used = new Set();
  const groups = [];
  CAT_GROUPS.forEach(g => {
    const cats = g.cats.filter(c => set.has(c));
    cats.forEach(c => used.add(c));
    if (cats.length) groups.push({key: g.key, en: g.en, cats});
  });
  const other = choices.filter(c => !used.has(c));
  if (other.length) groups.push({key: 'setup.grp.other', en: 'Other', cats: other});
  return groups;
}

// Render the picker into `container`, driving the `selected` Set. `onChange` is
// called after any change so callers can refresh their own summaries.
function renderCatPicker(container, selected, choices, onChange) {
  if (!container) return;
  container._catSelected = selected;
  container._catChoices = choices;
  container._catOnChange = onChange || null;
  const groups = _catGroupsFor(choices);
  const head = `<div class="catpick-bar">
      <span class="catpick-count" data-catcount></span>
      <span class="catpick-tools">
        <button type="button" class="scope-mini-btn" data-catall data-i18n="setup.cat.all">Select all</button>
        <button type="button" class="scope-mini-btn" data-catnone data-i18n="setup.cat.none">Select none</button>
      </span>
    </div>`;
  const body = groups.map(g => {
    const rows = g.cats.map(c => `<label class="catpick-item">
        <input type="checkbox" data-cat="${escAttr(c)}" ${selected.has(c) ? 'checked' : ''}>
        <span>${esc(catLabel(c))}</span>
      </label>`).join('');
    return `<div class="catpick-group">
        <div class="catpick-group-head">
          <label class="catpick-grouptoggle"><input type="checkbox" data-catgroup="${escAttr(g.key)}"><span data-i18n="${escAttr(g.key)}">${esc(g.en)}</span></label>
          <span class="catpick-group-count" data-groupcount="${escAttr(g.key)}"></span>
        </div>
        <div class="catpick-items">${rows}</div>
      </div>`;
  }).join('');
  container.innerHTML = head + `<div class="catpick-groups">${body}</div>`;

  container.querySelectorAll('input[data-cat]').forEach(cb => cb.addEventListener('change', () => {
    if (cb.checked) selected.add(cb.dataset.cat); else selected.delete(cb.dataset.cat);
    _catPickerRefresh(container);
  }));
  const allBtn = container.querySelector('[data-catall]');
  if (allBtn) allBtn.addEventListener('click', () => { choices.forEach(c => selected.add(c)); _catPickerSync(container); });
  const noneBtn = container.querySelector('[data-catnone]');
  if (noneBtn) noneBtn.addEventListener('click', () => { selected.clear(); _catPickerSync(container); });
  container.querySelectorAll('input[data-catgroup]').forEach(cb => cb.addEventListener('change', () => {
    const g = _catGroupsFor(choices).find(x => x.key === cb.dataset.catgroup);
    if (!g) return;
    if (cb.checked) g.cats.forEach(c => selected.add(c)); else g.cats.forEach(c => selected.delete(c));
    _catPickerSync(container);
  }));
  applyI18n();
  _catPickerRefresh(container);
}

// Re-sync every checkbox from the Set (after select-all / none / group toggle).
function _catPickerSync(container) {
  const selected = container._catSelected;
  container.querySelectorAll('input[data-cat]').forEach(cb => { cb.checked = selected.has(cb.dataset.cat); });
  _catPickerRefresh(container);
}

// Refresh the count summary and every group toggle's checked/indeterminate
// state, then notify the caller.
function _catPickerRefresh(container) {
  const selected = container._catSelected;
  const choices = container._catChoices;
  const countEl = container.querySelector('[data-catcount]');
  if (countEl) countEl.textContent = t('{n} of {m}').replace('{n}', selected.size).replace('{m}', choices.length);
  _catGroupsFor(choices).forEach(g => {
    const on = g.cats.filter(c => selected.has(c)).length;
    const gc = container.querySelector('[data-groupcount="' + g.key + '"]');
    if (gc) gc.textContent = on + '/' + g.cats.length;
    const gt = container.querySelector('input[data-catgroup="' + g.key + '"]');
    if (gt) { gt.checked = on === g.cats.length; gt.indeterminate = on > 0 && on < g.cats.length; }
  });
  if (container._catOnChange) container._catOnChange();
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
        flashMsg(t('Copied'));
        btn.textContent = '✓';
        setTimeout(() => { btn.textContent = '⧉'; }, 1200);
      } catch (_) {
        flashMsg(t('Copy failed'));
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
  _lastScanPayload = null;     // this view has no scan behind it
  $('public-scan-domain').textContent = '';
  $('public-scan-meta').textContent = '';
  $('public-scan-actions').style.display = 'none';
  $('public-scan-body').innerHTML = `
    <div class="pub-error">
      ${_PUB_ERROR_ICON}
      <h2>${esc(t('Scan not found'))}</h2>
      <p>${esc(t('The URL is incorrect or the scan has already expired.'))}</p>
      <a href="#/" class="btn btn-accent">${esc(t('Back to homepage'))}</a>
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
      <h2>${esc(t('This scan has expired'))}</h2>
      <p>${esc(t('A finished scan is kept for a limited time, then deleted with everything it contained. If you downloaded the HTML report, you can still open it.'))}</p>
      <a href="#/" class="btn btn-accent">${esc(t('Run a new scan'))}</a>
    </div>
  `;
}

async function cancelPublicScan() {
  if (!publicScanUrlId) return;
  if (!confirm(t('Cancel this scan?'))) return;
  await fetch(API + '/api/s/' + encodeURIComponent(publicScanUrlId), {method: 'DELETE'});
  location.hash = '#/';
}

// Homepage status strip. It replaced the "latest scans" feed: most scans stay
// private, so the feed read as dead even on busy days, while live numbers
// cannot look stale. Data rides the existing /api/service-status poll.
//
// The last metric used to be "N scans this week", with a floor of 52 added here
// so the strip never opened at zero. A number we inflate is not a measurement,
// and a weekly total does not say whether the service just served somebody.
// "Last scan 5 min ago" cannot be flattered and answers the one question a
// visitor actually has in front of an unfamiliar tool: is this still alive.

// Age of the last scan, translated. The API sends a timestamp, not an age, so
// this stays correct however long the server-side response sat in its cache.
function lastScanAge(iso) {
  if (!iso) return '';
  const ts = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
  if (!isFinite(ts)) return '';
  const diff = (Date.now() - ts) / 1000;
  // A clock skew between browser and server must not print "in -3 min".
  // The key IS the English string (see t()): it is returned as-is when the
  // language is English, so a key like 'ago.min' would print literally.
  if (diff < 60) return t('just now');
  if (diff < 3600) return t('{n} min ago').replace('{n}', Math.floor(diff / 60));
  if (diff < 86400) return t('{n}h ago').replace('{n}', Math.floor(diff / 3600));
  return t('{n}d ago').replace('{n}', Math.floor(diff / 86400));
}

function renderHomeStatus(svc, arc) {
  const navSettings = $('nav-settings-btn');
  if (navSettings) navSettings.hidden = !svc.config_panel;
  const line = $('home-status-line');
  if (!line) return;

  // Silent while normal. The dot plus the last-scan age is the whole message;
  // a label saying "Operational" next to a green dot repeats the dot, and the
  // old "0 scans running" advertised emptiness on every quiet hour.
  let dot = 'ok', label = '';
  if (svc.state === 'maintenance') { dot = 'down'; label = t('Maintenance'); }
  else if (arc.state === 'paused') { dot = 'down'; label = t('Scanning paused'); }
  else if (svc.state === 'busy') { dot = 'warn'; label = t('Slower than usual'); }

  // Counts are server ints, but coerce defensively: this string goes to innerHTML.
  const running = Math.max(0, parseInt(svc.active, 10) || 0);
  const queued = Math.max(0, parseInt(svc.waiting, 10) || 0);
  const age = lastScanAge(svc.last_scan_at);
  const sep = '<span class="sep" aria-hidden="true">·</span>';

  const parts = [];
  if (label) parts.push(esc(label));
  if (running > 0) {
    parts.push(`${running} ${esc(running === 1 ? t('scan running') : t('scans running'))}`);
  }
  if (queued > 0) parts.push(`${queued} ${esc(t('queued'))}`);
  // Freshness last, and only when nothing louder is being said and no scan is
  // running: "1 scan running" already proves the service is alive.
  if (!parts.length && age) parts.push(`${esc(t('last scan'))} ${esc(age)}`);

  line.innerHTML =
    `<span class="hs-state ${dot}"><span class="hs-dot ${dot}"></span></span>` +
    (parts.length ? ' ' + parts.join(' ' + sep + ' ') : '');
}

function formatEta(seconds) {
  if (!seconds || seconds < 1) return '<1s';
  if (seconds < 60) return Math.round(seconds) + 's';
  if (seconds < 3600) return Math.round(seconds / 60) + ' min';
  return Math.round(seconds / 3600) + 'h';
}

// Precise local timestamp "YYYY-MM-DD HH:MM:SS" from a stored (naive UTC) ISO.
function fmtScanStamp(iso) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  if (isNaN(d.getTime())) return '';
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
    + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// Wall-clock a scan took, from its start and completion ISO timestamps.
function fmtScanDuration(startIso, endIso) {
  if (!startIso || !endIso) return '';
  const a = new Date(startIso.endsWith('Z') ? startIso : startIso + 'Z').getTime();
  const b = new Date(endIso.endsWith('Z') ? endIso : endIso + 'Z').getTime();
  let s = Math.round((b - a) / 1000);
  if (!isFinite(s) || s < 0) return '';
  if (s < 60) return s + 's';
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
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
  if (diff < 0) return t('expired');
  if (diff < 3600) return t('in {n} min').replace('{n}', Math.floor(diff / 60));
  if (diff < 86400) return t('in {n}h').replace('{n}', Math.floor(diff / 3600));
  return t('in {n}d').replace('{n}', Math.floor(diff / 86400));
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
    document.body.classList.add('wt-export');
    initLang();          // the branch used to return before this, so every
                         // downloaded report opened in English
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
  applyInstanceDefaults();
  navigate(location.hash || '#/');
  checkServiceStatus();
  setInterval(checkServiceStatus, 60000);
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
  // Accept whatever people paste from the address bar: keep only the host.
  const raw = ($('domain-input')?.value || '').trim().toLowerCase()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//, '')
    .split('/')[0].split('?')[0].split('#')[0].split(':')[0]
    .replace(/^www\./, '');
  if (!raw) { showToast(t('Type a domain first.')); return; }
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
  { label: 'Max', perYear: Infinity, hint: 'every archived capture in scope' },
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

/* ===== PER-SCAN CATEGORY PICKER (advanced scan step) =====
   Seeded from the instance default (fetched from /api/config when the config
   panel is on; otherwise all categories). The user can narrow it for a single
   scan; when the selection matches the seed we omit `categories` from the
   payload so the backend applies its instance default. */
let _scopeCatState = null;

async function _initScopeCats() {
  const card = $('scope-card-cats');
  if (!card) return;
  let choices = REPORT2_SCOPE.slice();
  let seed = null;   // instance default (empty/null => all)
  if (_lastSvc && _lastSvc.config_panel) {
    try {
      const r = await fetch(API + '/api/config');
      if (r.ok) {
        const cfg = await r.json();
        const flat = {};
        (cfg.groups || []).forEach(g => g.settings.forEach(s => { flat[s.key] = s; }));
        if (flat.default_categories) {
          choices = flat.default_categories.choices || choices;
          seed = flat.default_categories.value || [];
        }
      }
    } catch (_) {}
  }
  const seedSet = new Set((seed && seed.length) ? seed : choices);
  _scopeCatState = {choices, seedSet, set: new Set(seedSet)};
  const body = $('scope-cats');
  if (body) { body.hidden = true; }
  const caret = $('scope-cats-caret');
  if (caret) caret.textContent = '+';
  const head = $('scope-cats-head');
  if (head) head.setAttribute('aria-expanded', 'false');
  renderCatPicker(body, _scopeCatState.set, choices, _updateScopeCatSummary);
  _updateScopeCatSummary();
}

function _updateScopeCatSummary() {
  const el = $('scope-cats-summary');
  if (!el || !_scopeCatState) return;
  const n = _scopeCatState.set.size, m = _scopeCatState.choices.length;
  el.textContent = (n === m)
    ? t('all {m} categories').replace('{m}', m)
    : t('{n} of {m} categories').replace('{n}', n).replace('{m}', m);
}

function toggleScopeCats() {
  const body = $('scope-cats');
  const caret = $('scope-cats-caret');
  const head = $('scope-cats-head');
  if (!body) return;
  const open = body.hidden;
  body.hidden = !open;
  if (caret) caret.textContent = open ? '-' : '+';
  if (head) head.setAttribute('aria-expanded', String(open));
}

// The categories to send with a scan: null when the selection still matches the
// instance-default seed (backend fills it in), otherwise the explicit list.
function _scopeSelectedCategories() {
  if (!_scopeCatState) return null;
  const {choices, set, seedSet} = _scopeCatState;
  const sel = choices.filter(c => set.has(c));
  if (!sel.length) return null;   // guard: empty selection => use default, not "extract nothing"
  const seed = choices.filter(c => seedSet.has(c));
  const unchanged = sel.length === seed.length && sel.every((c, i) => c === seed[i]);
  return unchanged ? null : sel;
}

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
  $('scope-launch-btn').classList.remove('is-blocked');
  $('scope-sub').textContent = t('Tune the scan before launching it.');
  { const intro = document.querySelector('.scope-intro'); if (intro) intro.style.display = ''; }
  { const fb = $('scope-fallback-actions'); if (fb) { fb.hidden = true; fb.innerHTML = ''; } }
  $('scope-loading').style.display = '';
  $('scope-loading').textContent = t('Querying archive.org for subdomains...');
  { const e = $('scope-empty'); if (e) e.hidden = true; }
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
      const detail = await resp.json().catch(() => ({}))
        .then(d => _apiErrorText(d.detail, resp.statusText));
      showFallbackScopeUI(domain, detail);
      return;
    }
    const data = await resp.json();
    const subs = data.subdomain_groups || [];
    scopeSubdomains = subs;
    _scopePathGroups = data.path_groups || [];

    if (subs.length === 0) {
      showScopeEmpty();
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

    _applyScopePrefill();
    renderScopeSubList();
    renderScopePaths();
    if ($('scope-card-timeline')) $('scope-card-timeline').style.display = '';
    renderScopeChips();
    renderScopeTimeline();
    // Now that the selection is known, open on the strongest density that fits
    // under the cap. Opening on Max meant every large domain started already
    // sampled, which is the one state this page exists to let you avoid.
    _applyBestDensity();
    _initScopeCats();
  } catch (e) {
    showFallbackScopeUI(domain, e.message);
  }
}

// Carry the homepage pre-filters (exclude keywords, date range) into the
// scope step as defaults the user can still change.
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
}

// Preflight succeeded but archive.org has zero HTML captures for this domain:
// there is genuinely nothing to scan. The old code only swapped the spinner's
// text and returned, so the infinite CSS spinner kept turning and the user was
// dead-ended with no way out. Kill the spinner, hide the tuner, and show a clear
// empty state with a one-click way back to a new scan.
function showScopeEmpty() {
  $('scope-loading').style.display = 'none';
  $('scope-adv').style.display = 'none';
  { const intro = document.querySelector('.scope-intro'); if (intro) intro.style.display = 'none'; }
  const sub = $('scope-sub'); if (sub) sub.textContent = '';
  const el = $('scope-empty'); if (el) el.hidden = false;
}

function showFallbackScopeUI(domain, detailMsg) {
  // The preflight could not read the index for this domain.
  //
  // This used to be presented as a soft fallback: hide the pickers, say the
  // scan would "pick its own depth from the live archive", leave the launch
  // button looking like the normal next step. It is not. The preflight sends
  // the SAME CDX query as the scan, with the same 55 s and 35 s deadlines
  // (routers/scan.py, the preflight and scan call sites are identical), so a
  // scan launched right after a failed preflight repeats the request that just
  // failed and, two minutes later, fails too. That is where most of the 23
  // production failures came from, and the person had no way to know.
  //
  // Launching stays possible on purpose: archive.org is erratic, a retry does
  // sometimes go through, and refusing outright would take a real option away.
  // What changes is that it is no longer dressed up as the normal path, and
  // that retrying is offered first.
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
  $('scope-sub').innerHTML =
    '<b>' + t('We could not read the archive index for this domain.') + '</b> '
    + t('A scan started now would send the very same request, and would most likely fail the same way after a couple of minutes.')
    + ' ' + t('The speed of that index varies through the day, so the usual fix is simply to try again a little later.')
    + '<br><span style="color:var(--text-dim)">' + esc(String(detailMsg || fbReason)) + '</span>';

  // Retry first, launch anyway second. The retry replays the preflight, which
  // is one request, against a scan that costs thousands and holds the queue.
  const actions = $('scope-fallback-actions');
  if (actions) {
    actions.innerHTML =
      '<button type="button" class="btn btn-accent" id="scope-retry-preflight">'
      + esc(t('Try reading the index again')) + '</button>'
      + '<span class="scope-fb-hint">' + esc(t('or launch the scan anyway, below')) + '</span>';
    actions.hidden = false;
    const retry = $('scope-retry-preflight');
    if (retry) retry.addEventListener('click', () => { actions.hidden = true; loadScope(domain); });
  }
  _applyScopePrefill();
  renderScopeChips();
  _initScopeCats();
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
        <div class="scope-item-count">${nfmt(s.snapshot_count)} ${
          s.snapshot_count === 1 ? t('snapshot') : t('snapshots')}</div>
        <div class="scope-item-range">${s.first || '?'} - ${s.last || '?'}</div>
      </label>`;
  }).join('');
  const meta = $('scope-sub-meta');
  if (meta) meta.textContent = `${scopeCheckedSubs.size}/${scopeSubdomains.length} ${t('selected')}`;
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
    return `<label class="scope-path${off ? ' excluded' : ''}" title="${escAttr(pg.path)} · ${pg.count} snapshots">
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

/* What the current density asks for, before the cap re-balances it. The
   selection algorithm itself is unchanged and stays under its own tests; this
   only lets the page tell the difference between "this fits" and "this was
   quietly cut down to fit". */
function _scopeRawCount(idx) {
  const dens = SCOPE_DENSITY[idx == null ? scopeDensityIdx : idx];
  if (!dens) return 0;
  // Max asks for every snapshot in scope. It used to mean "as many as the cap
  // allows, sampled", which is the one outcome this page should never produce
  // without saying so, so it is counted honestly and refused like any other.
  if (!isFinite(dens.perYear)) return _scopeInScope().length;
  const byYear = {};
  for (const s of _scopeInScope()) (byYear[s.year] = byYear[s.year] || []).push(s);
  let n = 0;
  for (const y of Object.keys(byYear)) {
    n += Math.min(byYear[y].length, dens.perYear);
  }
  return n;
}

/* The strongest density whose own promise fits under the cap. Starting on Max
   meant every large domain opened already sampled, which is the one state the
   page exists to let you avoid. */
function _bestFittingDensity() {
  for (let i = SCOPE_DENSITY.length - 1; i >= 0; i--) {
    if (_scopeRawCount(i) <= SCOPE_CAP) return i;
  }
  return 0;
}

function _applyBestDensity() {
  scopeDensityIdx = _bestFittingDensity();
  const el = $('scope-density');
  if (el) { el.value = String(scopeDensityIdx); onScopeDensity(); }
  else updateScopeEstimate();
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
  // Three states, not two. A finite density is a promise ("~24 a year"); if it
  // cannot be kept the page says so and refuses, instead of quietly cutting the
  // selection down and calling it a scan. Max is the one level that asks to be
  // sampled to the cap, so it is never refused.
  const raw = _scopeRawCount();
  const over = raw > SCOPE_CAP;
  const lvl = over ? 'lvl-capped' : (n < 25 ? 'lvl-thin' : 'lvl-good');
  const lvlLabel = over
    ? t('too many for one scan')
    : (n < 25 ? t('thin coverage, raise density or range') : t('good coverage'));
  const word = t('snapshots'), estW = t('est.'), densW = t('density');
  el.style.display = '';
  const shown = over ? raw : n;
  const densPhrase = LANG === 'fr' ? `${densW} ${dens}` : `${dens} ${densW}`;
  const cap1 = (x) => x.charAt(0).toUpperCase() + x.slice(1);
  el.innerHTML = `<span class="scope-est-line"><span class="scope-est-dot ${lvl}"></span><strong class="scope-est-n ${lvl}">${shown.toLocaleString()}</strong> ${word}`
    + (over ? '' : ` <span class="scope-est-sep">·</span> ${estW} <strong class="scope-est-t">${etaTxt}</strong>`)
    + `</span>`
    + `<span class="scope-est-sub">${cap1(densPhrase)}, ${rangeTxt}. ${cap1(lvlLabel)}.`
    + (over ? '' : ' ' + t('More density = more snapshots = longer scan.')) + `</span>`;

  // The launch button is the honest place to refuse: greyed out, with the two
  // levers named and one of them a single click away.
  const btn = $('scope-launch-btn');
  if (btn) {
    btn.disabled = over;
    btn.classList.toggle('is-blocked', over);
  }
  const note = $('scope-cap-note');
  if (note) {
    note.hidden = false;
    note.classList.toggle('is-capped', over);
    const repo = '<a href="https://github.com/thomashousset/WayTrace" target="_blank" rel="noopener">github.com/thomashousset/WayTrace</a>';
    const best = _bestFittingDensity();
    const fitBtn = `<button type="button" class="scope-fit-btn" onclick="_applyBestDensity()">`
      + `${esc(t('Fit it for me'))}</button>`;
    if (over) {
      note.innerHTML = (LANG === 'fr'
        ? `Cette sélection demande <strong>${raw.toLocaleString()}</strong> snapshots, au-dessus des <strong>${SCOPE_CAP.toLocaleString()}</strong> qu'un scan hébergé traite. Baissez la densité, resserrez la plage de dates, ou décochez des sous-domaines. La densité <strong>${esc(t(SCOPE_DENSITY[best].label))}</strong> passe. Pour analyser le domaine entier sans limite, lancez WayTrace en local : ${repo}.`
        : `This selection asks for <strong>${raw.toLocaleString()}</strong> snapshots, above the <strong>${SCOPE_CAP.toLocaleString()}</strong> a hosted scan handles. Lower the density, narrow the date range, or untick subdomains. <strong>${esc(t(SCOPE_DENSITY[best].label))}</strong> density fits. To scan the whole domain with no ceiling, run WayTrace locally: ${repo}.`)
        + ' ' + fitBtn;
    } else if (raw > SCOPE_CAP * 0.7) {
      note.innerHTML = LANG === 'fr'
        ? `Un scan hébergé traite jusqu'à ${SCOPE_CAP.toLocaleString()} snapshots. Pour de plus gros travaux, lancez WayTrace en local : ${repo}.`
        : `A hosted scan handles up to ${SCOPE_CAP.toLocaleString()} snapshots. For bigger jobs you can run WayTrace locally: ${repo}.`;
    } else {
      note.hidden = true;
      note.innerHTML = '';
    }
  }
}

async function launchScopedScan() {
  // Belt and braces: the button is disabled while the selection overflows, but
  // a stale click or a keyboard activation must not slip a sampled scan through.
  if (!scopeFallback && _scopeRawCount() > SCOPE_CAP) {
    showToast(t('too many for one scan'));
    updateScopeEstimate();
    return;
  }
  $('scope-launch-btn').disabled = true;
  resetSessionState();
  try {
    // Assemble the explicit snapshot list from the picker. In fallback mode
    // (no preflight data) it is empty and the backend crawls on its own.
    const selected = scopeFallback ? [] : _scopeAssembleSelected();
    const body = { domain: scopeDomain };
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

    // Per-scan category override (null when it still matches the instance
    // default). Categories live on the ScanConfig, and JobCreate forbids extra
    // top-level keys, so it MUST go under body.config, not at the top level.
    const cats = _scopeSelectedCategories();
    if (cats) { body.config = body.config || {}; body.config.categories = cats; }

    const resp = await fetch(API + '/api/scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (resp.status === 429) {
      const d = await resp.json().catch(() => ({}));
      if (d.detail?.error === 'per_user_limit') {
        showToast(t('You already have a scan in flight. Find it in My scans.'));
        location.hash = '#/history';
        return;
      }
      throw new Error(_apiErrorText(d.detail));
    }
    if (resp.status === 503) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(_apiErrorText(d.detail));
    }
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(_apiErrorText(err.detail, t('Scan failed')));
    }
    const data = await resp.json();
    // Guardrail: the server returned an existing scan for this domain instead
    // of re-scanning it. Tell the user why they landed on results.
    if (data.reused && data.live) {
      showToast(t('This domain is being scanned right now. Attaching you to the live scan.'));
    } else if (data.reused) {
      showToast(t('Already scanned recently. Scans are kept {n} days, so the results open instantly. Use Scan more for a fresh scan.')
        .replace('{n}', data.retention_days || 14));
    }
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
  findingsPage = 0;
}

/* ===== RESULTS ===== */

// Re-poll handle for the "analysis pending" state. When the user lands on
// #/results/{id} between collection-done and analyze-done, the findings
// endpoint returns []. We re-fetch every 3s and re-render once findings
// appear, so the user never sees a permanently-empty results page.



function renderResultsHeader(info) {
  $('res-domain').textContent = info.name;
  const el = $('res-meta');
  const m = info.scanMeta;
  const n = (v) => Number(v || 0).toLocaleString(LANG === 'fr' ? 'fr-FR' : 'en-US');
  if (m && (m.snapshots_analyzed || m.pages_scraped || info.total_findings)) {
    const found = m.total_snapshots_found, ana = m.snapshots_analyzed,
          failed = m.pages_failed || 0, dedup = m.pages_deduped || 0,
          fnd = info.total_findings || 0;
    // A written 0 is a fact, the no-HTML-snapshots path records exactly that.
    // A missing key is not zero, and Number(v || 0) turned one into the other:
    // a scan whose meta carried no page count announced "0 pages were
    // retrieved and analysed" under a headline saying thousands of snapshots
    // were analysed, with the findings from those pages listed below it.
    const scr = (m.pages_scraped != null) ? m.pages_scraped
              : (ana != null ? ana : null);
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
        + (scr == null ? 'les pages ont été récupérées et analysées'
                       : `<b>${n(scr)}</b> pages ont été récupérées et analysées`)
        + (gaps ? `, <b>${n(gaps)}</b> n'étaient plus disponibles côté archive (lacunes d'archive)` : '')
        + (dedup ? `, <b>${n(dedup)}</b> doublons ignorés` : '')
        + (blocked ? `, <b>${n(blocked)}</b> pages non récupérées (archive.org limitait le débit)` : '')
        + `${range ? `, couvrant ${esc(range)}` : ''}.`
      : `Of ${found ? `<b>${n(found)}</b> archived snapshots` : 'the archived snapshots'}, `
        + (scr == null ? 'the pages were retrieved and analysed'
                       : `<b>${n(scr)}</b> pages were retrieved and analysed`)
        + (gaps ? `, <b>${n(gaps)}</b> were no longer available from the archive (archive gaps)` : '')
        + (dedup ? `, <b>${n(dedup)}</b> duplicates skipped` : '')
        + (blocked ? `, <b>${n(blocked)}</b> pages archive.org rate-limited this run` : '')
        + `${range ? `, spanning ${esc(range)}` : ''}.`;
    /* Coverage. "snapshots analysed" was the SELECTED count, so a run that hit
       the download budget read as complete while covering a fraction of its own
       selection. Show the ratio instead, and say so in one calm line when the
       selection was not exhausted. A partial scan is a normal outcome on a big
       domain, not a failure, so it gets a statistic and not a warning. */
    const attempted = (typeof m.pages_attempted === 'number') ? m.pages_attempted : ana;
    const partial = !!m.truncated && ana > 0 && attempted < ana;
    const pct = (ana > 0) ? Math.round((attempted / ana) * 100) : 100;
    /* How WIDE the report is, next to how deep. On mail.ru the cap came out at
       100 because only 8 distinct addresses were visible in the index window,
       so "100 of 100 processed" would have read as full coverage of a domain
       barely seen. When the sample is that narrow a percentage is a tautology,
       so the breadth sentence replaces it instead of sitting next to it. */
    const paths = (typeof m.unique_paths === 'number') ? m.unique_paths : null;
    const narrow = !!m.narrow_sample && paths !== null;
    const partialFr = `Le budget de téléchargement a été atteint avant la fin de la sélection, `
      + `<b>${n(attempted)}</b> des <b>${n(ana)}</b> captures retenues ont été traitées. `
      + `Relancez avec « Scanner plus » pour poursuivre.`;
    const partialEn = `The download budget ran out before the end of the selection, `
      + `<b>${n(attempted)}</b> of the <b>${n(ana)}</b> selected captures were processed. `
      + `Use "Scan more" to continue.`;
    let coverage = '';
    if (narrow) {
      coverage = (LANG === 'fr'
        ? `Ce scan couvre <b>${n(paths)}</b> adresse${paths > 1 ? 's' : ''} distincte${paths > 1 ? 's' : ''}. `
          + `L'index d'archive.org pour ce domaine est bien plus large que la fenêtre que nous avons pu en lire, `
          + `donc ce rapport est un échantillon étroit et non une vue du domaine.`
        : `This scan covers <b>${n(paths)}</b> distinct address${paths > 1 ? 'es' : ''}. `
          + `Archive.org's index for this domain is far wider than the window we could read of it, `
          + `so this report is a narrow sample and not a view of the domain.`)
        + (partial ? ' ' + (LANG === 'fr' ? partialFr : partialEn) : '');
    } else if (partial) {
      coverage = (LANG === 'fr'
        ? `Couverture partielle : <b>${n(attempted)}</b> des <b>${n(ana)}</b> captures retenues ont été traitées (${pct} %), `
          + `le budget de téléchargement a été atteint avant la fin de la sélection. `
          + `Relancez avec « Scanner plus » pour poursuivre sur les captures restantes.`
        : `Partial coverage: <b>${n(attempted)}</b> of the <b>${n(ana)}</b> selected captures were processed (${pct}%), `
          + `the download budget ran out before the end of the selection. `
          + `Use "Scan more" to continue on the remaining captures.`);
    }
    el.innerHTML =
      `<div class="rm-line">`
      + `<span class="rm-stat"><span class="rm-num">${n(attempted)}${partial ? ` / ${n(ana)}` : ''}</span> ${t('snapshots analysed')}</span>`
      + (paths !== null
          ? `<span class="rm-stat"><span class="rm-num">${n(paths)}</span> ${t('distinct addresses')}</span>` : '')
      + (scr && scr !== attempted
          ? `<span class="rm-stat"><span class="rm-num">${n(scr)}</span> ${t('pages scraped')}</span>` : '')
      + (range ? `<span class="rm-range">${esc(range)}</span>` : '')
      + `</div>`
      + `<div class="rm-explain">${explain}</div>`
      + (coverage ? `<div class="rm-explain">${coverage}</div>` : '');
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
  support_chat: 'Live-chat / helpdesk widgets and their tenant id (Intercom, Crisp, Tawk.to, Drift, Zendesk...). The id clusters sites run by one operator.',
  email_marketing: 'Marketing-automation / CRM embeds and their account id (Mailchimp, HubSpot, Marketo, Klaviyo, Pardot...). Pivot on the shared account.',
  payment_processors: 'Checkout stacks and merchant ids (Shopify store, PayPal button, Paddle vendor, Gumroad, Snipcart...). Same id elsewhere means the same seller.',
  mobile_apps: 'Linked iOS / Android apps from app-banner and App Links meta tags and store links. Pivot to the app listing (developer, reviews, versions).',
  cdn_accounts: 'Media/content/search SaaS account ids in asset URLs (Cloudinary cloud, Contentful space, Sanity project, Algolia app, imgix...). Account-scoped operator pivot.',
};

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
  _r2RawHighlights = results.highlights || [];
  /* A highlight that lists `values` covers exactly those; one that lists none
     covers its whole category. The old code applied the FIRST highlight's
     severity to every finding in the category, so a public-by-design Google
     key inherited LEAK from the Stripe secret key beside it. Where two
     highlights disagree about a category and neither narrows itself, nothing
     is asserted: a wrong severity is worse than none. */
  const sevByCatValue = new Map();
  const blanket = new Map();        // category -> severity, or null if disputed
  for (const h of (results.highlights || [])) {
    if (!h || !h.category) continue;
    const sev = h.severity || null;
    const cat = h.category.replace(/_public$/, '');
    if (Array.isArray(h.values) && h.values.length) {
      for (const v of h.values) sevByCatValue.set(cat + '::' + v, sev);
      continue;                     // narrowed, so it claims nothing wider
    }
    if (h.value) { sevByCatValue.set(cat + '::' + h.value, sev); continue; }
    if (blanket.has(cat) && blanket.get(cat) !== sev) blanket.set(cat, null);
    else if (!blanket.has(cat)) blanket.set(cat, sev);
  }
  const sevByCategory = blanket;
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
   Default "Categories" view: a rail of every category (found first, empty
   collapsed), one open at a time, its findings + its own activity together.
   "Activity" view: checkable categories + pivots compose a shared-axis timeline,
   plus the favicon evolution gallery and a dated change feed. Neutral: provenance
   (first/last-seen, occurrences, archived source) is the evidence, no severity.
   ========================================================================== */

const REPORT2_SCOPE = Object.keys(CAT_DESCRIPTIONS); // the canonical category list
let _r2RawHighlights = [];   // kept so the rail can order by consequence

let report2State = {
  view: 'cats',        // 'cats' | 'activity'
  openCat: null,       // category key, or '__all__' for the flat dump
  filter: '',          // in-category / global value filter
  showEmpty: false,    // rail: reveal the empty categories
  checkedCats: null,   // Activity view: Set of category keys shown as lanes
  checkedPivots: null, // Activity view: Set of "cat::value" pivots shown as lanes
  pivotFilter: '',     // Activity view: search box over the pivot list
  presence: 'all',     // findings filter: 'all' | 'live' (still present) | 'gone'
  expandedPage: null,  // source_page_id whose co-occurrence panel is open, or null
};
let _r2 = { findings: [], info: null, byCat: new Map(), found: [], empty: [], job: null, lo: 0, hi: 0 };

function _r2Chip(f) {
  const m = f.metadata;
  if (!m || typeof m !== 'object') return '';
  // Nature, never verdict: "secret key" is what the extractor determined and
  // anyone can check it against the prefix. "Leak" would be an inference about
  // what the owner intended, which is the reader's call, not the tool's.
  if (m.tier === 'secret' || m.tier === 'public') {
    const lab = m.tier === 'secret' ? t('secret key') : t('public key');
    return `<span class="r2-chip r2-chip-${m.tier}">${esc(lab)}</span>`
      + (m.type ? `<span class="r2-chip">${esc(String(m.type).slice(0, 22))}</span>` : '');
  }
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
  // Stable global bound for "still present" logic. _r2.hi gets mutated per-render
  // by _r2SetBoundsFrom (activity timelines use local bounds), so presence/live
  // colouring must read this immutable one instead of _r2.hi.
  _r2.globalHi = hi;
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
    <span class="r2-lbl" title="${escAttr(label)}">${esc(label)} ${tag}</span>
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
      ? ` <a href="${escAttr(e.f.metadata.source_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${t('last capture')} ↗</a>` : '';
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
  // Which categories a highlight put in each tier. Read from the highlights
  // list, not from the per-finding severity, because that one is inherited in
  // bulk per category and gets individual values wrong.
  const tierOf = new Map();
  const RANK = { LEAK: 0, PIVOT: 1, CONTEXT: 2 };
  for (const h of ((info.scanResults && info.scanResults.highlights) || _r2RawHighlights || [])) {
    if (!h || !h.category) continue;
    const r = RANK[h.severity];
    if (r == null) continue;
    const cat = h.category.replace(/_public$/, '');
    if (!tierOf.has(cat) || r < tierOf.get(cat)) tierOf.set(cat, r);
  }
  const rank = (c) => (tierOf.has(c) ? tierOf.get(c) : 3);
  const found = [...byCat.keys()].filter(c => byCat.get(c).length)
    .sort((a, b) => rank(a) - rank(b) || byCat.get(b).length - byCat.get(a).length);
  const empty = REPORT2_SCOPE.filter(c => !byCat.has(c) || !byCat.get(c).length);
  // Co-occurrence: group findings by the archived page that introduced them, so a
  // row can reveal what else was seen on the same page (source_page_id).
  const byPage = new Map();
  for (const f of findings) {
    const pid = f.metadata && f.metadata.source_page_id;
    if (pid == null) continue;
    if (!byPage.has(pid)) byPage.set(pid, []);
    byPage.get(pid).push(f);
  }
  _r2 = { findings, info, byCat, found, empty, job, lo: 0, hi: 0, byPage };
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
    el.innerHTML = `<img class="res-fav" src="${escAttr(src)}" alt="" onerror="this.remove()">${esc(domain || '')}`;
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
// Make a div-based control keyboard-operable: Enter/Space fire its click.
function report2KeyActivate(ev) {
  if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ev.currentTarget.click(); }
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

// "Still present" = the value's last_seen reaches the archive's latest month; a
// finding that stopped earlier has "disappeared".
function _r2IsLive(f) { const b = _r2Month(f.last_seen); return b != null && b >= _r2.globalHi; }
function _r2ApplyPresence(list) {
  if (report2State.presence === 'live') return list.filter(_r2IsLive);
  if (report2State.presence === 'gone') return list.filter(f => !_r2IsLive(f));
  return list;
}

// Compact summary strip + the presence filter (All / Still present / Disappeared).
function report2RenderSummary() {
  const el = document.getElementById('r2-summary');
  if (!el) return;
  const m = (_r2.info && _r2.info.scanMeta) || {};
  const nf = _r2.findings.length;
  const nc = _r2.found.length;
  const live = _r2.findings.filter(_r2IsLive).length;
  const gone = nf - live;
  const stat = (n, label) => `<span class="r2-sum-stat"><b>${n}</b> ${esc(label)}</span>`;
  const p = report2State.presence;
  const seg = (key, label, count) =>
    `<button class="wt-tab${p === key ? ' on' : ''}" aria-pressed="${p === key}"`
    + ` ${count ? '' : 'disabled'} onclick="report2SetPresence('${key}')">${esc(label)}`
    + `<span class="wt-tabn">${count}</span></button>`;
  const nfmt2 = (v) => Number(v || 0).toLocaleString(LANG === 'fr' ? 'fr-FR' : 'en-US');
  el.innerHTML =
    `<div class="r2-sum-stats">`
    + stat(nfmt2(nf), t('findings'))
    + stat(nc + '/' + REPORT2_SCOPE.length, t('categories'))
    + `</div>`
    + `<div class="wt-tabs" role="group" aria-label="${esc(t('Filter by presence'))}">`
    + seg('all', t('All'), nf)
    + seg('live', t('Still present'), live)
    + seg('gone', t('Disappeared'), gone)
    + `</div>`;
}
function report2SetPresence(v) { report2State.presence = v; report2Render(); }

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
  report2RenderSummary();
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
            placeholder="${esc(t('Search pivots…'))}" value="${escAttr(report2State.pivotFilter || '')}"
            oninput="report2PivotFilter(this.value)"></div>`
         + (pivots.length
            ? pivots.map(p => {
                const on = report2State.checkedPivots.has(p.key);
                // The key contains archive-derived text; pass it via a data-
                // attribute (escAttr) and read it in the handler, never inside the
                // onclick JS string, so a value with a quote can't inject a handler.
                return `<div role="button" tabindex="0" onkeydown="report2KeyActivate(event)" class="r2-chk pv${on ? ' on' : ''}" data-pivot-key="${escAttr(p.key)}" onclick="report2TogglePivot(this.dataset.pivotKey)">
                  <span class="r2-box">${on ? '✓' : ''}</span><span class="r2-pv" title="${escAttr(p.label)}">${esc(String(p.label))}</span></div>`;
              }).join('')
            : `<div class="r2-pivnote">${t('No pivot matches.')}</div>`))
      : `<div class="r2-pivnote">${t('Tick a category above to pick pivots from its values.')}</div>`;
    rail.innerHTML =
      `<div class="r2-rt"><span>${t('Categories')}</span><span>${t('tick')}</span></div>` +
      _r2.found.map(c => {
        const on = report2State.checkedCats.has(c);
        return `<div role="button" tabindex="0" onkeydown="report2KeyActivate(event)" class="r2-chk${on ? ' on' : ''}" onclick="report2ToggleCat('${c}')">
          <span class="r2-box">${on ? '✓' : ''}</span><span>${esc(catLabel(c))}</span>
          <span class="r2-c">${_r2.byCat.get(c).length}</span></div>`;
      }).join('') +
      `<div class="r2-rt2">${t('Pivots from ticked categories')}</div>` +
      pivotBody +
      `<div class="r2-rt2">${t('Views')}</div>
       <div role="button" tabindex="0" onkeydown="report2KeyActivate(event)" class="r2-nav" onclick="report2SetView('cats')"><span class="i">▤</span> ${t('Categories')}</div>`;
    return;
  }
  // Categories view rail
  const link = (c) => {
    const on = report2State.openCat === c;
    const all = _r2.byCat.get(c);
    return `<div class="r2-rlink${on ? ' on' : ''}" role="button" tabindex="0" onclick="report2OpenCat('${c}')" onkeydown="report2RailKey(event,'${c}')">
      <span>${esc(catLabel(c))}</span><span class="r2-c">${all.length}</span></div>`;
  };
  const emptyLink = (c) => `<div class="r2-rlink zero${report2State.openCat === c ? ' on' : ''}" role="button" tabindex="0" onclick="report2OpenCat('${c}')" onkeydown="report2RailKey(event,'${c}')">
      <span>${esc(catLabel(c))}</span><span class="r2-c">0</span></div>`;
  rail.innerHTML =
    `<div class="r2-rt"><span>${t('Found')}</span><span>${_r2.found.length}</span></div>` +
    _r2.found.map(link).join('') +
    `<div role="button" tabindex="0" onkeydown="report2KeyActivate(event)" class="r2-rall${report2State.openCat === '__all__' ? ' on' : ''}" onclick="report2OpenCat('__all__')"><span class="i">▦</span> ${t('Show all')}</div>` +
    `<div role="button" tabindex="0" onkeydown="report2KeyActivate(event)" class="r2-emptytoggle" onclick="report2ToggleEmpty()"><span>${report2State.showEmpty ? '▾' : '▸'}</span> ${_r2.empty.length} ${t('empty categories (searched)')}</div>` +
    (report2State.showEmpty ? `<div class="r2-emptylist">${_r2.empty.map(emptyLink).join('')}</div>` : '') +
    `<div class="r2-rt2">${t('Views')}</div>
     <div role="button" tabindex="0" onkeydown="report2KeyActivate(event)" class="r2-nav" onclick="report2SetView('activity')"><span class="i">▚</span> ${t('Activity')}</div>`;
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
        <div class="r2-noresults-sub">${t('WayTrace searched all {c} categories across {n} archived pages and found nothing to extract.').replace('{c}', REPORT2_SCOPE.length).replace('{n}', ana)}</div>
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
        ? `${esc(f.first_seen || '?')} <span class="r2-arw">→</span> ${_r2IsLive(f) ? `<span class="r2-now">${esc(f.last_seen)}</span>` : `<span class="r2-end">${esc(f.last_seen || '?')}</span>`}`
        : '<span class="r2-end">·</span>';
      // Co-occurrence: an optional chip revealing other findings from the same
      // archived page. Purely opt-in — clicking the value copies; only this chip
      // expands the panel.
      const pid = f.metadata && f.metadata.source_page_id;
      const coCount = pid != null && _r2.byPage.has(pid) ? _r2.byPage.get(pid).length - 1 : 0;
      const chip = coCount > 0
        ? `<button class="r2-cooc-chip${report2State.expandedPage === pid ? ' on' : ''}" title="${escAttr(t('Other findings on the same archived page'))}" onclick="report2ToggleCooc(event, ${pid})">⋯ ${coCount}</button>`
        : '';
      const row = `<div class="r2-row">
        <span class="r2-val">
          <button class="r2-copy" title="${escAttr(t('Copy') + ': ' + f.value)}" onclick="report2Copy(event)" aria-label="${escAttr(t('Copy'))}">⧉</button>
          <span class="r2-val-text" title="${escAttr(f.value)}">${esc(f.value)}</span>${_r2Chip(f)}${chip}
        </span>
        <span class="r2-occ"><b>${f.occurrences || 1}</b></span>
        <span class="r2-span">${span}</span>
        <span class="r2-src">${makeSourceLink(f)}</span>
      </div>`;
      const panel = (coCount > 0 && report2State.expandedPage === pid) ? _r2CoocPanel(pid, f) : '';
      return row + panel;
    }).join('');
}

// Inline co-occurrence panel: the OTHER findings introduced by the same archived
// page, grouped by category, with a link to that page.
function _r2CoocPanel(pid, self) {
  const peers = (_r2.byPage.get(pid) || []).filter(x => x !== self);
  if (!peers.length) return '';
  const src = self.metadata && self.metadata.source_url;
  const byCat = new Map();
  for (const p of peers) { if (!byCat.has(p.category)) byCat.set(p.category, []); byCat.get(p.category).push(p); }
  const groups = [...byCat.entries()].map(([c, arr]) =>
    `<div class="r2-cooc-grp"><span class="r2-cooc-cat">${esc(catLabel(c))}</span>`
    + arr.slice(0, 8).map(p => `<span class="r2-cooc-val">${esc(String(p.value).slice(0, 60))}</span>`).join('')
    + (arr.length > 8 ? `<span class="r2-cooc-more">+${arr.length - 8}</span>` : '')
    + `</div>`).join('');
  return `<div class="r2-cooc-panel">
    <div class="r2-cooc-head">${t('Seen together on the same archived page')}${src ? ` <a href="${escAttr(src)}" target="_blank" rel="noopener">${t('view page')} ↗</a>` : ''}</div>
    ${groups}
  </div>`;
}
function report2ToggleCooc(ev, pid) {
  ev.stopPropagation();
  report2State.expandedPage = (report2State.expandedPage === pid) ? null : pid;
  report2RenderMain();
}

function report2CatBlock(cat, withActivity, isEmpty) {
  const total = _r2.byCat.get(cat) || [];
  const all = _r2ApplyPresence(total);   // still-present / disappeared filter
  const q = report2State.filter.toLowerCase();
  const list = q ? all.filter(f => f.value.toLowerCase().includes(q)) : all;
  const desc = CAT_DESCRIPTIONS[cat] ? t(CAT_DESCRIPTIONS[cat]) : '';
  const filtered = (list.length !== total.length)
    ? `<span class="r2-filtered">${list.length} ${t('shown')}</span>` : '';
  const head = `<div class="r2-dhead"><span class="r2-name">${esc(catLabel(cat))}</span><span class="r2-cnt">${total.length}</span>${filtered}
    <span role="button" tabindex="0" onkeydown="report2KeyActivate(event)" class="r2-copycol" onclick="report2CopyCol('${cat}', event)">${t('copy column')}</span></div>
    ${desc ? `<p class="r2-ddesc">${esc(desc)}</p>` : ''}`;

  if (isEmpty) {
    return head + `<div class="r2-emptystate">${t('Searched across every snapshot, found nothing in this category.')}</div>`;
  }
  if (!list.length) {
    // The category has findings, but none match the active presence/text filter.
    const why = report2State.presence === 'live' ? t('none still present')
      : report2State.presence === 'gone' ? t('none disappeared') : t('nothing matches the filter');
    return `<div class="r2-catblock">${head}<div class="r2-emptystate">${why}</div></div>`;
  }
  const CAP = 200;
  const rows = _r2Rows(list.slice(0, CAP));
  const more = list.length > CAP ? `<div class="r2-more">${t('Showing first')} ${CAP} ${t('of')} ${list.length}</div>` : '';
  const act = withActivity ? report2CatActivity(cat, all) : '';
  return `<div class="r2-catblock">${head}${rows}${more}${act}</div>`;
}

function report2CatActivity(cat, list) {
  // Axis spans exactly this category's findings (no dead years). "Gone" values
  // are faded based on the global archive end (not this local axis), so a value
  // that stopped before the archive's latest month reads as disappeared.
  _r2SetBoundsFrom(list);
  const lanes = list.slice(0, 24).map(f => _r2Lane(f.value, f.first_seen, f.last_seen, { faded: !_r2IsLive(f) })).join('');
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

  _r2SetBoundsFrom(shownFindings);   // axis spans exactly the checked lanes (no dead years)
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
      ? `<img class="r2-favimg" src="${escAttr(src)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><span class="r2-favph" style="display:none">◆</span>`
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
  copyText(val, null).then((ok) => {
    if (!ok) { showToast(t('Copy failed')); return; }
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
  // Copy exactly what's shown: honour the presence filter, then the text filter.
  const all = _r2ApplyPresence(_r2.byCat.get(cat) || []);
  const q = report2State.filter.toLowerCase();
  const list = q ? all.filter(f => f.value.toLowerCase().includes(q)) : all;
  const btn = ev && ev.currentTarget;
  copyText(list.map(f => f.value).join('\n'), null)
    .then((ok) => {
      if (!ok) { showToast(t('Copy failed')); return; }
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
window.report2KeyActivate = report2KeyActivate;
window.report2SetPresence = report2SetPresence;
window.report2ToggleCooc = report2ToggleCooc;
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
  currentDomainId = job.url_id;  // string url_id; report2 filters in place, so this never round-trips through the legacy URL
  sortCol = 'occurrences';
  sortDir = 'desc';
  findingsPage = 0;

  // Swap actions: drop Timeline/Export/Re-analyze, inject our v2 buttons.
  const actions = document.querySelector('#view-results .results-actions');
  if (actions && IS_EXPORT) {
    // Downloading, re-scanning, sharing a link and the expiry countdown are
    // all about the copy on the server. This file says what it is instead.
    const taken = (job.created_at || '').slice(0, 10);
    actions.innerHTML = `<div class="export-banner">${t('Offline copy.')} `
      + `${taken ? esc(t('This scan was taken on {d} and nothing in this file updates.')
                        .replace('{d}', taken)) : esc(t('Nothing in this file updates.'))}</div>`;
  } else if (actions) {
    const expires = job.expires_at ? ` <span class="v2-expires-badge">${t('expires')} ${relativeFutureTime(job.expires_at)}</span>` : '';
    const uid = encodeURIComponent(job.url_id);
    let shareBtn = '';
    actions.innerHTML = `
      <a class="btn btn-accent" id="v2-download-btn"
         href="/api/s/${uid}/export.html" download>${t('Download HTML')}</a>
      <a class="btn" href="/api/s/${uid}/export.json" download>${t('JSON')}</a>
      <a class="btn" href="/api/s/${uid}/export.csv" download>${t('CSV')}</a>
      ${job.can_publish === false ? '' :
        `<button class="btn" type="button" onclick="scanMore('${esc(job.domain)}')" title="${escAttr(t('Run a denser scan of this domain, reusing what was already found'))}">${t('Scan more')}</button>`}
      ${shareBtn}
      ${expires}
    `;
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
  report2State.presence = 'all';
  report2State.expandedPage = null;
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

function makeSourceLink(f) {
  if (f.metadata && f.metadata.source_url) {
    const url = f.metadata.source_url;
    const isArchive = url.includes('web.archive.org');
    const label = isArchive ? 'archive' : 'view';
    const cls = isArchive ? 'src-link' : 'src-link cc';
    return `<a class="${cls}" href="${escAttr(url)}" target="_blank" rel="noopener" title="View source page" onclick="event.stopPropagation()">${label}</a>`;
  }
  return '';
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
      <span>${esc(catLabel(c))}</span>
      <span class="exp-cat-item-count">${counts[c]}</span>
    </label>
  `).join('');
}

function toggleExportCat(cat, checked) {
  if (checked) exportSelectedCats.add(cat);
  else exportSelectedCats.delete(cat);
}

function renderExportFiltersHint() {
  // The legacy severity/search filter controls were removed when the old
  // results page became report2, so tolerate their absence instead of
  // throwing on a null element.
  const sevEl = document.getElementById('filter-severity');
  const searchEl = document.getElementById('filter-search');
  const sev = sevEl ? sevEl.value : '';
  const search = searchEl ? searchEl.value : '';
  const hints = [];
  if (sev) hints.push(`severity=${sev}`);
  if (search) hints.push(`search="${search}"`);
  if (activeCategory) hints.push(`category=${activeCategory}`);
  const el = document.getElementById('exp-filters-hint');
  el.textContent = hints.length
    ? `Applying: ${hints.join(', ')} (will limit export to filtered findings)`
    : t('No filters active (will export all selected categories)');
}

function buildExportData() {
  // Apply current filters if any are active. The legacy severity/search filter
  // controls were removed with the old results page (report2 replaced them), so
  // tolerate their absence instead of throwing on a null element.
  const sevEl = document.getElementById('filter-severity');
  const searchEl = document.getElementById('filter-search');
  const sev = sevEl ? sevEl.value : '';
  const search = searchEl ? (searchEl.value || '').toLowerCase() : '';
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
        let v = flat[c] == null ? '' : String(flat[c]);
        // A cell opening with one of these is a formula in Excel, LibreOffice
        // and Sheets, and every value here came out of someone else's archived
        // page. The apostrophe is what a spreadsheet reads as "text follows";
        // one character strips it back to what was on the page.
        if (/^[=+\-@\t\r]/.test(v)) v = "'" + v;
        return /["',\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
      }).join(',');
    });
    return [header, ...rows].join('\n');
  }
  if (format === 'markdown') {
    const byCat = {};
    for (const f of findings) (byCat[f.category] = byCat[f.category] || []).push(f);
    // Markdown is the format that gets pasted into somebody's written report,
    // and it used to start at the first category heading: nothing in the file
    // said which domain it was about or when the scan was taken. The machine
    // formats stay bare, a header would only get in jq's way.
    const job = _lastScanPayload || {};
    const taken = (job.created_at || '').slice(0, 10);
    const nCats = Object.keys(byCat).length;
    const lines = [];
    if (job.domain) lines.push(`# ${job.domain}`, '');
    lines.push([
      'WayTrace',
      `${findings.length} ${t(findings.length === 1 ? 'finding' : 'findings')}`
        + ` · ${nCats} ${t(nCats === 1 ? 'category' : 'categories')}`,
      taken ? `${t('scan of')} ${taken}` : '',
    ].filter(Boolean).join(' · '), '');
    for (const cat of Object.keys(byCat).sort()) {
      lines.push(`## ${catLabel(cat)}`);
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
    showToast(t('Copy failed'));
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
  showToast(t('Downloaded {n} findings').replace('{n}', data.length));
}

// Close on Escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.getElementById('export-drawer').classList.contains('open')) {
    closeExportDrawer();
  }
});

/* One copy path for the whole app, because there were five and three of them
   announced success without checking. writeText returns a promise: it rejects,
   it does not throw, so a try/catch wrapped around the bare call catches
   nothing and the "Copied" toast fires even when the clipboard refused.
   Returns true only if the text really landed. */
async function copyText(value, okMsg) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(value);
    ok = true;
  } catch (_) {
    // Denied permission, insecure context, or no Clipboard API: fall back to
    // the old selection trick before admitting defeat.
    try {
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand('copy');
      ta.remove();
    } catch (_) { ok = false; }
  }
  if (okMsg !== null) showToast(ok ? (okMsg || t('Copied')) : t('Copy failed'));
  return ok;
}

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
    showToast(t('Copy failed'));
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
        <a href="${escAttr(sourceUrl)}" target="_blank" rel="noopener">${esc(sourceUrl)}</a>
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
      ${shodan !== null ? `<div class="fd-hash-row"><span class="fd-hash-k">Shodan</span><code class="fd-hash-v">${esc(String(shodan))}</code><a class="fd-hash-pivot" href="${escAttr(shodanUrl)}" target="_blank" rel="noopener" title="${esc(t('Search this favicon on Shodan'))}">${t('pivot')} ↗</a></div>` : ''}
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

// Shared focus state: remember the element that was focused when a drawer opened
// so we can restore focus to it on close. One slot, since drawers aren't nested.
let _lastFocusBeforeDrawer = null;

function restoreFocus() {
  if (_lastFocusBeforeDrawer && typeof _lastFocusBeforeDrawer.focus === 'function') {
    try { _lastFocusBeforeDrawer.focus(); } catch (e) {}
  }
  _lastFocusBeforeDrawer = null;
}

/* ===== HISTORY ===== */
async function loadHistory() {
  // v2: the History view is now "My scans". The legacy v1 domains table is
  // hidden in public mode.
  await renderMyScans();
}

/* A row that says only "completed" gives no reason to open it, and a row that
   says only "failed" makes you open it to learn why. Both numbers were already
   in the API payload and simply were not shown. */
const MYSCANS = { q: '', state: 'all', sort: 'recent' };

/* Was five equal boxes with a big number and two lines of caption each: the
   canonical dashboard, and five cards to say what is one sentence. A date is
   not a metric either, and a caption under a number is a confession that the
   number does not speak for itself. One line, and the room goes to the list. */
function _myScansSummary(done) {
  const kept = (window._myScansAll || done).length;
  const ok = done.filter(s => s.status === 'completed').length;
  const ko = done.filter(s => s.status === 'failed').length;
  const finds = done.reduce((a, s) => a + (s.findings || 0), 0);
  const withExp = done.filter(s => s.expires_at).sort((a, b) => a.expires_at < b.expires_at ? -1 : 1);
  const oldest = withExp[0];
  const bit = (n, word) => `<b>${nfmt(n)}</b> ${esc(word)}`;
  const parts = [bit(kept, t('kept'))];
  if (ko) parts.push(`<span class="ms-ko">${bit(ko, t('ms.failed.word'))}</span>`);
  if (finds) parts.push(bit(finds, t('findings')));
  let line = parts.join('<span class="ms-dot">·</span>');
  if (oldest) {
    const when = fmtExpiresIn(oldest.expires_at);
    line += `<span class="ms-dot">·</span><span class="ms-exp">`
      + esc(t('next to expire')) + ' ' + `<b>${esc(oldest.domain)}</b>`
      + (when ? ' ' + esc(when) : '') + `</span>`;
  }
  return `<p class="ms-summary">${line}</p>`;
}

/* A filter removes rows, a sort reorders them. They were two identical pill
   groups side by side, which is decoration applied uniformly rather than form
   following the act. Filters are tabs carrying their own counts, so the reader
   sees what each one is worth before clicking and a state with nothing in it
   cannot be clicked at all. Sort is a select, because it is a choice among
   orders, not a set of things to switch on. */
function _myScansControls() {
  const rows = window._myScansDone || [];
  const n = {
    all: rows.length,
    completed: rows.filter(s => s.status === 'completed').length,
    failed: rows.filter(s => s.status === 'failed').length,
    shared: rows.filter(s => s.is_published).length,
  };
  const states = [['all', t('ms.all')], ['completed', t('ms.completed')], ['failed', t('ms.failed')]];
  const tabs = states.map(([k, lab]) => {
    const c = n[k] || 0;
    const on = MYSCANS.state === k;
    return `<button type="button" class="ms-tab${on ? ' on' : ''}" ${c ? '' : 'disabled'}
      onclick="_myScansSet('state','${k}')">${esc(lab)}<span class="ms-tabn">${c}</span></button>`;
  }).join('');
  const sorts = [['recent', t('Most recent')], ['findings', t('Most findings')]];
  return '<div class="ms-controls">'
    + `<div class="ms-tabs" role="tablist">${tabs}</div>`
    + `<input class="ms-filter" type="search" placeholder="${escAttr(t('filter by domain…'))}"`
    + ` value="${escAttr(MYSCANS.q)}" oninput="_myScansSet('q',this.value)">`
    + `<label class="ms-sort">${esc(t('Sort'))}`
    + `<select onchange="_myScansSet('sort',this.value)">`
    + sorts.map(([k, lab]) =>
        `<option value="${k}"${MYSCANS.sort === k ? ' selected' : ''}>${esc(lab)}</option>`).join('')
    + '</select></label>'
    + '</div>';
}

function _myScansSet(k, v) {
  MYSCANS[k] = v;
  if (k === 'q') { _myScansApply(); return; }   // typing must not lose focus
  const host = document.getElementById('my-scans');
  const ctl = host && host.querySelector('.ms-controls');
  if (ctl) ctl.outerHTML = _myScansControls();
  _myScansApply();
}

function _myScansApply() {
  const list = document.getElementById('myscans-list');
  if (!list) return;
  let rows = (window._myScansDone || []).slice();
  const q = MYSCANS.q.trim().toLowerCase();
  if (q) rows = rows.filter(s => (s.domain || '').toLowerCase().includes(q));
  if (MYSCANS.state === 'shared') rows = rows.filter(s => s.is_published);
  else if (MYSCANS.state !== 'all') rows = rows.filter(s => s.status === MYSCANS.state);
  if (MYSCANS.sort === 'findings') rows.sort((a, b) => (b.findings || 0) - (a.findings || 0));
  list.innerHTML = rows.length ? _myScansRows(rows)
    : `<div class="myscans-note">${esc(t('No scan matches that filter.'))}</div>`;
}

/* Grouped by day, because "when" is how people look for a scan they ran. */
function _myScansRows(rows) {
  const groups = new Map();
  for (const s of rows) {
    const g = MYSCANS.sort === 'findings' ? '' : _dayBucket(s.created_at);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(s);
  }
  return [...groups].map(([label, items]) =>
    (label ? `<div class="ms-group">${esc(label)}</div>` : '')
    + items.map(_myScansRow).join('')).join('');
}

function _dayBucket(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return t('Earlier');
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today - day) / 86400000);
  if (diff <= 0) return t('Today');
  if (diff === 1) return t('Yesterday');
  return t('Earlier');
}

function fmtExpiresIn(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const days = Math.ceil((d - Date.now()) / 86400000);
  if (days < 0) return '';
  if (days === 0) return t('today');
  if (days === 1) return t('in 1 day');
  return t('in {n} days').replace('{n}', days);
}

function nfmt(n) { return Number(n).toLocaleString(LANG === 'fr' ? 'fr-FR' : 'en-US'); }

function _myScansRow(s) {
  const uid = encodeURIComponent(s.url_id);
  let sharedBadge = '';
  const facts = s.status === 'failed'
    ? `<span class="ms-why" title="${escAttr(t(s.error || ''))}">${esc(s.error ? t(s.error) : t('no reason recorded'))}</span>`
    : [s.findings != null ? nfmt(s.findings) + ' ' + t('ms.findings') : '',
       s.pages ? nfmt(s.pages) + ' ' + t('ms.pages') : '']
        .filter(Boolean).map(x => `<span>${esc(x)}</span>`).join('');
  const dur = fmtScanDuration(s.created_at, s.completed_at);
  return `
    <div class="myscans-row" onclick="location.hash='#/s/${uid}'">
      <span class="myscans-domain">${esc(s.domain)}</span>
      <span class="myscans-status st-${esc(s.status)}">${esc(t(s.status))}</span>
      ${sharedBadge}
      <span class="ms-facts">${facts}</span>
      <span class="myscans-meta">
        <span class="myscans-when">${esc(fmtScanStamp(s.created_at))}</span>
        ${dur ? `<span class="myscans-dur">${esc(t('took'))} ${esc(dur)}</span>` : ''}
      </span>
      <span class="ms-rowacts" onclick="event.stopPropagation()">
        <button class="btn-mini" type="button" onclick="copyScanLink('${uid}')">${esc(t('Copy link'))}</button>
        <button class="btn-mini" type="button" onclick="location.hash='#/scope/'+encodeURIComponent('${escAttr(s.domain)}')">${esc(t('Re-run'))}</button>
      </span>
      ${_scanActionsCell(s.url_id, t('Delete scan'))}
    </div>`;
}

function copyScanLink(uid) {
  copyText(location.origin + '/#/s/' + uid, t('Link copied'));
}

async function renderMyScans() {
  const host = $('my-scans');
  if (!host) return;

  // Solo / self-hosted build: list EVERY scan this instance has run - it's a
  // single-user install, so they are all yours.
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
      <span class="myscans-meta">
        <span class="myscans-when">${esc(fmtScanStamp(s.created_at))}</span>
        ${fmtScanDuration(s.created_at, s.completed_at) ? `<span class="myscans-dur">${esc(t('took'))} ${esc(fmtScanDuration(s.created_at, s.completed_at))}</span>` : ''}
      </span>
      ${_scanActionsCell(s.url_id, t('Delete scan'))}
    </div>`).join('') + '</div>';
}

// Delete affordance for a My-scans row. A scan is not trivially destroyed by a
// single stray click: the cross turns into an explicit "Delete / Keep" pair
// (in place, no native dialog), and only the deliberate second click removes it.
function _scanActionsCell(uid, label) {
  return `<span class="myscans-actions" data-uid="${escAttr(uid)}" data-label="${escAttr(label)}">`
    + `<button class="myscans-del" title="${escAttr(label)}" aria-label="${escAttr(label)}"`
    + ` onclick="askDeleteMyScan(this, event)">&times;</button></span>`;
}

function askDeleteMyScan(btn, ev) {
  if (ev) ev.stopPropagation();
  const box = btn.closest('.myscans-actions');
  if (!box) return;
  box.classList.add('confirming');
  box.innerHTML =
    `<span class="myscans-confirm-q">${esc(t('Delete?'))}</span>`
    + `<button class="myscans-del-yes" type="button" onclick="confirmDeleteMyScan(this, event)">${esc(t('Delete'))}</button>`
    + `<button class="myscans-del-no" type="button" onclick="cancelDeleteMyScan(this, event)">${esc(t('Keep'))}</button>`;
}

function cancelDeleteMyScan(btn, ev) {
  if (ev) ev.stopPropagation();
  const box = btn.closest('.myscans-actions');
  if (!box) return;
  box.classList.remove('confirming');
  box.innerHTML = `<button class="myscans-del" title="${escAttr(box.dataset.label || '')}"`
    + ` aria-label="${escAttr(box.dataset.label || '')}" onclick="askDeleteMyScan(this, event)">&times;</button>`;
}

// Delete a scan the current visitor owns (the url_id is the capability). Only
// reached after the explicit in-row confirmation above.
async function confirmDeleteMyScan(btn, ev) {
  if (ev) ev.stopPropagation();
  const box = btn.closest('.myscans-actions');
  const urlId = box && box.dataset.uid;
  if (!urlId) return;
  try {
    const r = await fetch(API + '/api/s/' + encodeURIComponent(urlId), { method: 'DELETE' });
    if (!r.ok && r.status !== 404) {
      const d = await r.json().catch(() => ({}));
      showToast(t('Error: ') + (d.detail || r.statusText));
      return;
    }
    renderMyScans();
  } catch (e) {
    showToast(t('Network error: ') + e.message);
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
  const el = document.getElementById('kb-overlay');
  el.classList.add('visible');
  wtDialogOpened(el, closeKbHelp);
}

function closeKbHelp() {
  const el = document.getElementById('kb-overlay');
  el.classList.remove('visible');
  wtDialogClosed(el);
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
  // Ask the page, not the URL. A downloaded export renders the report with no
  // hash at all, so deriving this from location left every report shortcut
  // dead in the one context where the panel documenting them was still shown.
  const resultsEl = document.getElementById('view-results');
  const onResults = (resultsEl && resultsEl.classList.contains('active'))
                    || currentView === 'results';

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

  if (key === 'h' && !IS_EXPORT) {
    e.preventDefault();
    location.hash = '#/history';
    return;
  }

  if (key === 'n' && !IS_EXPORT) {
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
    if (r.ok) {
      // The footer used to carry a hand-written version string, which had
      // drifted to v1.8.2 while production ran 1.9.1. It comes from the API now.
      const d = await r.json();
      if (d.version) WT_VERSION = d.version;
      const el = document.getElementById('home-version');
      if (el && d.version) el.textContent = 'v' + d.version;
      // The wizard may already be on screen when this resolves.
      if (document.getElementById('setup-ua-preview')) setupOnContact();
    }
  } catch (e) {
    console.warn('Backend unreachable:', e.message);
  }
})();
