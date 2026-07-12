from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Single source of truth for the tool version, surfaced in the API (/api/health,
# OpenAPI) and injected into the frontend footer.
APP_VERSION = "1.5.0"

# Shared User-Agent for every archive.org request (CDX collector, page scraper,
# favicon fetcher). One polite identity so the Internet Archive can attribute
# and contact us.
USER_AGENT = f"WayTrace/{APP_VERSION} (OSINT research tool; +https://github.com/HXLLO/WayTrace)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

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
    archive_rate_max: int = 80            # ceiling: ~1.33 req/s (below the ~105/min that got refused on 2600.eu)
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
    job_ttl_seconds: int = 7200
    max_active_jobs: int = 10

    # v2 public-mode queue caps. Scans are I/O-bound on archive.org; keep only
    # 2 running at once so the aggregate archive.org load stays low (politeness,
    # not a memory limit) and extra scans queue rather than pile on. The global
    # rate limiter above is the real ceiling regardless of this.
    max_active_total: int = 1
    max_queue_total: int = 15
    max_active_per_ip: int = 1
    # Hard ceiling on snapshots scanned per scan on the HOSTED service, to keep
    # archive.org load bounded and scans fast. The selection stays representative
    # (year-proportional). Set to 0 to disable the ceiling entirely — that's the
    # mode for a self-hosted / local install, which can scan a domain in full.
    hosted_snapshot_ceiling: int = 5000
    scan_retention_days: int = 7
    cleanup_interval_seconds: int = 3600

    # Security: hide OpenAPI schema + Swagger UI by default in prod.
    # Set EXPOSE_API_DOCS=1 in dev/local for interactive exploration.
    expose_api_docs: bool = False

    # Only trust CF-Connecting-IP / X-Forwarded-For for the client IP when a
    # known proxy (Cloudflare) actually sits in front. Off by default: our
    # deployment terminates TLS at Caddy, which overwrites X-Real-IP with the
    # real remote host, so a direct client cannot forge its IP to dodge the
    # per-IP caps. Set TRUST_CLOUDFLARE=1 only if Cloudflare fronts the app.
    trust_cloudflare: bool = False

    # Set IS_PRODUCTION=1 in deploy/.env.prod. Enables production boot checks
    # (e.g. refusing to start with the default SECRET_KEY).
    is_production: bool = False
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

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Database
    database_url: str = "/data/waytrace.db"

    # Auth / accounts (v3). secret_key signs session + magic-link JWTs; override
    # in prod via SECRET_KEY. Email is sent via Resend when resend_api_key is
    # set, otherwise links are logged (dev fallback). public_base_url is used to
    # build absolute links in emails.

    # Rate limiter (for slow collection; be polite to archive.org)
    rate_limit_initial_delay: float = 0.15
    rate_limit_min_delay: float = 0.1
    rate_limit_max_delay: float = 300.0
    rate_limit_speedup_factor: float = 0.9
    rate_limit_speedup_streak: int = 10
    rate_limit_backoff_factor: float = 3.0
    rate_limit_429_pause: float = 120.0

    @field_validator("max_concurrent_scrapes")
    @classmethod
    def _scrapes_bounds(cls, v: int) -> int:
        if v < 1 or v > 50:
            raise ValueError("max_concurrent_scrapes must be between 1 and 50")
        return v

    @field_validator("max_active_jobs")
    @classmethod
    def _jobs_bounds(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_active_jobs must be >= 1")
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



settings = Settings()
