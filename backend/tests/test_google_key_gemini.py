"""A Google AIza key used for Gemini ranks LEAK again.

a303cc8 moved every AIza key to the public tier (PIVOT): a browser key for
Maps or Firebase is restricted by referrer and meant to ship. But the same
AIza shape is the Gemini API key, billed per call and never meant for a
page; next to the Gemini endpoint it is an exposure, as it was before.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.highlights import compute_highlights

DOMAIN = "example.com"
KEY = "AIzaSyD-" + "abcdefghijklmnopqrstuvwxyz01234"


def _key(html):
    res = extract_all([{"html": html, "url": f"https://{DOMAIN}/", "timestamp": "20250601120000"}], DOMAIN)
    hl = compute_highlights(res, DOMAIN)
    [k] = [k for k in res["api_keys"] if k["type"] == "Google_API"]
    leak = [h for h in hl if h.get("severity") == "LEAK" and h.get("category") == "api_keys"]
    return k["tier"], bool(leak)


@pytest.mark.parametrize("html", [
    f'<script>fetch("https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={KEY}")</script>',
    f'<script>const genAI = new GoogleGenerativeAI("{KEY}");</script>',
    f'<pre>GEMINI_API_KEY={KEY}</pre>',
])
def test_a_gemini_key_is_a_leak(html):
    assert _key(html) == ("secret", True)


@pytest.mark.parametrize("html", [
    f'<script src="https://maps.googleapis.com/maps/api/js?key={KEY}"></script>',
    f'<script>firebase.initializeApp({{apiKey: "{KEY}", authDomain: "x.firebaseapp.com"}});</script>',
])
def test_a_browser_key_stays_a_pivot(html):
    assert _key(html) == ("public", False)
