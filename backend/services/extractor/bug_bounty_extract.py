"""Detect references to bug-bounty / disclosure programs.

Matches links to HackerOne, Bugcrowd, Intigriti and YesWeHack and stores
the program slug. The presence of one of these is a useful pivot: scope
pages list assets and sometimes acknowledged researchers.
"""
from __future__ import annotations

from .helpers import update_entity
from .patterns import BUG_BOUNTY_PATTERNS


_PIVOT_TEMPLATES = {
    "hackerone": "https://hackerone.com/{handle}",
    "bugcrowd": "https://bugcrowd.com/{handle}",
    "intigriti": "https://app.intigriti.com/programs/{handle}",
    "yeswehack": "https://yeswehack.com/programs/{handle}",
}

# Platform marketing / nav / account paths that are not program slugs.
# The per-platform regex lookaheads catch some of these, but Intigriti
# matches a bare ``intigriti.com/<word>`` and the others miss footer links
# like /blog, /pricing, /security. One shared denylist covers them all.
_RESERVED_HANDLES = {
    "blog", "pricing", "contact", "careers", "customers", "researchers",
    "research", "about", "about-us", "product", "products", "solutions",
    "security", "company", "companies", "features", "resources", "terms",
    "privacy", "press", "partners", "platform", "docs", "documentation",
    "faq", "support", "help", "login", "signup", "sign-up", "search",
    "programs", "program-list", "current_user", "bug_bounty",
    "hall-of-fame", "directory", "leaderboard", "events",
}


def extract_bug_bounty_programs(
    raw_text: str, month: str, accum: dict
) -> None:
    """Populate ``accum['bug_bounty_programs']`` with each program seen."""
    for platform, pattern in BUG_BOUNTY_PATTERNS.items():
        for match in pattern.finditer(raw_text):
            handle = match.group(1).rstrip("/").lower()
            if not handle or len(handle) < 2:
                continue
            if handle in _RESERVED_HANDLES:
                continue
            key = f"{platform}:{handle}"
            pivot = _PIVOT_TEMPLATES.get(platform, "").format(handle=handle)
            update_entity(
                accum["bug_bounty_programs"],
                key,
                month,
                {
                    "platform": platform,
                    "handle": handle,
                    "pivot_url": pivot,
                },
            )
