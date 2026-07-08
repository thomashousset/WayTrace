"""All regex patterns and constants used by the extractor."""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Core patterns
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Cloudflare email protection: the email is XOR'd with the first byte of
# the hex blob. Seen everywhere WP/Drupal + Cloudflare are combined.
CF_EMAIL_RE = re.compile(r'data-cfemail\s*=\s*"([0-9a-fA-F]{8,})"')

# Textual obfuscation: foo [at] bar [dot] com, name(at)x(dot)y, etc.
# Kept strict so prose like "click [at] the [dot] below" is only recognised
# as an email when the reassembled string re-matches EMAIL_RE.
EMAIL_OBFUSCATED_AT_RE = re.compile(
    r"([A-Za-z0-9._%+\-]{2,})\s*[\[\(\{]\s*(?:at|@)\s*[\]\)\}]\s*"
    r"([A-Za-z0-9.\-]+?)\s*[\[\(\{]\s*(?:dot|\.)\s*[\]\)\}]\s*"
    r"([A-Za-z]{2,})",
    re.IGNORECASE,
)

# JS-concatenation obfuscation: var e = "foo" + "@" + "bar.com".
EMAIL_JS_CONCAT_RE = re.compile(
    r'"([A-Za-z0-9._%+\-]+)"\s*\+\s*"@"\s*\+\s*"([A-Za-z0-9.\-]+\.[A-Za-z]{2,})"'
)

PHONE_RE = re.compile(
    r"(?<![.\d/@])"
    r"(?:"
    # Branch 1: international + prefix, e.g. +33 1 42 68 53 00, +1-800-555-1234
    r"\+\d{1,3}[\s\-.]?\(?\d{1,4}\)?(?:[\s\-.]?\d{2,5}){1,4}"
    r"|"
    # Branch 2: parens area code, e.g. (800) 555-1234
    r"\(\d{2,4}\)[\s\-.]?\d{2,4}(?:[\s\-.]?\d{2,4})+"
    r"|"
    # Branch 3: separated groups (≥2 separators), e.g. 01 42 68 53 00
    r"\d{2,4}[\s\-.]\d{2,4}(?:[\s\-.]?\d{2,4})+"
    r")"
    r"(?![\d.])"
)

TRACKER_PATTERNS = {
    "GA_Universal": re.compile(r"UA-\d{4,10}-\d{1,2}"),
    # GA4 measurement IDs are always exactly 10 base36 chars after the
    # "G-" prefix. The looser `{8,12}` legacy bound let neighbouring noise
    # (e.g. `G-12345678` from any 8-digit "G-" prefixed string) through
    # while disagreeing with the dedicated extractor in analytics_ids.py.
    "GA4": re.compile(r"\bG-[A-Z0-9]{10}\b"),
    "GTM": re.compile(r"GTM-[A-Z0-9]{5,8}"),
    "Google_Ads": re.compile(r"AW-\d{9,12}"),
    "Meta_Pixel": re.compile(r"fbq\([^)]*[\"'](\d{14,16})[\"']"),
    # Hotjar site IDs are 5-8 digits in practice; the looser `{5,10}` bound
    # let arbitrary numeric runs through and conflicted with analytics_ids.
    "Hotjar": re.compile(r"hjid[:\s]*[\"']?(\d{5,8})[\"']?"),
    "Mixpanel": re.compile(r"mixpanel\.init\([\"']([a-f0-9]{32})[\"']"),
    "Yandex_Metrica": re.compile(r"ym\((\d{7,10})\s*,"),
}

