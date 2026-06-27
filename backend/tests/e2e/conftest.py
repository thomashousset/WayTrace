"""E2E (Playwright) harness.

These drive a real browser against a locally-launched server and need a
Playwright browser installed, so they are OPT-IN: the default `pytest tests/`
run skips them. Run them with:

    WT_E2E=1 python -m pytest tests/e2e -q

They only exercise offline pages (home, language toggle, legal) — no scan, so
they never touch archive.org.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WT_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="e2e: set WT_E2E=1 to run (needs a Playwright browser)")
    e2e_dir = str(Path(__file__).resolve().parent)
    for item in items:
        # Only skip the e2e specs themselves, never the rest of the suite.
        if str(getattr(item, "fspath", "")).startswith(e2e_dir):
            item.add_marker(skip)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": str(tmp_path_factory.mktemp("e2e") / "e2e.db"),
        "REQUIRE_ACCOUNT_TO_SCAN": "false",
        "CORS_ORIGINS": f"http://127.0.0.1:{port}",
        "LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(_BACKEND), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("e2e server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
