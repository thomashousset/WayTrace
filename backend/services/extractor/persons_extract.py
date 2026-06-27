"""Person-name extractor.

Combines three signals: ``<meta name="author">``-style tags, CSS classes
like ``author``/``byline``/``writer``, and JSON-LD ``@type`` Person/author
fields. All entries write to ``accum["persons"]``.
"""
from __future__ import annotations

import json
import re

from selectolax.parser import HTMLParser

from .helpers import update_entity


_PERSON_EXCLUDE = {
    "admin", "administrator", "author", "anonymous", "unknown", "undeveloped",
    "editor", "moderator", "root", "webmaster", "guest", "bot", "system",
    "staff", "team", "support", "default", "test", "null", "none",
}


# A token shaped like a name part: letters (with accents), apostrophes,
# hyphens, periods (for initials). 1-30 chars per token.
_NAME_TOKEN_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'.\-]{0,29}$")


def _looks_like_name(text: str) -> bool:
    """Heuristic: text reads as a person name (2-5 alphabetic tokens).

    Rejects bio-paragraph blobs that the class-based selectors otherwise
    sweep up (e.g. ``<div class="author-bio">Jane Doe is a senior engineer
    at WP. She has been …</div>``) because their split() yields >5 tokens
    or non-name characters.
    """
    if not text:
        return False
    text = text.strip()
    parts = text.split()
    if not (2 <= len(parts) <= 5):
        return False
    return all(_NAME_TOKEN_RE.match(p) for p in parts)


def _is_valid_person(name: str, domain: str) -> bool:
    """Filter out generic words, single words, and domain-derived names."""
    lower = name.lower().strip()
    if lower in _PERSON_EXCLUDE:
        return False
    if " " not in lower and len(lower) < 15:
        return False
    domain_base = domain.split(".")[0].lower()
    if lower == domain_base:
        return False
    return True


def _walk_jsonld_authors(data, month: str, accum: dict, domain: str = "") -> None:
    """Walk a JSON-LD graph and emit only nodes whose @type is Person.

    The legacy walker treated any dict carrying an "author" field as a
    Person record and emitted ``data["author"]["name"]``. On Schema.org
    pages where the article author is an Organization (very common: a
    school posting blog articles ``{ author: { @type: Organization,
    name: "Oteria" } }``), the org name leaked into the persons list.
    Now we only emit ``name`` from a node when @type explicitly says
    Person, and we additionally accept bare string ``author`` values
    (a common shorthand). Both paths are gated through
    :func:`_is_valid_person` so generic placeholders / domain-derived
    strings still get filtered.
    """
    if isinstance(data, dict):
        atype = data.get("@type")
        is_person = atype == "Person" or (
            isinstance(atype, list) and "Person" in atype
        )
        if is_person:
            name = (data.get("name") or "").strip()
            if name and _is_valid_person(name, domain):
                update_entity(
                    accum["persons"], name.lower(), month,
                    {"name": name, "context": "json-ld:Person"},
                )
        author = data.get("author")
        if isinstance(author, str) and author.strip():
            text = author.strip()
            if _is_valid_person(text, domain):
                update_entity(
                    accum["persons"], text.lower(), month,
                    {"name": text, "context": "json-ld:author-str"},
                )
        for v in data.values():
            _walk_jsonld_authors(v, month, accum, domain)
    elif isinstance(data, list):
        for item in data:
            _walk_jsonld_authors(item, month, accum, domain)


def extract_persons(
    tree: HTMLParser, raw_text: str, month: str, accum: dict,
    domain: str = "",
) -> None:
    for selector in ('meta[name="author"]', 'meta[property="article:author"]'):
        for node in tree.css(selector):
            name = node.attributes.get("content", "").strip()
            if name and _is_valid_person(name, domain):
                update_entity(
                    accum["persons"], name.lower(), month,
                    {"name": name, "context": "meta:author"},
                )

    # Restrict to leaf-ish elements (a, span, cite, b, strong, em) so we
    # don't sweep up <div class="author-bio">…long paragraphs…</div>. The
    # extra `_looks_like_name` shape check then drops any remaining noise
    # (bios, multi-line CSS-styled hover text, "by John Smith - SEO Expert").
    leaf_selectors = (
        "a[rel=author]",
        "a[class*=author]", "span[class*=author]", "cite[class*=author]",
        "b[class*=author]", "strong[class*=author]", "em[class*=author]",
        "a[class*=byline]", "span[class*=byline]", "cite[class*=byline]",
        "a[class*=writer]", "span[class*=writer]",
    )
    for selector in leaf_selectors:
        for node in tree.css(selector):
            text = node.text(strip=True)
            if not text or len(text) > 60:
                continue
            if not _looks_like_name(text):
                continue
            if not _is_valid_person(text, domain):
                continue
            update_entity(
                accum["persons"], text.lower(), month,
                {"name": text, "context": "html:class"},
            )

    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
            _walk_jsonld_authors(data, month, accum, domain=domain)
        except (json.JSONDecodeError, TypeError):
            continue
