"""A value is "removed" only when every page that carried it was archived
again later and read without it.

The report used to call every value whose last_seen came before the scan's
last month "disappeared". On the demo scan that was 34 values out of 46,
among them a security@ mailbox still on its page two months before the end:
its page had simply not been archived since. These tests pin the stricter
rule that replaced it, computed in finalize.resolve_presence.
"""
from services.extractor.finalize import extract_all


def _page(path: str, ts: str, body: str) -> dict:
    url = f"https://www.acme-atelier.fr{path}"
    return {"url": url, "timestamp": ts, "html": f"<html><body>{body}</body></html>",
            "source_url": f"https://web.archive.org/web/{ts}/{url}"}


def _email(result: dict, value: str) -> dict:
    return next(e for e in result["emails"] if e["value"] == value)


def test_value_dropped_from_a_later_capture_of_its_page_is_removed():
    r = extract_all([
        _page("/contact", "20150101120000", "jane.doe@acme-atelier.fr"),
        _page("/contact", "20180101120000", "nothing here"),
    ], "acme-atelier.fr")
    assert _email(r, "jane.doe@acme-atelier.fr")["presence"] == "removed"


def test_page_never_archived_again_makes_no_claim():
    r = extract_all([
        _page("/contact", "20150101120000", "jane.doe@acme-atelier.fr"),
        _page("/about", "20200101120000", "other text"),
    ], "acme-atelier.fr")
    assert "presence" not in _email(r, "jane.doe@acme-atelier.fr")


def test_value_still_on_its_last_capture_makes_no_claim():
    r = extract_all([
        _page("/contact", "20150101120000", "jane.doe@acme-atelier.fr"),
        _page("/contact", "20180101120000", "jane.doe@acme-atelier.fr"),
    ], "acme-atelier.fr")
    assert "presence" not in _email(r, "jane.doe@acme-atelier.fr")


def test_removed_needs_every_page_that_carried_it():
    # Dropped from /contact, but /team was never archived after carrying it.
    r = extract_all([
        _page("/contact", "20150101120000", "jane.doe@acme-atelier.fr"),
        _page("/team", "20160101120000", "jane.doe@acme-atelier.fr"),
        _page("/contact", "20180101120000", "nothing here"),
    ], "acme-atelier.fr")
    assert "presence" not in _email(r, "jane.doe@acme-atelier.fr")


def test_pages_arriving_out_of_order_are_judged_by_date():
    # The scraper hands pages over as they download, not by date.
    r = extract_all([
        _page("/contact", "20180101120000", "nothing here"),
        _page("/contact", "20150101120000", "jane.doe@acme-atelier.fr"),
    ], "acme-atelier.fr")
    assert _email(r, "jane.doe@acme-atelier.fr")["presence"] == "removed"


def test_a_page_that_failed_to_download_proves_nothing():
    failed = _page("/contact", "20180101120000", "")
    failed["html"] = None
    r = extract_all([
        _page("/contact", "20150101120000", "jane.doe@acme-atelier.fr"),
        failed,
    ], "acme-atelier.fr")
    assert "presence" not in _email(r, "jane.doe@acme-atelier.fr")


def test_bookkeeping_never_reaches_the_report():
    r = extract_all([
        _page("/contact", "20150101120000", "jane.doe@acme-atelier.fr"),
        _page("/contact", "20180101120000", "nothing here"),
    ], "acme-atelier.fr")
    for cat, items in r.items():
        if not isinstance(items, list):
            continue
        for it in items:
            assert "_seen_on" not in it and "_untracked" not in it, cat
