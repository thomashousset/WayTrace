"""Detect ATS / job-board tenants.

Greenhouse, Lever, Workable, Ashby, Personio, Recruitee, BambooHR and
SmartRecruiters. Useful as a hiring signal and because public job
postings often expose internal team structure.
"""
from __future__ import annotations

from .helpers import update_entity
from .patterns import JOB_BOARD_PATTERNS


_PIVOT_TEMPLATES = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "personio": "https://{slug}.jobs.personio.com",
    "recruitee": "https://{slug}.recruitee.com",
    "bamboohr": "https://{slug}.bamboohr.com/careers",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
}

# Slugs that indicate the platforms' own pages, not real tenants.
_RESERVED_SLUGS = {
    "www", "app", "api", "blog", "help", "docs", "support",
    "login", "signup", "search", "embed", "boards-api",
    "careers", "jobs",  # generic pages on multi-domain providers
    # Providers' own content/marketing hosts (resources.workable.com, …),
    # not customer ATS tenants.
    "resources", "go", "info", "status", "widget", "assets", "cdn",
}


def extract_job_boards(raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['job_boards']`` with detected ATS tenants."""
    for platform, pattern in JOB_BOARD_PATTERNS.items():
        for match in pattern.finditer(raw_text):
            # Patterns have alternations, take the first non-empty group.
            slug = next((g for g in match.groups() if g), None)
            if not slug:
                continue
            slug = slug.strip("/").lower()
            if not slug or len(slug) < 2:
                continue
            if slug in _RESERVED_SLUGS:
                continue
            key = f"{platform}:{slug}"
            pivot = _PIVOT_TEMPLATES.get(platform, "").format(slug=slug)
            update_entity(
                accum["job_boards"],
                key,
                month,
                {
                    "platform": platform,
                    "slug": slug,
                    "pivot_url": pivot,
                },
            )
