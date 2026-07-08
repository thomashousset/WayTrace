"""Tests for the linked_documents extractor."""
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
    return extract_all(pages, "example.com")["linked_documents"]


def _exts(items: list[dict]) -> set[str]:
    return {it["extension"] for it in items}


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_detects_pdf_link():
    items = _run('<a href="/files/report.pdf">report</a>')
    assert any(it["url"] == "/files/report.pdf" and it["extension"] == "pdf" for it in items)


def test_detects_docx_link():
    items = _run('<a href="https://example.com/cv.docx">cv</a>')
    assert any(it["extension"] == "docx" for it in items)


def test_detects_xlsx_with_query_string():
    # Query string stripped from the dedup key / extension, but preserved in url.
    items = _run('<a href="/data/budget.xlsx?v=3&dl=1">budget</a>')
    entry = next(it for it in items if it["extension"] == "xlsx")
    assert entry["url"] == "/data/budget.xlsx?v=3&dl=1"


def test_detects_multiple_document_types():
    html = (
        '<a href="/a.csv">c</a>'
        '<a href="/b.pptx">p</a>'
        '<a href="/c.rtf">r</a>'
        '<a href="/d.odt">o</a>'
    )
    items = _run(html)
    assert {"csv", "pptx", "rtf", "odt"}.issubset(_exts(items))


def test_detects_uppercase_extension():
    # Regex is case-insensitive; extension is normalised to lowercase.
    items = _run('<a href="/SLIDES.PPT">slides</a>')
    assert any(it["extension"] == "ppt" for it in items)


def test_detects_ods_and_txt_and_xls():
    html = (
        '<a href="/sheet.ods">o</a>'
        '<a href="/notes.txt">n</a>'
        '<a href="/legacy.xls">x</a>'
        '<a href="/old.doc">d</a>'
    )
    items = _run(html)
    assert {"ods", "txt", "xls", "doc"}.issubset(_exts(items))


def test_dedup_same_url_different_query():
    # Same path with differing query strings collapses to one entry,
    # keeping the first href encountered.
    html = '<a href="/a.pdf?x=1">1</a><a href="/a.pdf?x=2">2</a>'
    items = _run(html)
    pdfs = [it for it in items if it["extension"] == "pdf"]
    assert len(pdfs) == 1
    assert pdfs[0]["url"] == "/a.pdf?x=1"


# ---------------------------------------------------------------------------
# False positives (must NOT be captured)
# ---------------------------------------------------------------------------


def test_ignores_html_link():
    assert _run('<a href="/about.html">about</a>') == []


def test_ignores_js_link():
    assert _run('<a href="/static/app.js">script</a>') == []


def test_ignores_image_link():
    assert _run('<a href="/img/logo.png">logo</a>') == []


def test_ignores_anchor_only():
    assert _run('<a href="#section">jump</a>') == []


def test_ignores_plain_page_url():
    assert _run('<a href="https://example.com/contact">contact</a>') == []


def test_ignores_extension_not_at_end():
    # Anchored to end of string: extension followed by a path segment is not a doc.
    assert _run('<a href="/files/report.pdf/view">view</a>') == []


def test_ignores_mailto_link():
    assert _run('<a href="mailto:info@example.com">mail</a>') == []


def test_ignores_zip_archive_link():
    # .zip is not in _DOC_EXTENSIONS, so archives are not treated as documents.
    assert _run('<a href="/bundle.zip">download</a>') == []


def test_query_value_ending_in_doc_ext_is_not_a_document():
    # A doc extension living in a query value (viewer/download proxy) is not a
    # linked document: the real path is /download, not a .docx file.
    assert _run('<a href="/download?file=report.docx">dl</a>') == []
    assert _run('<a href="/viewer?doc=annual.pdf">v</a>') == []


def test_document_with_fragment_anchor_is_captured():
    # /report.pdf#page=3 is a real PDF link; the fragment must not hide it.
    items = _run('<a href="/files/report.pdf#page=3">report</a>')
    assert len(items) == 1
    assert items[0]["extension"] == "pdf"


def test_epub_is_a_document():
    items = _run('<a href="/books/guide.epub">ebook</a>')
    assert len(items) == 1
    assert items[0]["extension"] == "epub"
