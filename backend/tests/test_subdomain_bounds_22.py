"""The text subdomain regex reads whole hosts, bounded on both sides.

Before: a.b.example.fr gave the made-up b.example.fr (one label only),
api.v2.example.fr gave v2.example.fr, and with no right boundary
shop.example.fr.attacker.net and blog.example.frites.com gave
shop.example.fr and blog.example.fr: hosts that do not exist, ranked PIVOT.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.extract import _get_subdomain_re

D = "example.fr"


def _subs(text):
    html = f"<html><body><p>{text}</p></body></html>"
    res = extract_all([{"html": html, "url": f"https://{D}/", "timestamp": "20220101000000"}], D)
    return {s["value"] for s in res["subdomains"]}


def test_a_deeper_host_is_read_whole():
    assert _subs("see a.b.example.fr") == {"a.b.example.fr"}
    assert _subs("api at api.v2.example.fr/v1") == {"api.v2.example.fr"}


def test_a_lookalike_suffix_is_not_the_target():
    assert _subs("go to shop.example.fr.attacker.net now") == set()
    assert _subs("blog.example.frites.com") == set()
    assert _subs("evilshop-example.fr") == set()


def test_ordinary_hosts_still_come_out():
    assert _subs("mail: jean@shop.example.fr, https://api.example.fr/x") == {"shop.example.fr", "api.example.fr"}
    assert _subs("end of sentence on cdn.example.fr.") == {"cdn.example.fr"}
    assert _subs("www.blog.example.fr") == {"blog.example.fr"}
    assert _subs("www.example.fr and example.fr") == set()


def test_url_encoded_tails_are_still_skipped():
    assert _subs("redirect=%2Fwww.example.fr") == set()


def test_stays_linear_on_long_dotted_runs():
    pat = _get_subdomain_re(D)
    for text in ("a." * 50000, ("a" * 60 + ".") * 2000, "-a" * 50000 + ".example.f"):
        t0 = time.perf_counter()
        list(pat.finditer(text))
        assert time.perf_counter() - t0 < 0.5, text[:20]
