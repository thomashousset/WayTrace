"""The compose files a self-hoster runs must honour their .env and stay local.

README says `cp .env.example .env` then `docker compose up -d`. .env is kept
out of the image by .dockerignore, and neither compose file passed it in, so
every setting a self-hoster wrote there (OPERATOR_CONTACT, the snapshot
ceiling, the rate knobs) was silently ignored inside the container. The
hot-reload file also published on 0.0.0.0, opening the scan endpoint to the
whole LAN, while the main file binds to loopback on purpose.

Read as text: PyYAML is not a dependency, and these are the two lines that
matter.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ["docker-compose.yml", "docker-compose.dev.yml"]


@pytest.mark.parametrize("name", FILES)
def test_compose_hands_over_the_dot_env(name):
    text = (ROOT / name).read_text()
    assert re.search(r"^\s*env_file:\s*\n\s*-\s*path:\s*\.env\s*\n\s*required:\s*false", text, re.M), name


@pytest.mark.parametrize("name", FILES)
def test_compose_publishes_on_loopback_only(name):
    text = (ROOT / name).read_text()
    ports = re.findall(r'^\s*-\s*"([^"]+:\d+)"', text, re.M)
    assert ports, name
    assert all(p.startswith("127.0.0.1:") for p in ports), (name, ports)


@pytest.mark.parametrize("name", FILES)
def test_the_database_stays_on_the_volume(name):
    # environment: wins over env_file, so a DATABASE_URL copied from a
    # bare-metal .env can never move the database off /data.
    assert "DATABASE_URL=/data/waytrace.db" in (ROOT / name).read_text()