# Every host is guarded with (?<![A-Za-z0-9]) so a look-alike domain glued to
# the left (notlinkedin.com, mytwitter.com, evilgithub.com) can't match, while
# a real subdomain (www.twitter.com, preceded by '.') still does.
SOCIAL_PATTERNS = {
    "twitter": re.compile(r"(?<![A-Za-z0-9])twitter\.com/(?!share|intent)([A-Za-z0-9_]{1,50})"),
    "x": re.compile(r"(?<![A-Za-z0-9])x\.com/(?!share|intent)([A-Za-z0-9_]{1,50})"),
    "linkedin": re.compile(
        r"(?<![A-Za-z0-9])linkedin\.com/(?:in|company|school|edu|org|pub|profile)/([A-Za-z0-9_\-%.]{1,100})"
    ),
    # Accept both facebook.com and the fb.com shortener; the URL is normalised
    # to facebook.com downstream. Excludes share/dialog/plugin/pixel endpoints.
    "facebook": re.compile(
        r"(?<![A-Za-z0-9])(?:facebook|fb)\.com/(?!sharer|share|dialog|plugins|tr[/?])([A-Za-z0-9_.]{1,100})"
    ),
    "instagram": re.compile(r"(?<![A-Za-z0-9])instagram\.com/([A-Za-z0-9_.]{1,100})"),
    "telegram": re.compile(r"(?<![A-Za-z0-9])t\.me/(?!joinchat)([A-Za-z0-9_]{3,50})"),
    "youtube": re.compile(
        r"(?<![A-Za-z0-9])youtube\.com/(?:channel/|@|user/|c/)([A-Za-z0-9_\-]{1,100})"
    ),
    "pinterest": re.compile(
        r"(?<![A-Za-z0-9])pinterest\.com/(?!pin/|search/|categories/|ideas/|today/)([A-Za-z0-9_\-]{2,60})"
    ),
    "github": re.compile(r"(?<![A-Za-z0-9])github\.com/([A-Za-z0-9_\-]{1,100})"),
    "tiktok": re.compile(r"(?<![A-Za-z0-9])tiktok\.com/@([A-Za-z0-9_.]{1,50})"),
    "snapchat": re.compile(r"(?<![A-Za-z0-9])snapchat\.com/add/([A-Za-z0-9_.]{1,50})"),
    # Discord server invite codes. `discord.gg/<code>` is the short form,
    # `discord.com/invite/<code>` the long form. Both pivot to a server
    # community that often reveals operator team / followers.
    "discord": re.compile(
        r"(?<![A-Za-z0-9])discord(?:\.gg|\.com/invite)/([A-Za-z0-9\-]{2,32})"
    ),
}

# --- Cloud Buckets ---

# S3 in two shapes, both anchored on amazonaws.com so a stray ".s3-" label on
# an unrelated host is not mistaken for a bucket:
#   virtual-hosted: <bucket>.s3[.-]<region>.amazonaws.com/...
#   path-style:     s3[.-]<region>.amazonaws.com/<bucket>...
S3_RE = re.compile(
    r"(?:[a-z0-9.\-]+\.s3(?:[.\-][a-z0-9\-]+)*\.amazonaws\.com"
    r"|s3(?:[.\-][a-z0-9\-]+)*\.amazonaws\.com/[a-z0-9._\-]+)"
    r"[^\s\"'<>]*",
    re.IGNORECASE,
)
GCS_RE = re.compile(r"storage\.googleapis\.com/[a-z0-9._\-]+", re.IGNORECASE)
AZURE_RE = re.compile(r"[a-z0-9]+\.blob\.core\.windows\.net[^\s\"'<>]*", re.IGNORECASE)
DO_SPACES_RE = re.compile(r"[a-z0-9.\-]+\.digitaloceanspaces\.com[^\s\"'<>]*", re.IGNORECASE)

CLOUD_BUCKET_PATTERNS = (S3_RE, GCS_RE, AZURE_RE, DO_SPACES_RE)

# --- API Keys / Secrets ---

# AWS access keys: all documented prefixes per AWS IAM guide.
# AKIA = long-term user, ASIA = temp STS, AIDA/AROA = IAM IDs (lower OSINT
# value but we want parity), ABIA/ACCA/AGPA/AIPA/ANPA/ANVA/ASCA = misc.
AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|AROA|ASCA)[0-9A-Z]{16}\b")
GOOGLE_API_RE = re.compile(r"(?<![A-Za-z0-9_\-])AIza[0-9A-Za-z_\-]{35}(?![A-Za-z0-9_\-])")
# Google OAuth client IDs are high-value: appear in archived web apps.
GOOGLE_OAUTH_CLIENT_RE = re.compile(r"\b\d{9,}-[a-z0-9]{32}\.apps\.googleusercontent\.com\b")
# Stripe: sk_/pk_/rk_ (restricted) × test/live. 24+ body chars.
STRIPE_RE = re.compile(r"\b(?:sk|pk|rk)_(?:test|live)_[0-9a-zA-Z]{24,}\b")
# Anchored both sides: a bare `key-<32>` token, not the tail of
# `cache-key-<md5>` / `data-key-<hash>` (md5 is exactly 32 chars, so those
# fragments otherwise read as a leaked Mailgun key and surface in LEAK).
MAILGUN_RE = re.compile(r"(?<![A-Za-z0-9\-])key-[0-9a-zA-Z]{32}\b")
# Twilio Account SID (AC…) and API Key SID (SK…). hex.
TWILIO_RE = re.compile(r"\b(?:AC|SK)[0-9a-fA-F]{32}\b")
SENDGRID_RE = re.compile(r"SG\.[0-9A-Za-z_\-]{22}\.[0-9A-Za-z_\-]{43}")
# Slack: hooks + bot/user/app tokens. xoxb-/xoxp-/xoxa-/xoxr-/xoxs-…
SLACK_WEBHOOK_RE = re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[a-zA-Z0-9]+")
SLACK_TOKEN_RE = re.compile(r"\bxox[bpoasr]-\d+-\d+-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?\b")
# GitHub: classic (gh[pousr]_) + fine-grained (github_pat_…). Fine-grained
# tokens are exactly 82 chars of [A-Za-z0-9_] after the prefix.
GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{82}\b")

