# backend/tests/test_connstring_extract.py
"""Tests for connection string extraction."""
from __future__ import annotations

import pytest

from services.extractor.connstring_extract import extract_connection_strings


# ---------------------------------------------------------------------------
# Positive tests (9)
# ---------------------------------------------------------------------------

def test_mysql_with_credentials_masked():
    html = 'DB_URL=mysql://admin:s3cr3t@db.example.com:3306/mydb'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "mysql"
    assert r["has_credentials"] is True
    assert "s3cr3t" not in r["value"]
    assert "****" in r["value"]


def test_postgresql_with_credentials():
    html = 'postgresql://user:pass123@pg.example.com:5432/production'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "postgresql"
    assert r["has_credentials"] is True
    assert "pass123" not in r["value"]
    assert "****" in r["value"]


def test_mongodb_srv_with_credentials():
    html = 'mongodb+srv://alice:hunter2@cluster0.mongodb.net/mydb?retryWrites=true'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "mongodb+srv"
    assert r["has_credentials"] is True
    assert "hunter2" not in r["value"]


def test_redis_password_only_format():
    html = 'REDIS_URL=redis://:mypassword@redis.example.com:6379/0'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "redis"
    assert r["has_credentials"] is True
    assert "mypassword" not in r["value"]
    assert "****" in r["value"]


def test_smtp_connection():
    html = 'SMTP=smtp://mailuser:mailpass@smtp.example.com:587'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "smtp"
    assert r["has_credentials"] is True


def test_ldap_connection():
    html = 'LDAP=ldap://cn=admin,dc=example,dc=com:secret@ldap.example.com/dc=org'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "ldap"


def test_no_credentials_has_credentials_false():
    html = 'cache: redis://redis.example.com:6379'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "redis"
    assert r["has_credentials"] is False


def test_multiple_connection_strings():
    html = (
        'DB=mysql://user:pass@db.host/mydb '
        'CACHE=redis://cache.host:6379'
    )
    results = extract_connection_strings(html)
    assert len(results) == 2
    types = {r["type"] for r in results}
    assert "mysql" in types
    assert "redis" in types


def test_dedup_same_url():
    url = 'postgres://user:secret@db.host:5432/db'
    html = f'{url} and again {url}'
    results = extract_connection_strings(html)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# False positive tests (4)
# ---------------------------------------------------------------------------

def test_skip_localhost_no_credentials():
    html = 'mysql://localhost:3306/testdb'
    results = extract_connection_strings(html)
    assert results == []


def test_skip_127_0_0_1_no_credentials():
    html = 'redis://127.0.0.1:6379'
    results = extract_connection_strings(html)
    assert results == []


def test_keep_localhost_with_credentials():
    html = 'mysql://root:password@localhost/proddb'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "mysql"
    assert r["has_credentials"] is True
    assert "password" not in r["value"]


def test_no_match_plain_text():
    html = 'We use MySQL and Redis for our infrastructure.'
    results = extract_connection_strings(html)
    assert results == []


# ---------------------------------------------------------------------------
# Additional drivers (added 2026-05): oracle, cassandra, neo4j, clickhouse,
# mariadb, cockroachdb, rediss (TLS).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scheme", [
    "oracle", "cassandra", "neo4j", "neo4j+s", "clickhouse",
    "mariadb", "cockroachdb", "rediss",
])
def test_additional_drivers_detected_and_masked(scheme):
    html = f'CONN={scheme}://svc:topsecret@host.example.com:1234/db'
    results = extract_connection_strings(html)
    assert len(results) == 1
    r = results[0]
    assert r["type"] == scheme
    assert r["has_credentials"] is True
    assert "topsecret" not in r["value"]
    assert "****" in r["value"]


def test_unknown_scheme_not_detected():
    # A scheme we don't track must not be picked up as a connection string.
    html = 'see https://example.com/redis://not-a-real-conn'
    results = extract_connection_strings(html)
    assert all(r["type"] not in ("https",) for r in results)
