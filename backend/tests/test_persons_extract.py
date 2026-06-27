"""Tests for the persons extractor."""
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
    return extract_all(pages, "example.com")["persons"]


def _names(items: list[dict]) -> set[str]:
    return {it["name"] for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_meta_author():
    html = '<meta name="author" content="Jane Doe">'
    items = _run(html)
    assert "Jane Doe" in _names(items)
    entry = next(it for it in items if it["name"] == "Jane Doe")
    assert entry["context"] == "meta:author"


def test_detects_article_author_meta():
    html = '<meta property="article:author" content="Bob Marley">'
    items = _run(html)
    assert "Bob Marley" in _names(items)
    entry = next(it for it in items if it["name"] == "Bob Marley")
    assert entry["context"] == "meta:author"


def test_detects_jsonld_person():
    html = '<script type="application/ld+json">{"@type":"Person","name":"John Smith"}</script>'
    items = _run(html)
    assert "John Smith" in _names(items)
    entry = next(it for it in items if it["name"] == "John Smith")
    assert entry["context"] == "json-ld:Person"


def test_detects_jsonld_person_type_list():
    # @type may be a list; "Person" anywhere in it counts.
    html = '<script type="application/ld+json">{"@type":["Person","Thing"],"name":"Grace Hopper"}</script>'
    items = _run(html)
    assert "Grace Hopper" in _names(items)


def test_detects_jsonld_author_string():
    # Bare string author shorthand is accepted.
    html = '<script type="application/ld+json">{"@type":"Article","author":"Alice Cooper"}</script>'
    items = _run(html)
    assert "Alice Cooper" in _names(items)
    entry = next(it for it in items if it["name"] == "Alice Cooper")
    assert entry["context"] == "json-ld:author-str"


def test_detects_rel_author_link():
    html = '<a rel="author">Carl Sagan</a>'
    items = _run(html)
    assert "Carl Sagan" in _names(items)
    entry = next(it for it in items if it["name"] == "Carl Sagan")
    assert entry["context"] == "html:class"


def test_detects_byline_class():
    html = '<span class="byline">Dana Scully</span>'
    items = _run(html)
    assert "Dana Scully" in _names(items)


def test_detects_author_class_span():
    html = '<span class="author-name">Mary Johnson</span>'
    items = _run(html)
    assert "Mary Johnson" in _names(items)


# ---------------------------------------------------------------------------
# False positives
# ---------------------------------------------------------------------------


def test_ignores_organization_jsonld():
    # An Organization node (very common author shape) must not leak as a person.
    html = '<script type="application/ld+json">{"@type":"Organization","name":"Oteria"}</script>'
    assert _run(html) == []


def test_ignores_generic_author_placeholder():
    # "admin" is in the exclude list.
    html = '<meta name="author" content="admin">'
    assert _run(html) == []


def test_ignores_single_word_brand_in_byline():
    # A single short word (no space, <15 chars) is rejected even in author markup.
    html = '<span class="author">Microsoft</span>'
    assert _run(html) == []


def test_ignores_domain_derived_name():
    # The name equals the domain base ("example") -> rejected.
    html = '<meta name="author" content="example">'
    assert _run(html) == []


def test_ignores_bio_paragraph_blob():
    # A long bio sweeps in via the author class but fails the name-shape check.
    html = (
        '<div class="author-bio">Jane Doe is a senior engineer at WP. '
        'She has been writing for years.</div>'
    )
    assert _run(html) == []


def test_ignores_byline_with_title_suffix():
    # "by John Smith - SEO Expert" has too many tokens to read as a name.
    html = '<span class="byline">by John Smith - SEO Expert</span>'
    assert _run(html) == []


def test_ignores_random_capitalized_prose():
    # Plain prose with no author markup must yield nothing.
    html = '<p>The Quick Brown Fox Jumps</p>'
    assert _run(html) == []


def test_ignores_nav_text():
    # Navigation links are not author/byline markup.
    html = '<nav><a href="/about">About Us</a><a href="/team">Our Team</a></nav>'
    assert _run(html) == []
