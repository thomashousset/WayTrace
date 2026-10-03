from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root, resolved from this file so it does not depend on the CWD uvicorn
# was started from (the README's manual quick start runs from backend/).
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Env files loaded by the runtime `settings` singleton below: the repo-root
# .env always, plus a CWD-local .env which takes priority when it differs.
# Kept out of model_config so Settings() instantiated bare stays hermetic.
ENV_FILES = (_REPO_ROOT / ".env", Path(".env"))

# Single source of truth for the tool version, surfaced in the API (/api/health,
# OpenAPI) and injected into the frontend footer.
APP_VERSION = "2.0.2"


class Settings(BaseSettings):
    # extra="ignore": unknown env vars (e.g. a setting removed in a later
    # release that still lingers in someone's .env) are ignored instead of
    # crashing the boot, so upgrades never break on a stale .env.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Polite defaults for archive.org. archive.org throttles by DROPPING the TCP
    # connection well before it returns HTTP 429, so a low concurrency plus the
    # scraper's connection-error back-off (see services/scraper.py) is what keeps
    # a scan from cascading into hundreds of connection failures and getting the
    # server IP blocked. This is the PER-SCAN cap; archive_global_concurrency
    # caps the aggregate across all running scans.
    max_concurrent_scrapes: int = 4
    # Process-wide ceiling on simultaneous archive.org requests, shared by every
    # running scan, so N parallel scans never exceed this in flight. Kept low on
    # purpose: archive.org rate-limits by connection count, and staying at ~3 in
    # flight (with the delays below) keeps us under its throttling threshold so a
    # normal scan never trips the circuit breaker.
    archive_global_concurrency: int = 3
    # Process-wide archive.org request-rate governor (services/archive_rate.py).
    # The rate ADAPTS (AIMD, like TCP congestion control): it starts at
    # archive_rate_per_minute, creeps up by _step after _increase_interval
    # seconds with no connection-refusal, and HALVES on the first refusal, kept
    # within [_min, _max]. Bias is deliberately slow-up / fast-down so the server
    # IP is never pushed past archive.org's (dynamic, unpublished) tolerance.
    # archive_rate_per_minute is the STARTING rate; _max is the hard ceiling it
    # may probe up to. Values are requests per minute.
    archive_rate_per_minute: int = 75     # start: 1.25 req/s
    archive_rate_min: int = 60            # floor: 1 req/s
    archive_rate_max: int = 80            # ceiling: ~1.33 req/s (below the ~105/min refusal point measured during tuning)
    archive_rate_step: int = 15           # additive increase: +0.25 req/s per bump
    archive_rate_increase_interval: float = 90.0   # seconds clean before a bump
    archive_rate_decrease_factor: float = 0.5      # multiplicative decrease on a refusal
    archive_rate_burst: int = 6
    # Hard-block (connection-refusal) cooldown ESCALATION. A first refusal is
    # usually a temporary, rate-based reject that clears in seconds, so the first
    # pause is short; it only lengthens if refusals keep recurring close together
    # (the signature of a real block). cooldown = base * 2**streak, capped at max;
    # a quiet gap longer than _streak_reset resets the streak to 0.
    archive_hard_cooldown_base: int = 120     # 2 min: first hard-block pause
    archive_hard_cooldown_max: int = 1800     # 30 min: ceiling for repeated blocks
    archive_hard_streak_reset: int = 900      # 15 min quiet since last block = fresh incident

    # v2 public-mode queue caps. Scans are I/O-bound on archive.org; keep few
    # running at once so the aggregate archive.org load stays low (politeness,
    # not a memory limit) and extra scans queue rather than pile on. The global
    # rate limiter above is the real ceiling regardless of this, which is why
    # the WAITING queue can be deep (waiting jobs send archive.org nothing):
    # launch-day traffic queues up instead of getting "service full" errors.
    # The per-IP cap is only an abuse net (kept high so CGNAT users are safe).
    max_active_total: int = 1
    max_queue_total: int = 100
    max_active_per_ip: int = 2
    # Hard ceiling on snapshots scanned per scan on the HOSTED service, to keep
    # archive.org load bounded and scans fast. The selection stays representative
    # (year-proportional). Set to 0 to disable the ceiling entirely — that's the
    # mode for a self-hosted / local install, which can scan a domain in full.
    hosted_snapshot_ceiling: int = 3000
    # Scales the adaptive snapshot cap (services/filters.py) before the depth
    # preset clamps apply. 1.0 = tuned defaults; raising it fetches more
    # snapshots per scan (longer scans, preset max caps still win).
    snapshot_cap_multiplier: float = 1.0
    # Scans are kept (and thus reused by the already-scanned guardrail) for this
    # long. 14 days = a domain isn't re-scanned within two weeks.
    scan_retention_days: int = 14
    cleanup_interval_seconds: int = 3600
    # Optional demo scan. When set (EXAMPLE_SCAN_DOMAIN), a completed scan of
    # this domain is persisted with a far-future expiry (never purged by
    # retention) and /api/example-scan returns its url_id. Empty (the default)
    # disables the feature.
    example_scan_domain: str = ""

    # Self-host instance personalization (first-run wizard + Settings panel).
    # All four are empty by default so a fresh instance behaves exactly as
    # before; the hosted service leaves them empty too. instance_name brands
    # the UI, operator_contact is folded into the archive.org User-Agent (see
    # services/identity.py), default_theme is applied before paint when the
    # browser has no saved theme, and default_categories (empty = all 48)
    # narrows extraction for scans submitted without an explicit set.
    instance_name: str = ""
    operator_contact: str = ""
    default_theme: str = ""
    default_categories: list[str] = Field(default_factory=list)

    # Self-host configuration panel (#/config + /api/config): edit the scan and
    # archive.org politeness settings from the UI, persisted in app_state.
    # Enabled on a self-hosted install; your machine, your rules.
    config_panel_enabled: bool = True

    # Build identity, baked into the image by the Dockerfile (WAYTRACE_COMMIT /
    # WAYTRACE_BUILT_AT build args) and surfaced by /api/health. Answering "what
    # exactly is running" should not require diffing checksums against git.
    # Empty when running from source, which is the honest answer there.
    waytrace_commit: str = ""
    waytrace_built_at: str = ""

    # Security: hide OpenAPI schema + Swagger UI by default in prod.
    # Set EXPOSE_API_DOCS=1 in dev/local for interactive exploration.
    expose_api_docs: bool = False

    # Only trust CF-Connecting-IP / X-Forwarded-For for the client IP when
    # Cloudflare actually sits in front. Off by default: a reverse proxy that
    # overwrites X-Real-IP with the real remote host already prevents a direct
    # client from forging its IP to dodge the per-IP caps. Set
    # TRUST_CLOUDFLARE=1 only if Cloudflare fronts the app.
    trust_cloudflare: bool = False

    # Reject request bodies larger than this (bytes) before reading them, so an
    # unauthenticated POST can't OOM the single worker. The largest legitimate
    # body is selected_snapshots (max 5000 small objects); 2 MB is ample.
    max_request_body_bytes: int = 2_000_000
    archive_request_timeout: int = 60
    archive_retry_count: int = 3
    scan_timeout_seconds: int = 3600
    # Wall-clock budget for the scrape phase. archive.org latency is erratic, so
    # rather than let a scan drag on (or hit the hard job timeout and lose
    # everything), once this many seconds elapse we stop scraping, keep the pages
    # already fetched ("fresh"), and let the pipeline extract that subset so the
    # scan still completes. 0 disables the budget (scrape until done).
    scrape_budget_seconds: int = 0
    scrape_delay_min: float = 0.5
    scrape_delay_max: float = 1.2
    scrape_max_retries: int = 3
    log_level: str = "INFO"
    # Both default to "" meaning "beside the database", which is the directory
    # every deployment already keeps (the /data volume under Docker). Before
    # that, both lived in the container's ephemeral layer: every rebuild wiped
    # the CDX cache and destroyed the only record of why a scan failed.
    cdx_cache_dir: str = ""
    log_dir: str = ""

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Database. Docker images pin DATABASE_URL=/data/waytrace.db explicitly
    # (Dockerfile + every compose file); this default only serves bare-metal
    # runs, where /data is not creatable, so it lands at the repo root.
    database_url: str = str(_REPO_ROOT / "waytrace.db")


    @field_validator("max_concurrent_scrapes")
    @classmethod
    def _scrapes_bounds(cls, v: int) -> int:
        if v < 1 or v > 50:
            raise ValueError("max_concurrent_scrapes must be between 1 and 50")
        return v

    @field_validator("archive_request_timeout")
    @classmethod
    def _timeout_bounds(cls, v: int) -> int:
        if v < 5 or v > 120:
            raise ValueError("archive_request_timeout must be between 5 and 120")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]



settings = Settings(_env_file=ENV_FILES)
