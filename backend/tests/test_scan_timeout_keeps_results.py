"""End to end: a scan still downloading at its timeout ends completed and
partial, with what it fetched, instead of failed with nothing.

Real run_scan, real _scan_pipeline, real scrape_snapshots; only the network
is fake (the session from test_scrape_budget: "slow" URLs stall 30 s).
"""
import asyncio

import pytest
import pytest_asyncio

from config import settings
from db import get_job_by_url_id, init_db
from models import ScanConfig
from store import JobStore
from tests.test_scrape_budget import _fast_and_fake  # noqa: F401  (autouse fixture)


@pytest_asyncio.fixture
async def fresh_db(tmp_path):
    await init_db(str(tmp_path / "wt.db"))
    yield
    import db as _db
    _db._db_path = None


@pytest.mark.asyncio
async def test_a_timed_out_scan_keeps_its_pages(fresh_db, monkeypatch):
    import routers.scan as scan_module
    import store as store_module
    fresh = JobStore()
    monkeypatch.setattr(store_module, "store", fresh)
    monkeypatch.setattr(scan_module, "store", fresh)
    monkeypatch.setattr(settings, "scrape_budget_seconds", 0)       # the default
    monkeypatch.setattr(settings, "scan_timeout_seconds", 3)        # 0.6 s reserve

    html = '<html><body><a href="mailto:jean.dupont@ex.com">x</a></body></html>'

    class _Content:
        async def read(self, *a):
            return html.encode()
    from tests import test_scrape_budget as tsb
    monkeypatch.setattr(tsb._FakeContent, "read", _Content.read)

    selected = [{"timestamp": f"20{10 + i}0101000000", "url": f"http://ex.com/p{i}"} for i in range(4)]
    selected += [{"timestamp": "20200101000000", "url": f"http://slow/{i}"} for i in range(2)]
    res = await fresh.create_job("ex.com", "5.5.5.5")
    await asyncio.wait_for(
        scan_module.run_scan(job_id=res["job_id"], config=ScanConfig(), selected_snapshots=selected),
        timeout=20,
    )
    job = await get_job_by_url_id(res["url_id"])
    assert job["status"] == "completed", job.get("meta")
    meta = job["meta"]
    assert meta.get("pages_scraped") == 4
    assert [e["value"] for e in job["results"]["emails"]] == ["jean.dupont@ex.com"]
