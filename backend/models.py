from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DOMAIN_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}$"
)

IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

TIMESTAMP_RE = re.compile(r"^\d{14}$")
WAYBACK_URL_RE = re.compile(r"^https?://[^\s]{3,2048}$")
DATE_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _normalize_domain(v: str) -> str:
    v = v.strip().lower()
    if len(v) > 255:
        raise ValueError("Domain too long (max 255 characters)")
    for prefix in ("https://", "http://"):
        if v.startswith(prefix):
            raise ValueError("Provide a domain, not a URL (no http(s)://)")
    v = v.removeprefix("www.").rstrip("/")
    if IP_RE.match(v):
        raise ValueError("IP addresses are not supported, use a domain name")
    if not DOMAIN_RE.match(v):
        raise ValueError(f"Invalid domain format: {v}")
    return v


class SnapshotRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str
    url: str

    @field_validator("timestamp")
    @classmethod
    def _ts_format(cls, v: str) -> str:
        if not TIMESTAMP_RE.match(v):
            raise ValueError("timestamp must be 14 digits (YYYYMMDDhhmmss)")
        return v

    @field_validator("url")
    @classmethod
    def _url_format(cls, v: str) -> str:
        # The scraper embeds this URL into a Wayback fetch; requiring a
        # plain http(s) scheme blocks the obvious SSRF-adjacent attempts
        # (javascript:, data:, file:, ftp: that the user could try).
        if not WAYBACK_URL_RE.match(v):
            raise ValueError("url must be a plain http(s) URL")
        return v


class SnapshotDetail(BaseModel):
    timestamp: str
    url: str
    digest: str | None = None


class PathGroup(BaseModel):
    path: str
    score: int  # 1=low, 2=homepage, 3=high
    count: int
    first: str  # YYYYMMDDhhmmss
    last: str
    snapshots: list[SnapshotDetail]


class ScanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    # 'max' is the completeness-first preset: skip dedup, raise caps,
    # and do per-path coverage instead of budget allocation. Use when
    # you're investigating an incident and would rather pay wall-time
    # than miss a snapshot.
    depth: Literal["quick", "standard", "full", "max"] = "standard"
    categories: list[str] | None = None
    smart_dedup: bool = True
    # Drop any snapshot whose URL contains one of these substrings (case-
    # insensitive), e.g. ["blog", "tag", "author"] to skip a noisy blog. The
    # interactive picker applies this client-side too; this covers the fallback
    # crawl path (huge domains where preflight is skipped).
    exclude_keywords: list[str] | None = None

    @field_validator("exclude_keywords")
    @classmethod
    def _clean_keywords(cls, v: list[str] | None) -> list[str] | None:
        if not v:
            return None
        cleaned: list[str] = []
        for kw in v:
            kw = (kw or "").strip().lower()
            if kw and len(kw) <= 64 and kw not in cleaned:
                cleaned.append(kw)
            if len(cleaned) >= 50:
                break
        return cleaned or None

    @field_validator("date_from", "date_to")
    @classmethod
    def _date_month(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not DATE_MONTH_RE.match(v):
            raise ValueError("date must be YYYY-MM")
        return v

    @field_validator("cap")
    @classmethod
    def cap_bounds(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("cap must be >= 1")
        return v


class DateRange(BaseModel):
    first: str | None = None
    last: str | None = None


class SubdomainGroup(BaseModel):
    subdomain: str
    snapshot_count: int
    first: str | None = None
    last: str | None = None


class PreflightResponse(BaseModel):
    domain: str
    total_snapshots: int
    html_snapshots: int
    unique_paths: int
    unique_content: int
    date_range: DateRange
    suggested_config: ScanConfig
    path_groups: list[PathGroup] = []
    subdomain_groups: list[SubdomainGroup] = []


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    config: ScanConfig | None = None
    # Upper bound on selected_snapshots keeps a malicious client from
    # forcing a 5-million-item in-memory allocation in the pipeline.
    selected_snapshots: list[SnapshotRef] | None = Field(default=None, max_length=5000)
    # Upfront publish choice. When true, the backend publishes the scan to
    # the public feed as soon as it completes successfully. Stored on the
    # job and applied by run_scan() in its finally block; survives the
    # client closing their tab (the JS-only auto-publish wouldn't).
    publish_on_complete: bool = False
    # Opt-in: email the signed-in user a link when the scan finishes. Honoured
    # only on the hosted service (needs an account email + Resend).
    notify_on_complete: bool = False

    @field_validator("domain", mode="before")
    @classmethod
    def normalize_domain(cls, v: str) -> str:
        return _normalize_domain(v)


class JobResponse(BaseModel):
    job_id: str


class ScanCreateResponse(BaseModel):
    """v2 scan submission response, public-facing url_id + queue info."""
    job_id: str
    url_id: str
    url: str
    status: str
    position: int
    eta_seconds: int


class JobStatus(BaseModel):
    id: str
    domain: str
    status: str
    progress: int
    step: str
    created_at: datetime
    updated_at: datetime
    meta: dict[str, Any] | None = None
    results: dict[str, Any] | None = None
    url_id: str | None = None
    position: int | None = None
    eta_seconds: int | None = None
    total_in_queue: int | None = None


class HealthResponse(BaseModel):
    status: str
    active_jobs: int
    uptime_seconds: float


class StatsResponse(BaseModel):
    total_scans_run: int
    active_jobs: int


# --- v2 models ---

class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    config: ScanConfig | None = None

    @field_validator("domain", mode="before")
    @classmethod
    def normalize_domain_v2(cls, v: str) -> str:
        return _normalize_domain(v)


class CollectResponse(BaseModel):
    domain_id: int
    status: str


class CollectStatus(BaseModel):
    domain_id: int
    domain: str
    phase: str
    status: str
    progress: float
    total_snapshots: int
    snapshots_indexed: int
    pages_downloaded: int
    pages_failed: int
    started_at: str | None = None
    updated_at: str | None = None
    pages_selected: int = 0
    coverage_pct: float = 0.0
