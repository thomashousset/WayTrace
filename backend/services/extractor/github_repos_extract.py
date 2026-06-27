"""GitHub repository pivot extractor.

Normalizes every GitHub surface we find (github.com, raw.githubusercontent.com,
owner.github.io) into a canonical ``owner/repo`` key. Owner-only URLs and
reserved paths (settings, issues, pulls, marketplace, ...) are discarded.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from .helpers import update_entity


# Reserved owner slots that are GitHub-internal, not user/org accounts.
_RESERVED_OWNERS = {
    "settings", "issues", "pulls", "marketplace", "notifications",
    "explore", "topics", "trending", "collections", "events",
    "sponsors", "orgs", "features", "pricing", "about", "contact",
    "security", "enterprise", "customer-stories", "readme",
    "site", "apps", "login", "logout", "signup", "new",
    "dashboard", "codespaces", "search",
}

# Reserved sub-paths that mean "not a repo root" even when the owner is real.
# These are GitHub-internal owner-tabs / nav targets, not user repos.
_RESERVED_REPOS = {
    "settings", "followers", "following", "repositories", "projects",
    "packages", "stars", "sponsoring", "people", "teams", "tabs",
    # Owner-level GitHub UI surfaces commonly linked but never repos.
    "wiki", "network", "pulse", "commits", "branches", "releases",
    "tags", "actions", "deployments", "discussions", "community",
    "security", "advisories", "members", "policies", "forks",
    "watchers", "graphs", "traffic", "billing", "achievements",
}


_GITHUB_COM_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9_.\-]{0,38}))/"
    r"([A-Za-z0-9](?:[A-Za-z0-9_.\-]{0,99}))",
    re.IGNORECASE,
)

_RAW_RE = re.compile(
    r"https?://raw\.githubusercontent\.com/([A-Za-z0-9](?:[A-Za-z0-9_.\-]{0,38}))/"
    r"([A-Za-z0-9](?:[A-Za-z0-9_.\-]{0,99}))",
    re.IGNORECASE,
)

# owner.github.io or owner.github.io/repo
_PAGES_RE = re.compile(
    r"https?://([A-Za-z0-9](?:[A-Za-z0-9_\-]{0,38}))\.github\.io"
    r"(?:/([A-Za-z0-9](?:[A-Za-z0-9_.\-]{0,99})))?",
    re.IGNORECASE,
)


def _is_valid(owner: str, repo: str) -> bool:
    if not owner or not repo:
        return False
    if owner.lower() in _RESERVED_OWNERS:
        return False
    if repo.lower() in _RESERVED_REPOS:
        return False
    # Strip trailing .git for normalization equality, but reject noise suffixes.
    if repo.endswith(".git") and len(repo) <= 4:
        return False
    return True


def _normalize_repo(repo: str) -> str:
    # Trim obvious URL fragments (anchor/query) if they leaked through the regex.
    for ch in ("#", "?"):
        if ch in repo:
            repo = repo.split(ch, 1)[0]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo


def _emit(accum: dict, owner: str, repo: str, raw_url: str, month: str) -> None:
    owner = owner.strip()
    repo = _normalize_repo(repo.strip())
    if not _is_valid(owner, repo):
        return
    key = f"{owner.lower()}/{repo.lower()}"
    update_entity(
        accum["github_repos"],
        key,
        month,
        {
            "owner": owner,
            "repo": repo,
            "raw_url": raw_url,
            "pivot_url": f"https://github.com/{owner}/{repo}",
        },
    )


def extract_github_repos(
    tree: HTMLParser, raw_text: str, month: str, accum: dict
) -> None:
    """Populate ``accum['github_repos']`` with owner/repo pairs."""

    for m in _GITHUB_COM_RE.finditer(raw_text):
        _emit(accum, m.group(1), m.group(2), m.group(0), month)

    for m in _RAW_RE.finditer(raw_text):
        _emit(accum, m.group(1), m.group(2), m.group(0), month)

    for m in _PAGES_RE.finditer(raw_text):
        owner = m.group(1)
        repo = m.group(2) or f"{owner}.github.io"
        _emit(accum, owner, repo, m.group(0), month)
