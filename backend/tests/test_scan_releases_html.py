"""A scan lets go of each page's HTML once the page is extracted.

The scraper's result list holds every page until the scan ends. Each page used
to keep its full HTML in that list until the very end, only to be parsed once
more for the page index, so a 3000-page scan carried all of it in memory for
its whole run. Now the visible text is taken at extraction time and the HTML is
dropped; the index and the page counts must come out the same.
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


@pytest.mark.asyncio
async def test_html_is_dropped_after_extraction_and_index_keeps_text(fresh_db, monkeypatch):
    import store as store_module
    import routers.scan as scan_module
    from db import get_job_by_url_id, search_scan_pages

    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)

    pages = [
        {"timestamp": "20200101000000", "url": "http://ex.com/",
         "html": "<html><body><p>Bienvenue chez Zorglub</p>"
                 "<script>var hidden='scriptword'</script></body></html>", "error": None},
        {"timestamp": "20210101000000", "url": "http://ex.com/a",
         "html": "<html><body>Contactez Marcelline</body></html>", "error": None},
        {"timestamp": "20220101000000", "url": "http://ex.com/b", "html": None, "error": "http_404"},
    ]

    async def _fake_scrape(selected, job_id, on_page=None, stats=None, deadline=None):
        for p in pages:
            if on_page:
                await on_page(p)
        return pages
    monkeypatch.setattr(scan_module, "scrape_snapshots", _fake_scrape)

    res = await fresh.create_job("ex.com", "9.9.9.9")
    selected = [{"timestamp": p["timestamp"], "url": p["url"]} for p in pages]
    await scan_module.run_scan(res["job_id"], ScanConfig(), selected_snapshots=selected)

    # The fetched pages no longer hold their HTML; the failed one is untouched.
    assert pages[0]["html"] == "" and pages[1]["html"] == ""
    assert pages[2]["html"] is None

    persisted = await get_job_by_url_id(res["url_id"])
    assert persisted["status"] == "completed"
    assert persisted["meta"]["pages_scraped"] == 2
    assert persisted["meta"]["pages_failed"] == 1

    # The index still has the visible text of both pages, and not the script.
    assert [r["url"] for r in await search_scan_pages(res["url_id"], "zorglub")] == ["http://ex.com/"]
    assert [r["url"] for r in await search_scan_pages(res["url_id"], "marcelline")] == ["http://ex.com/a"]
    assert await search_scan_pages(res["url_id"], "scriptword") == []