# OpenAI: legacy `sk-` + 48 alnum chars, plus modern project / service-
# account / admin keys (`sk-proj-`, `sk-svcacct-`, `sk-admin-`) which are
# 100+ chars of [A-Za-z0-9_-]. Anchored on prefix so we don't collide
# with Stripe (uses `sk_` underscore) or random 48-char identifiers.
OPENAI_RE = re.compile(
    r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}\b"
    r"|\bsk-[A-Za-z0-9]{48}\b"
)

# Anthropic: api / session keys are `sk-ant-api01-`, `sk-ant-sid01-`, …
# (numeric version suffix). Body is base64-url-safe, 93+ chars.
ANTHROPIC_RE = re.compile(
    r"\bsk-ant-(?:api|sid|skey)\d{2}-[A-Za-z0-9_\-]{93,}\b"
)

# GitLab tokens: personal (glpat-), SCIM (glsoat-), deploy (gldt-),
# CI/CD (glcbt-), pipeline trigger (glptt-), runner (glrt-).
GITLAB_TOKEN_RE = re.compile(
    r"\bgl(?:pat|soat|dt|cbt|ptt|rt)-[A-Za-z0-9_\-]{20,}\b"
)

# HuggingFace user access token: `hf_` + 34-40 alnum.
HUGGINGFACE_RE = re.compile(r"\bhf_[A-Za-z0-9]{34,40}\b")

# Notion internal-integration secret: `secret_` + 43 alnum.
NOTION_RE = re.compile(r"\bsecret_[A-Za-z0-9]{43}\b")

# Discord bot token: <id-base64>.<6-char-time>.<27-char-hmac>.
DISCORD_TOKEN_RE = re.compile(
    r"\b[MN][A-Za-z\d_\-]{23,28}\.[A-Za-z\d_\-]{6,7}\.[A-Za-z\d_\-]{27,38}\b"
)

# Supabase personal access token: `sbp_` + 40 hex. (anon/service keys are
# JWTs and are caught by the jwt extractor instead.)
SUPABASE_RE = re.compile(r"\bsbp_[a-f0-9]{40}\b")

# DigitalOcean tokens: personal (dop_v1_), oauth (doo_v1_), refresh (dor_v1_),
# each followed by 64 hex.
DIGITALOCEAN_RE = re.compile(r"\bdo[opr]_v1_[a-f0-9]{64}\b")

# Shopify tokens: access (shpat_), custom-app (shpca_), private-app (shppa_),
# shared-secret (shpss_), each + 32 hex.
SHOPIFY_RE = re.compile(r"\bshp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}\b")

# Linear API key: `lin_api_` + 40 alnum.
LINEAR_RE = re.compile(r"\blin_api_[A-Za-z0-9]{40}\b")

# npm automation/publish token: `npm_` + 36 base62.
NPM_TOKEN_RE = re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")

# Sentry DSN. leaks org slug + project ID even when the public key is
# random. Format: https://<32hex>@<orgslug>.ingest[.<region>].sentry.io/<projectid>
SENTRY_DSN_RE = re.compile(
    r"https?://[a-f0-9]{32}@(?:o\d+\.)?(?:[a-z0-9-]+\.)?ingest(?:\.[a-z]{2})?\.sentry\.io/\d+",
    re.IGNORECASE,
)

# Mapbox public access token. JWT-shaped, prefixed `pk.`. Distinct from
# Stripe (`pk_` underscore) and OpenAI (`sk-`).
MAPBOX_TOKEN_RE = re.compile(
    r"\bpk\.eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{20,}\b"
)

# Telegram bot token: <8-10 digit bot id>:<35 base64url chars>.
# Bot tokens grant full bot control. high LEAK value when accidentally
# embedded in archived JS/HTML.
TELEGRAM_BOT_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")

# Discord webhook URL. `/api/webhooks/<id>/<token>` lets anyone post
# to the webhook channel. Distinct surface from Slack webhooks we
# already cover.
DISCORD_WEBHOOK_RE = re.compile(
    r"https?://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+",
    re.IGNORECASE,
)

