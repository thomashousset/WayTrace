"""Source-page provenance: every value records the first page it appeared on,
so the UI can link to the snapshot and group co-occurring findings."""
from __future__ import annotations

from services.extractor import extract_all


def _pages(*specs):
    out = []
    for i, (html, url) in enumerate(specs):
        out.append({
            "html": html,
            "url": url,
            "timestamp": f"2022060{i+1}120000",
        })
    return out


def test_finding_gets_source_url_and_page_id():
    pages = _pages(("<p>contact: alice@acorp.io</p>", "https://acorp.io/"))
    emails = extract_all(pages, "acorp.io", categories=["emails"])["emails"]
    assert emails
    e = emails[0]
    assert e.get("source_url")
    assert "web.archive.org" in e["source_url"]
    assert isinstance(e.get("source_page_id"), int)


def test_two_values_same_page_share_source_page_id():
    html = "<p>alice@acorp.io and bob@acorp.io</p>"
    pages = _pages((html, "https://acorp.io/contact"))
    emails = extract_all(pages, "acorp.io", categories=["emails"])["emails"]
    ids = {e["source_page_id"] for e in emails}
    assert len(emails) == 2
    assert len(ids) == 1  # co-occurrence: same page -> same id


def test_values_on_different_pages_get_different_ids():
    pages = _pages(
        ("<p>alice@acorp.io</p>", "https://acorp.io/a"),
        ("<p>bob@acorp.io</p>", "https://acorp.io/b"),
    )
    emails = extract_all(pages, "acorp.io", categories=["emails"])["emails"]
    by_val = {e["value"]: e["source_page_id"] for e in emails}
    assert by_val["alice@acorp.io"] != by_val["bob@acorp.io"]


def test_first_page_wins_for_repeated_value():
    pages = _pages(
        ("<p>alice@acorp.io</p>", "https://acorp.io/first"),
        ("<p>alice@acorp.io again</p>", "https://acorp.io/second"),
    )
    emails = extract_all(pages, "acorp.io", categories=["emails"])["emails"]
    e = next(x for x in emails if x["value"] == "alice@acorp.io")
    assert "/first" in e["source_url"]


def test_source_url_prefers_page_supplied_value():
    pages = [{
        "html": "<p>alice@acorp.io</p>",
        "url": "https://acorp.io/",
        "timestamp": "20220601120000",
        "source_url": "https://web.archive.org/web/20220601120000/https://acorp.io/",
    }]
    emails = extract_all(pages, "acorp.io", categories=["emails"])["emails"]
    assert emails[0]["source_url"].endswith("https://acorp.io/")


def test_provenance_across_categories_same_page():
    html = "<p>alice@acorp.io</p><a href='https://twitter.com/acorp'>x</a>"
    pages = _pages((html, "https://acorp.io/"))
    res = extract_all(pages, "acorp.io")
    em = res["emails"][0]
    assert em.get("source_page_id") == 1
