from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # Polite defaults for archive.org. 8 parallel requests with 0.25-0.75 s
    # jitter avoids the 429 storms we saw at 30 concurrent / 0.02 s delay.
    # This is the PER-SCAN cap; archive_global_concurrency caps the aggregate
    # across all running scans so more scans can run without raising the peak
    # load on archive.org (and thus the ban risk).
    max_concurrent_scrapes: int = 8
    # Process-wide ceiling on simultaneous archive.org requests, shared by every
    # running scan. Kept below the old worst case (max_active_total * per-scan =
    # 2 * 8 = 16) on purpose: a lone scan still runs at 8, but four concurrent
    # scans share 12 instead of demanding 32.
    archive_global_concurrency: int = 12
    job_ttl_seconds: int = 7200
    max_active_jobs: int = 10

    # v2 public-mode queue caps. Scans are I/O-bound on archive.org, so running
    # 4 in parallel is safe now that archive_global_concurrency caps the
    # aggregate request rate; it just drains the queue faster between users.
    max_active_total: int = 4
    max_queue_total: int = 20
    max_active_per_ip: int = 3
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
    archive_request_timeout: int = 60
    archive_retry_count: int = 3
    scan_timeout_seconds: int = 3600
    # Wall-clock budget for the scrape phase. archive.org latency is erratic, so
    # rather than let a scan drag on (or hit the hard job timeout and lose
    # everything), once this many seconds elapse we stop scraping, keep the pages
    # already fetched ("fresh"), and let the pipeline extract that subset so the
    # scan still completes. 0 disables the budget (scrape until done).
    scrape_budget_seconds: int = 0
    scrape_delay_min: float = 0.25
    scrape_delay_max: float = 0.75
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
