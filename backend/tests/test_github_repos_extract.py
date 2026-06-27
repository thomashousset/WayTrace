"""Tests for the github_repos extractor."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{
        "html": html,
        "url": "https://example.com/",
        "timestamp": "20220601120000",
    }]
    return extract_all(pages, "example.com")["github_repos"]


def _pairs(items: list[dict]) -> set[str]:
    return {f"{it['owner']}/{it['repo']}" for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_plain_github_repo_url():
    html = '<a href="https://github.com/acme/widget">repo</a>'
    assert "acme/widget" in _pairs(_run(html))


def test_detects_repo_with_subpath():
    html = '<a href="https://github.com/acme/widget/blob/main/README.md">source</a>'
    assert "acme/widget" in _pairs(_run(html))


def test_detects_raw_githubusercontent():
    html = '<img src="https://raw.githubusercontent.com/octo/cat/main/logo.png">'
    assert "octo/cat" in _pairs(_run(html))


def test_detects_github_pages_subdomain():
    html = '<a href="https://acme.github.io/docs/">docs</a>'
    assert "acme/docs" in _pairs(_run(html))


def test_dedupes_same_repo_across_urls():
    html = (
        '<a href="https://github.com/acme/widget/issues/1">a</a>'
        '<a href="https://github.com/acme/widget/pulls">b</a>'
        '<a href="https://raw.githubusercontent.com/acme/widget/main/x">c</a>'
    )
    items = _run(html)
    pairs = _pairs(items)
    assert pairs == {"acme/widget"}
    assert items[0]["occurrences"] >= 2


def test_strips_dot_git_suffix():
    html = '<a href="https://github.com/acme/widget.git">clone</a>'
    assert "acme/widget" in _pairs(_run(html))


def test_pivot_url_points_at_repo_root():
    html = '<a href="https://github.com/acme/widget/commit/deadbeef">a</a>'
    items = _run(html)
    assert items[0]["pivot_url"] == "https://github.com/acme/widget"


# ---------------------------------------------------------------------------
# Negative
# ---------------------------------------------------------------------------


def test_rejects_owner_only_url():
    html = '<a href="https://github.com/acme">profile</a>'
    assert _run(html) == []


def test_rejects_reserved_owners():
    html = (
        '<a href="https://github.com/settings/profile">set</a>'
        '<a href="https://github.com/marketplace/actions">mp</a>'
    )
    assert _run(html) == []


def test_rejects_reserved_repo_slots():
    html = '<a href="https://github.com/acme/followers">followers</a>'
    assert _run(html) == []


def test_rejects_extended_reserved_repo_tabs():
    """GitHub owner-tab pages (wiki, network, pulse, commits, branches,
    releases, tags, actions, deployments, discussions, security, advisories,
    members, traffic, etc.) must not be treated as repos."""
    suspects = (
        "wiki", "network", "pulse", "commits", "branches", "releases",
        "tags", "actions", "deployments", "discussions", "community",
        "security", "advisories", "members", "policies", "forks",
        "watchers", "graphs", "traffic", "billing", "achievements",
    )
    html = "".join(
        f'<a href="https://github.com/acme/{slot}">x</a>' for slot in suspects
    )
    assert _run(html) == []


def test_ignores_plain_text_without_url():
    assert _run("<p>I use github a lot.</p>") == []


def test_ignores_non_github_domain():
    html = '<a href="https://gitlab.com/acme/widget">gitlab</a>'
    assert _run(html) == []
