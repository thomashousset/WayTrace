"""The content search covers pages spread over the whole scan, and says when
it covers only a part.

Over the per-scan cap the index used to keep the first pages it was given,
and pages come in date order: on a 22-year history (framasoft.org, 1291
pages fetched) every indexed page was from the first ten months. Nothing in
the report said so, so a word missing from the results read as a word that
never appeared on the site.
"""
import pytest
import pytest_asyncio

from db import init_db
from models import ScanConfig
from store import JobStore


@pytest_asyncio.fixture
async def fresh_db(tmp_path):
    p = str(tmp_path / "wt.db")
    await init_db(p)
    yield p
    import db as _db
    _db._db_path = None


async def _run(monkeypatch, pages, cap):
    import store as store_module
    import routers.scan as scan_module
    from db import get_job_by_url_id

    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)
    monkeypatch.setattr(scan_module, "FTS_MAX_PAGES", cap)

    async def _fake_scrape(selected, job_id, on_page=None, stats=None, deadline=None):
        for p in pages:
            if on_page:
                await on_page(p)
        return pages
    monkeypatch.setattr(scan_module, "scrape_snapshots", _fake_scrape)

    res = await fresh.create_job("ex.com", "9.9.9.9")
    selected = [{"timestamp": p["timestamp"], "url": p["url"]} for p in pages]
    await scan_module.run_scan(res["job_id"], ScanConfig(), selected_snapshots=selected)
    return res["url_id"], (await get_job_by_url_id(res["url_id"]))["meta"]


def _page(year, word):
    return {"timestamp": f"{year}0101000000", "url": f"http://ex.com/{year}",
            "html": f"<html><body>common {word}</body></html>", "error": None}


@pytest.mark.asyncio
async def test_over_the_cap_indexed_pages_span_the_whole_period(fresh_db, monkeypatch):
    from db import search_scan_pages
    pages = [_page(y, f"word{y}") for y in range(2010, 2019)]   # 9 pages, date order
    url_id, meta = await _run(monkeypatch, pages, cap=3)

    years = sorted(r["timestamp"][:4] for r in await search_scan_pages(url_id, "common"))
    assert years == ["2010", "2014", "2018"]
    # The last year is searchable, not only the first ones.
    assert await search_scan_pages(url_id, "word2018")
    assert meta["pages_indexed"] == 3
    assert meta["search_partial"] is True


@pytest.mark.asyncio
async def test_pages_without_text_do_not_make_the_search_partial(fresh_db, monkeypatch):
    pages = [_page(2010, "a"), _page(2011, "b"),
             {"timestamp": "20120101000000", "url": "http://ex.com/frame",
              "html": "<html><body><script>x=1</script></body></html>", "error": None}]
    _, meta = await _run(monkeypatch, pages, cap=600)
    assert meta["pages_scraped"] == 3
    assert meta["pages_indexed"] == 2
    assert meta["search_partial"] is False


def test_coverage_of_a_new_scan_reads_what_the_scan_recorded():
    from routers.public import _search_coverage
    assert _search_coverage({"pages_scraped": 1291, "pages_indexed": 600, "search_partial": True}) \
        == {"pages_indexed": 600, "pages_scraped": 1291, "partial": True}
    assert _search_coverage({"pages_scraped": 3, "pages_indexed": 2, "search_partial": False})["partial"] is False


def test_coverage_of_a_scan_from_before_the_field():
    # Older scans indexed their first 600 pages and recorded nothing about it.
    from routers.public import _search_coverage
    assert _search_coverage({"pages_scraped": 1291}) \
        == {"pages_indexed": None, "pages_scraped": 1291, "partial": True}
    assert _search_coverage({"pages_scraped": 400})["partial"] is False
    assert _search_coverage({})["partial"] is False

