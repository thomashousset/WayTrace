"""RFC 9116 security.txt, built per request from the operator's contact.

The two properties that matter are not "does it return 200". One is that an
instance with no contact configured publishes nothing, because a self-hosted
copy must never hand out the hosted operator's address, which is the mistake
the legal page shipped for months. The other is that Expires stays in the
future without anyone maintaining it: RFC 9116 makes the field mandatory and a
date in the past voids the file, so a literal would rot silently.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from config import settings

PATHS = ("/.well-known/security.txt", "/security.txt")


@pytest.fixture
def client():
    return TestClient(main.app)


# public_base_url only exists on the server build: the generated public tree
# strips it, so touching it unguarded turns every test in this file into a
# collection error there, which is how a public suite stops running at all.
_HAS_BASE_URL = hasattr(settings, "public_base_url")
_needs_base_url = pytest.mark.skipif(
    not _HAS_BASE_URL, reason="public_base_url is server-only")


@pytest.fixture(autouse=True)
def restore_settings():
    contact = settings.operator_contact
    base = settings.public_base_url if _HAS_BASE_URL else None
    yield
    settings.operator_contact = contact
    if _HAS_BASE_URL:
        settings.public_base_url = base


@pytest.mark.parametrize("path", PATHS)
def test_no_contact_configured_serves_nothing(client, path):
    """A self-hosted install has nowhere to route a report. Say so with a 404
    rather than publish the address of whoever this copy was built from."""
    settings.operator_contact = ""
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", PATHS)
def test_both_paths_serve_the_same_file(client, path):
    settings.operator_contact = "sec@example.org"
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "Contact: mailto:sec@example.org" in r.text


def test_a_bare_address_becomes_a_uri(client):
    """RFC 9116 requires Contact to be a URI, so a bare mailbox needs the
    scheme adding. Operators type an address, not a URI."""
    settings.operator_contact = "sec@example.org"
    assert "Contact: mailto:sec@example.org" in client.get(PATHS[0]).text


def test_a_contact_that_is_already_a_uri_is_left_alone(client):
    settings.operator_contact = "https://example.org/report"
    body = client.get(PATHS[0]).text
    assert "Contact: https://example.org/report" in body
    assert "mailto:https" not in body


def test_expires_is_mandatory_parseable_and_in_the_future(client):
    settings.operator_contact = "sec@example.org"
    line = next(l for l in client.get(PATHS[0]).text.splitlines()
                if l.startswith("Expires:"))
    when = datetime.strptime(line.split(" ", 1)[1].strip(),
                             "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert when > now
    # Under a year out, as RFC 9116 asks, and far enough that a scanner
    # revisiting next week still reads a valid file.
    assert (when - now).days < 365
    assert (when - now).days > 30


@_needs_base_url
def test_canonical_follows_the_configured_base_url(client):
    settings.operator_contact = "sec@example.org"
    settings.public_base_url = "https://example.org/"
    # Trailing slash stripped: a doubled slash in Canonical makes the URL fail
    # to match the one the file was fetched from, which is what Canonical is for.
    assert "Canonical: https://example.org/.well-known/security.txt" \
        in client.get(PATHS[0]).text


def test_no_base_url_means_no_canonical_line(client):
    """Canonical names where the file officially lives. Guessing it wrong is
    worse than omitting it, since a mismatch is what tells a reader the file
    was copied from somewhere else.

    Unset on the server build, absent entirely on the generated one: both have
    to reach the same answer, which is why the handler reads it with getattr
    rather than as an attribute."""
    settings.operator_contact = "sec@example.org"
    if _HAS_BASE_URL:
        settings.public_base_url = ""
    assert "Canonical:" not in client.get(PATHS[0]).text


def test_it_points_at_the_policy_that_is_actually_published(client):
    settings.operator_contact = "sec@example.org"
    assert "Policy: https://github.com/thomashousset/WayTrace/blob/main/SECURITY.md" \
        in client.get(PATHS[0]).text
