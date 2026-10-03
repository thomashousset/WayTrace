"""State that must survive a rebuild lives beside the database, not in the image.

Both the CDX cache and the application log used to sit in the container's
ephemeral layer. Rebuilding the image threw away the cache, so the next scans
re-queried archive.org for indexes already held, and destroyed the log, which
is how the record of why twenty-three scans failed was lost. Under Docker the
database is on a volume, so anchoring both to its directory makes a deploy free
of that cost.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from config import settings


def test_cdx_cache_sits_beside_the_database(monkeypatch):
    from services import cdx
    monkeypatch.setattr(settings, "database_url", "/data/waytrace.db")
    monkeypatch.setattr(settings, "cdx_cache_dir", "")
    assert str(cdx._default_cache_dir()) == "/data/cdx-cache"


def test_cdx_cache_is_never_inside_the_code_tree(monkeypatch):
    """The regression this guards: /app/backend/data/cdx was inside the image."""
    from services import cdx
    monkeypatch.setattr(settings, "database_url", "/data/waytrace.db")
    monkeypatch.setattr(settings, "cdx_cache_dir", "")
    assert "/app/" not in str(cdx._default_cache_dir())


def test_cdx_cache_dir_can_be_overridden(monkeypatch, tmp_path):
    from services import cdx
    monkeypatch.setattr(settings, "cdx_cache_dir", str(tmp_path / "elsewhere"))
    assert cdx._default_cache_dir() == tmp_path / "elsewhere"


def test_log_file_sits_beside_the_database(monkeypatch, tmp_path):
    import main
    db = tmp_path / "waytrace.db"
    db.write_text("")
    monkeypatch.setattr(settings, "database_url", str(db))
    monkeypatch.setattr(settings, "log_dir", "")
    path = main._log_file_path()
    assert path == tmp_path / "logs" / "waytrace.log"
    assert path.parent.is_dir()   # created, not merely computed


def test_log_dir_can_be_overridden(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(settings, "log_dir", str(tmp_path / "custom"))
    assert main._log_file_path() == tmp_path / "custom" / "waytrace.log"


def test_unwritable_log_location_does_not_stop_the_app(monkeypatch):
    """A container that cannot write a log must still serve scans."""
    import main
    monkeypatch.setattr(settings, "log_dir", "/proc/definitely-not-writable/logs")
    assert main._log_file_path() is None


def test_logging_configures_without_raising(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(settings, "log_dir", str(tmp_path / "l"))
    main._configure_logging()          # must not raise
    from loguru import logger
    logger.info("persistence smoke test")
    written = list((tmp_path / "l").glob("waytrace.log"))
    assert written, "no log file was created"
    assert "persistence smoke test" in written[0].read_text(encoding="utf-8")