# --- Job boards (ATS tenants) --------------------------------------------
# Each match resolves to the company slug on the platform. Reveals
# hiring activity, often surfaces employee names on job postings.
JOB_BOARD_PATTERNS = {
    "greenhouse": re.compile(
        r"\bboards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
    "lever": re.compile(
        r"\b(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
    "workable": re.compile(
        r"\bapply\.workable\.com/([a-z0-9_\-]{2,60})\b"
        r"|\b([a-z0-9_\-]{2,60})\.workable\.com\b",
        re.IGNORECASE,
    ),
    "ashby": re.compile(
        r"\bjobs\.ashbyhq\.com/([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
    "personio": re.compile(
        r"\b([a-z0-9_\-]{2,60})\.jobs\.personio\.(?:com|de)\b",
        re.IGNORECASE,
    ),
    "recruitee": re.compile(
        r"\b([a-z0-9_\-]{2,60})\.recruitee\.com\b",
        re.IGNORECASE,
    ),
    "bamboohr": re.compile(
        r"\b([a-z0-9_\-]{2,60})\.bamboohr\.com/(?:jobs|careers)\b",
        re.IGNORECASE,
    ),
    "smartrecruiters": re.compile(
        r"\bcareers\.smartrecruiters\.com/([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
}

# --- Auth provider tenants -----------------------------------------------
# Identity infrastructure fingerprint: which IdP / SSO platform fronts
# the org's authentication. Pivot to tenant management UI.
AUTH_PROVIDER_PATTERNS = {
    "auth0": re.compile(
        r"\bhttps?://([a-z0-9_\-]{2,60})(?:\.[a-z]{2})?\.auth0\.com\b",
        re.IGNORECASE,
    ),
    "okta": re.compile(
        r"\bhttps?://([a-z0-9_\-]{2,60})\.(?:okta|oktapreview)\.com\b",
        re.IGNORECASE,
    ),
    # AWS Cognito user pool URLs: <region>.amazoncognito.com/<userpool>
    "cognito": re.compile(
        r"\bhttps?://(?:cognito-idp\.[a-z0-9-]+\.amazonaws\.com/([a-z0-9-]+_[A-Za-z0-9]+)"
        r"|([a-z0-9_\-]{2,60})\.auth\.[a-z0-9-]+\.amazoncognito\.com)\b",
        re.IGNORECASE,
    ),
    # Keycloak: realm name lives in the URL path /auth/realms/<realm>/
    "keycloak": re.compile(
        r"/auth/realms/([a-zA-Z0-9_\-]{2,60})/",
    ),
    # WorkOS / Stytch / Clerk modern alternatives.
    "workos": re.compile(
        r"\bhttps?://api\.workos\.com\b|\bworkos_[A-Za-z0-9_\-]+\b",
        re.IGNORECASE,
    ),
    "clerk": re.compile(
        r"\bhttps?://([a-z0-9_\-]{2,60})\.clerk\.accounts\.dev\b"
        r"|\bclerk\.[a-z0-9_\-]+\.com\b",
        re.IGNORECASE,
    ),
}

# --- Bug bounty / disclosure programs --------------------------------------
# Each match resolves to the program slug on the platform. Reserved
# slugs (FAQ, signup, listings) are excluded so we don't emit ghosts
# from generic site links.
BUG_BOUNTY_PATTERNS = {
    "hackerone": re.compile(
        r"\bhackerone\.com/(?!hacktivity|opportunities|reports|directory|"
        r"sitemap|signup|users|login|leaderboard|teams)"
        r"([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
    "bugcrowd": re.compile(
        r"\bbugcrowd\.com/(?!faq|leaderboard|program-list|signin|"
        r"engagements|sign-up|disclosures|crowdcontrol)"
        r"([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
    "intigriti": re.compile(
        r"\bintigriti\.com/(?:companies/|programs/|researcher/programs/)?"
        r"(?!login|signup|search|api|hall-of-fame|company)"
        r"([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
    "yeswehack": re.compile(
        r"\byeswehack\.com/(?:programs/)?"
        r"(?!login|signup|hall-of-fame|directory)"
        r"([a-z0-9_\-]{2,60})\b",
        re.IGNORECASE,
    ),
}

# --- Captcha provider site keys --------------------------------------------
# reCAPTCHA v2/v3 site keys: `6L` + 38 base64url chars. Distinguishable
# format. Tested against archived live sites.
RECAPTCHA_SITEKEY_RE = re.compile(r"\b6L[A-Za-z0-9_\-]{38}\b")
# Cloudflare Turnstile: `0x4AAAAAAA` prefix + base64url body.
TURNSTILE_SITEKEY_RE = re.compile(r"\b0x4AAAAAAA[A-Za-z0-9_\-]{14,30}\b")
# hCaptcha: site keys are UUIDv4-shaped. too generic on its own, so we
# only match when the keyword "hcaptcha" appears within ~80 chars before
# the candidate UUID. Caller does the windowing in the extractor.
HCAPTCHA_UUID_RE = re.compile(
    r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b",
    re.IGNORECASE,
)

# Arkose Labs / FunCaptcha: the public key is a UUID embedded directly in the
# enforcement script URL (client-api.arkoselabs.com/v2/<PUBLIC_KEY>/api.js).
# Reading it from the URL is unambiguous, so no context window is needed.
ARKOSE_PUBKEY_RE = re.compile(
    r"arkoselabs\.com/v2/([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})/",
    re.IGNORECASE,
)
# GeeTest: the `gt` id is a 32-char hex string, indistinguishable from an md5,
# so the extractor only accepts it with a GeeTest context keyword nearby.
GEETEST_ID_RE = re.compile(r"\b[a-f0-9]{32}\b", re.IGNORECASE)
# Friendly Captcha: sitekeys are uppercase base36 prefixed `FC`. Collides with
# little else but still gated on a friendlycaptcha/frc context to be safe.
FRIENDLY_CAPTCHA_SITEKEY_RE = re.compile(r"\bFC[A-Z0-9]{10,30}\b")

# --- Public status pages ---------------------------------------------------
# Each matches a tenant slug on a known status-page provider; surfacing
# them lets the investigator fetch the public incident history.
STATUS_PAGE_PATTERNS = {
    "statuspage.io": re.compile(
        r"\bhttps?://([a-z0-9][a-z0-9-]{1,40})\.statuspage\.io\b",
        re.IGNORECASE,
    ),
    "instatus.com": re.compile(
        r"\bhttps?://([a-z0-9][a-z0-9-]{1,40})\.instatus\.com\b",
        re.IGNORECASE,
    ),
    # Better Stack hosted pages live on *.betteruptime.com (legacy) or
    # status.betterstack.com. The old `|status` alternation also matched
    # any *.status.com host, an unrelated domain, so it is dropped.
    "betterstack": re.compile(
        r"\bhttps?://([a-z0-9][a-z0-9-]{1,40})\.betteruptime\.com\b",
        re.IGNORECASE,
    ),
    "freshstatus": re.compile(
        r"\bhttps?://([a-z0-9][a-z0-9-]{1,40})\.freshstatus\.io\b",
        re.IGNORECASE,
    ),
    "statushub": re.compile(
        r"\bhttps?://([a-z0-9][a-z0-9-]{1,40})\.statushub\.io\b",
        re.IGNORECASE,
    ),
}

API_KEY_PATTERNS = {
    "AWS": AWS_KEY_RE,
    "Google_API": GOOGLE_API_RE,
    "Google_OAuth_Client": GOOGLE_OAUTH_CLIENT_RE,
    "Stripe": STRIPE_RE,
    "Mailgun": MAILGUN_RE,
    "Twilio": TWILIO_RE,
    "SendGrid": SENDGRID_RE,
    "Slack_Webhook": SLACK_WEBHOOK_RE,
    "Slack_Token": SLACK_TOKEN_RE,
    "GitHub": GITHUB_TOKEN_RE,
    "OpenAI": OPENAI_RE,
    "Anthropic": ANTHROPIC_RE,
    "GitLab": GITLAB_TOKEN_RE,
    "HuggingFace": HUGGINGFACE_RE,
    "Notion": NOTION_RE,
    "Discord_Token": DISCORD_TOKEN_RE,
    "Sentry_DSN": SENTRY_DSN_RE,
    "Mapbox": MAPBOX_TOKEN_RE,
    "Telegram_Bot": TELEGRAM_BOT_RE,
    "Discord_Webhook": DISCORD_WEBHOOK_RE,
    "Supabase": SUPABASE_RE,
    "DigitalOcean": DIGITALOCEAN_RE,
    "Shopify": SHOPIFY_RE,
    "Linear": LINEAR_RE,
    "npm": NPM_TOKEN_RE,
}

# --- Constants ---

EMAIL_EXCLUDE = {
    "noreply", "no-reply", "example", "email", "user", "test",
    "info@example", "your", "name@", "sample", "placeholder",
    "changeme", "youremail", "yourname",
}

# Placeholder email patterns (full addresses). covers RFC2606 reserved
# names + common template dummies seen in CMS defaults and starter kits.
EMAIL_PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net",
    "email.com", "email.fr", "domain.com", "domain.fr",
    "test.com", "test.org", "mail.com",
    "yourdomain.com", "yourcompany.com", "company.com",
    "acme.com", "localhost",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

# Code / asset file extensions. A "name@version.ext" string (e.g.
# ``lodash@4.17.15-<hash>.js`` from a bundler manifest) trivially matches the
# email regex (``.js`` reads as a TLD). These are never real email addresses.
ASSET_EXTENSIONS = {
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".scss", ".less",
    ".map", ".json", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".vue",
    ".wasm", ".min", ".html", ".htm", ".php", ".xml",
}

# A domain part that starts with a version number (``4.17.15``, ``10.3.3``)
# is a JS module spec (``pkg@1.2.3``), not an email domain.
EMAIL_SEMVER_DOMAIN_RE = re.compile(r"^\d+\.\d+")

# --- Technology detection from script/link URLs ---

SCRIPT_TECH_PATTERNS = {
    # JS frameworks
    "jQuery": re.compile(r"jquery[.\-/]", re.IGNORECASE),
    "React": re.compile(r"react(?:\.min)?\.js|react-dom", re.IGNORECASE),
    "Angular": re.compile(r"angular(?:\.min)?\.js|angular\.io", re.IGNORECASE),
    "Vue.js": re.compile(r"\bvue(?:\.min)?\.js|vuejs\.org", re.IGNORECASE),
    # "svelte" only as a delimited package/file token (/svelte@, /svelte/,
    # svelte.min.js, svelte.dev) - never glued into a blog/asset slug like
    # "why-we-left-svelte" or "svelte-island-page.js".
    "Svelte": re.compile(r"[/@]svelte[@/.]|\bsvelte\.dev\b", re.IGNORECASE),
    # Meta-frameworks
    "Next.js": re.compile(r"_next/static|__next", re.IGNORECASE),
    "Nuxt": re.compile(r"_nuxt/", re.IGNORECASE),
    "Remix": re.compile(r"/build/_shared/|__remixContext", re.IGNORECASE),
    "Gatsby": re.compile(r"/page-data/[^\"']+/page-data\.json|gatsby-chunk", re.IGNORECASE),
    "Astro": re.compile(r"/_astro/|astro-island", re.IGNORECASE),
    # UI kits
    "Bootstrap": re.compile(r"bootstrap(?:\.min)?\.(?:js|css)", re.IGNORECASE),
    "Tailwind": re.compile(r"tailwindcss|tailwind\.min\.css", re.IGNORECASE),
    "Font Awesome": re.compile(r"font-awesome|fontawesome", re.IGNORECASE),
    # Utilities
    "Lodash": re.compile(r"lodash(?:\.min)?\.js", re.IGNORECASE),
    "D3.js": re.compile(r"\bd3(?:\.min)?\.js|d3js\.org", re.IGNORECASE),
    "Moment.js": re.compile(r"moment(?:\.min)?\.js", re.IGNORECASE),
    # CDN & fonts
    "Google Fonts": re.compile(r"fonts\.googleapis\.com", re.IGNORECASE),
    "Cloudflare cdnjs": re.compile(r"cdnjs\.cloudflare\.com", re.IGNORECASE),
    "Unpkg": re.compile(r"unpkg\.com", re.IGNORECASE),
    "jsDelivr": re.compile(r"cdn\.jsdelivr\.net", re.IGNORECASE),
    # --- Observability / error tracking ------------------------------
    "Sentry": re.compile(r"@sentry/|browser\.sentry-cdn\.com|sentry-trace", re.IGNORECASE),
    "LogRocket": re.compile(r"cdn\.lr-(?:ingest|in|intake)|logrocket", re.IGNORECASE),
    "Datadog RUM": re.compile(r"datadog(?:hq)?-browser-(?:agent|logs|rum)", re.IGNORECASE),
    "New Relic": re.compile(r"js-agent\.newrelic\.com|nr-data\.net", re.IGNORECASE),
    # --- Product analytics (distinct from TRACKER_PATTERNS IDs) ------
    "Segment": re.compile(r"cdn\.segment\.com/analytics\.js", re.IGNORECASE),
    "PostHog": re.compile(r"posthog(?:-js|\.com/static)", re.IGNORECASE),
    "Amplitude": re.compile(r"cdn\.amplitude\.com|amplitude-js", re.IGNORECASE),
    "FullStory": re.compile(r"fullstory\.com/s/fs\.js|edge\.fullstory\.com", re.IGNORECASE),
    "Plausible": re.compile(r"plausible\.io/js/", re.IGNORECASE),
    "Matomo": re.compile(r"matomo\.(?:js|php)|piwik\.(?:js|php)", re.IGNORECASE),
    # --- Payment ------------------------------------------------------
    "Stripe.js": re.compile(r"js\.stripe\.com/v\d", re.IGNORECASE),
    "PayPal SDK": re.compile(r"paypal\.com/sdk/js|paypalobjects\.com", re.IGNORECASE),
    # --- Chat / support ----------------------------------------------
    "Intercom": re.compile(r"widget\.intercom\.io|js\.intercomcdn\.com", re.IGNORECASE),
    "Zendesk Widget": re.compile(r"static\.zdassets\.com|ekr\.zdassets\.com", re.IGNORECASE),
    "Drift": re.compile(r"js\.driftt\.com|widget\.drift\.com", re.IGNORECASE),
    # --- Marketing automation ----------------------------------------
    "HubSpot": re.compile(r"js\.hs-scripts\.com|js\.hsforms\.net|js\.hubspot\.com", re.IGNORECASE),
    # --- Auth ---------------------------------------------------------
    "Auth0": re.compile(r"cdn\.auth0\.com|auth0-spa-js", re.IGNORECASE),
    "Okta": re.compile(r"okta-(?:signin|auth)-js|\.okta\.com/", re.IGNORECASE),
    # --- Feature flags / A-B -----------------------------------------
    "Optimizely": re.compile(r"cdn\.optimizely\.com", re.IGNORECASE),
    "LaunchDarkly": re.compile(r"launchdarkly(?:-js-client-sdk|\.com/sdk)", re.IGNORECASE),
    # --- Search -------------------------------------------------------
    "Algolia": re.compile(r"algoliasearch|@algolia/", re.IGNORECASE),
    # --- Tag managers -------------------------------------------------
    "Tealium": re.compile(r"tags\.tiqcdn\.com", re.IGNORECASE),
    # --- Headless CMS -------------------------------------------------
    "Contentful": re.compile(r"cdn\.contentful\.com|images\.ctfassets\.net", re.IGNORECASE),
    "Sanity": re.compile(r"cdn\.sanity\.io|@sanity/client", re.IGNORECASE),
    # --- Website builders --------------------------------------------
    "Webflow": re.compile(r"assets\.website-files\.com|webflow\.com/(?:css|js)", re.IGNORECASE),
    "Framer": re.compile(r"framerusercontent\.com|framer\.com/m/", re.IGNORECASE),
}

TECH_COMMENT_RE = re.compile(
    r"<!--\s*(WordPress|Joomla|Drupal|Typo3|Magento)[\s\d.]*-->", re.IGNORECASE
)

# --- Wayback artifact patterns ---

WAYBACK_TOOLBAR_RE = re.compile(
    r"<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->",
    re.DOTALL,
)
WAYBACK_SCRIPT_RE = re.compile(
    r'<script[^>]+src="/_static/[^"]*"[^>]*>.*?</script>',
    re.DOTALL | re.IGNORECASE,
)
WAYBACK_DIV_RE = re.compile(
    r'<div\s+id="wm-ipp-base"[^>]*>.*?</div>\s*</div>\s*</div>',
    re.DOTALL | re.IGNORECASE,
)

# --- CMS class indicators ---

CMS_CLASS_INDICATORS = {
    "wp-content": "WordPress",
    "wp-includes": "WordPress",
    "drupal": "Drupal",
    "joomla": "Joomla",
}

JWT_RE = re.compile(
    r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"
)

# --- Internal IPs (RFC1918 + localhost + link-local + CGNAT) ---

# Link-local 169.254.0.0/16 is a HIGH-value signal: the AWS IMDS address
# (169.254.169.254) and SSRF evidence live here. CGNAT 100.64.0.0/10
# appears in mobile carrier infra and modern k8s CNIs.
INTERNAL_IP_RE = re.compile(
    r"(?<![.\d])"
    r"(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r")"
    r"(?!\.\d)"
)

# --- Google Adsense ---

ADSENSE_PUB_RE = re.compile(r"ca-pub-(\d{10,16})")
ADSENSE_SLOT_RE = re.compile(r"""data-ad-slot\s*=\s*["'](\d{10,})["']""")
# Google AdMob app publisher id (mobile ad SDK). Same keyspace as AdSense but a
# distinct "ca-app-pub-" prefix, so it must be matched before/separately.
ADMOB_RE = re.compile(r"ca-app-pub-(\d{10,16})")

# --- JS inline URLs ---

JS_URL_RE = re.compile(r"""https?://[^\s"'`<>)\]},;]{15,500}""")

JS_API_ASSIGNMENT_RE = re.compile(
    r"""(?:apiUrl|api_url|baseUrl|base_url|API_URL|BASE_URL|API_BASE|"""
    r"""api_base|endpoint|apiEndpoint|api_endpoint|serverUrl|server_url)"""
    r"""\s*[:=]\s*["'`](https?://[^\s"'`<>]+)["'`]""",
    re.IGNORECASE,
)

# --- Connection strings ---

CONNSTRING_RE = re.compile(
    # Left boundary so a scheme name glued to a longer word (custommysql://) or
    # a db URL embedded inside another URL's path/query is not extracted.
    r"(?<![A-Za-z0-9+.\-])"
    r"(mysql|mariadb|postgres(?:ql)?|cockroachdb|mongodb(?:\+srv)?|rediss?|"
    r"amqps?|smtps?|ftp|ldaps?|mssql|sqlserver|oracle|cassandra|neo4j(?:\+s)?|"
    r"clickhouse)"
    r"://[^\s\"'<>]{5,300}",
    re.IGNORECASE,
)

# --- Cryptocurrency addresses ---
#
# Candidate-finding regexes only. Every hit is *gated* through a checksum or
# context validator in ``crypto_extract.py``. these patterns will happily
# match MD5 hashes, UUIDs, commit SHAs, etc., so never trust the raw match.

# Bitcoin
BTC_LEGACY_RE = re.compile(r"(?<![A-Za-z0-9])[13][a-km-zA-HJ-NP-Z1-9]{25,34}(?![A-Za-z0-9])")
BTC_BECH32_RE = re.compile(r"(?<![A-Za-z0-9])bc1[ac-hj-np-z02-9]{6,87}(?![A-Za-z0-9])", re.IGNORECASE)
# Ethereum (and EVM-compatible chains)
ETH_RE = re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
# Monero (95 chars after the leading 4)
XMR_RE = re.compile(r"(?<![A-Za-z0-9])4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}(?![A-Za-z0-9])")
# Litecoin
LTC_LEGACY_RE = re.compile(r"(?<![A-Za-z0-9])[LM3][a-km-zA-HJ-NP-Z1-9]{25,34}(?![A-Za-z0-9])")
LTC_BECH32_RE = re.compile(r"(?<![A-Za-z0-9])ltc1[ac-hj-np-z02-9]{6,87}(?![A-Za-z0-9])", re.IGNORECASE)
# Dogecoin (P2PKH starts with D, length 34)
DOGE_RE = re.compile(r"(?<![A-Za-z0-9])D[5-9A-HJ-NP-U][a-km-zA-HJ-NP-Z1-9]{32}(?![A-Za-z0-9])")
# Tron (T followed by 33 base58 chars, total 34)
TRX_RE = re.compile(r"(?<![A-Za-z0-9])T[a-km-zA-HJ-NP-Z1-9]{33}(?![A-Za-z0-9])")
# Solana (Base58, 32-44 chars). No checksum. gated by context keyword.
SOL_RE = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])")
# Ripple/XRP (r + 24-34 base58 chars, ripple alphabet)
XRP_RE = re.compile(r"(?<![A-Za-z0-9])r[rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz]{24,34}(?![A-Za-z0-9])")

CRYPTO_PATTERNS = {
    "btc": [BTC_LEGACY_RE, BTC_BECH32_RE],
    "eth": [ETH_RE],
    "xmr": [XMR_RE],
    "ltc": [LTC_LEGACY_RE, LTC_BECH32_RE],
    "doge": [DOGE_RE],
    "trx": [TRX_RE],
    "sol": [SOL_RE],
    "xrp": [XRP_RE],
}

# --- French business / training identifiers ---
# SIREN: 9 digits, Luhn-validated. SIRET: 14 digits (SIREN + 5-digit
# establishment), Luhn-validated. TVA intracommunautaire: FR + 2 control
# chars (digit/letter) + SIREN. RCS: free-form "RCS <city> B|A <SIREN>".
# Qualiopi: French training-org cert, "<YY>FOR<NNNNN>" optionally followed
# by ".<sub>". Anchored on word boundaries to avoid grabbing portions of
# longer numeric runs.
#
# The group separator is captured as a backreference so all positions
# agree: real SIRENs are written either ``123 456 789`` (spaces),
# ``123.456.789`` (dots), or ``123456789`` (bare). Mixed/asymmetric
# formatting like ``867.852832`` is almost always a floating-point
# number, not a SIREN, and is rejected by the backreference.
SIREN_RE = re.compile(r"(?<![\d])\d{3}([ .]?)\d{3}\1\d{3}(?![\d])")
SIRET_RE = re.compile(r"(?<![\d])\d{3}([ .]?)\d{3}\1\d{3}\1\d{5}(?![\d])")
TVA_FR_RE = re.compile(r"\bFR[ ]?[0-9A-HJ-NP-Z]{2}[ .]?\d{3}[ .]?\d{3}[ .]?\d{3}\b")
RCS_RE = re.compile(
    r"\bRCS\s+([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ\-]{1,40}(?:\s[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ\-]{1,40}){0,3})\s+(?:[AB]\s+)?(\d{3}[ .]?\d{3}[ .]?\d{3})\b",
    re.IGNORECASE,
)
QUALIOPI_RE = re.compile(r"(?<![A-Za-z0-9])(\d{2})FOR(\d{5})(?:\.(\d+))?(?![A-Za-z0-9])")

# RNCP (Répertoire National des Certifications Professionnelles) -
# France Compétences certification IDs. Two surface forms:
#   1. Bare token "RNCP12345" or "RNCP 12345" in prose / footers.
#   2. URL on the official registry: francecompetences.fr/recherche/rncp/<id>.
# Codes are 4-6 digits. old certs are 4-digit, recent ones 5-6.
RNCP_BARE_RE = re.compile(r"\bRNCP[\s\-]?(\d{4,6})\b", re.IGNORECASE)
RNCP_URL_RE = re.compile(
    r"francecompetences\.fr/(?:[a-z\-/]+/)?rncp/?(\d{4,6})\b", re.IGNORECASE
)
