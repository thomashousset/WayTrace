"""Real mailboxes the placeholder rewrite dropped, and must keep.

Once separators were removed, exchange-members@ read as "exchangemembers"
and held "changeme"; bruno.replyer@ held "noreply". "name" became a whole
word to refuse, so domain-name@ and name.service@ went. And a 16-digit
local part read as an error-tracker key, which also took numeric mailboxes
(1000045678901234@facebook.com, fax gateways).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor.helpers import is_email_excluded


@pytest.mark.parametrize("email", [
    "exchange-members@acme.fr", "exchange.medical@acme.fr", "stock.exchange-medias@acme.fr",
    "xchange.mex@acme.fr", "bruno.replyer@acme.fr", "juno.reply@acme.fr",
    "nino.replyon@acme.fr", "tracy.ourname@acme.fr",
    "domain-name@acme.fr", "brand.name@acme.fr", "name.service@acme.fr",
    "1000045678901234@facebook.com", "0033142685300123@fax.acme.fr",
    "9f86d081884c7d65@sentry.acme.fr",
])
def test_real_mailboxes_are_kept(email):
    assert not is_email_excluded(email)


@pytest.mark.parametrize("email", [
    "noreply@acme.fr", "no.reply@acme.fr", "no.reply.handler@acme.fr",
    "contact-noreply@acme.fr", "mynoreply@acme.fr", "changeme@acme.fr",
    "change.me@acme.fr", "your-name@acme.fr", "name@acme.fr", "name2@acme.fr",
    "5f3c1a9b2e4d4c7f8a9b0c1d2e3f4a5b@o12345.ingest.sentry.io",
])
def test_placeholders_are_still_dropped(email):
    assert is_email_excluded(email)
