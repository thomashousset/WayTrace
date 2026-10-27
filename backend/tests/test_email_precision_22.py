"""Email precision fixes from the v2.2 audit.

The placeholder list was matched as substrings of the local part, so real
mailboxes vanished: j.hauser@ ("user"), lauren.testard@ and contestation@
("test"), sophie.yourcenar@ ("your"), anne.lesample@ ("sample"),
emailing@ ("email"). And two non-mailboxes came out as emails: an SSH git
remote and an error-tracker DSN key.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.helpers import is_email_excluded


@pytest.mark.parametrize("email", [
    "j.hauser@acme.fr", "lauren.testard@acme.fr", "sophie.yourcenar@acme.fr",
    "anne.lesample@acme.fr", "contestation@acme.fr", "attestation@acme.fr",
    "emailing@acme.fr", "userguide@acme.fr", "marc.dupont@acme.fr",
    "deadbeef@acme.fr",          # hex, but too short to be a key
])
def test_real_mailboxes_are_kept(email):
    assert not is_email_excluded(email)


@pytest.mark.parametrize("email", [
    "noreply@acme.fr", "no-reply@acme.fr", "no.reply@acme.fr", "noreply-billing@acme.fr",
    "test@acme.fr", "test123@acme.fr", "test.account@acme.fr", "user@acme.fr",
    "user1@acme.fr", "your.name@acme.fr", "yourname@acme.fr", "your-email@acme.fr",
    "youremail@acme.fr", "email@acme.fr", "sample@acme.fr", "placeholder@acme.fr",
    "changeme@acme.fr", "name@acme.fr", "example@acme.fr",
    "git@github.com", "git@gitlab.com",
    "5f3c1a9b2e4d4c7f8a9b0c1d2e3f4a5b@o12345.ingest.sentry.io",
])
def test_placeholders_and_lookalikes_are_dropped(email):
    assert is_email_excluded(email)


def test_end_to_end_on_a_page():
    html = """<p>Contact: lauren.testard@acme.fr, j.hauser@acme.fr</p>
    <pre>git clone git@github.com:acme/site.git</pre>
    <script>Sentry.init({dsn:"https://5f3c1a9b2e4d4c7f8a9b0c1d2e3f4a5b@o12345.ingest.sentry.io/42"})</script>"""
    res = extract_all([{"html": html, "url": "https://acme.fr/", "timestamp": "20220101000000"}], "acme.fr")
    assert sorted(e["value"] for e in res["emails"]) == ["j.hauser@acme.fr", "lauren.testard@acme.fr"]
