"""Hosts the bounded subdomain regex lost, and must find again.

Bounding the text regex on both sides (test_subdomain_bounds_22.py) also
refused real hosts that the one-label regex used to find: a wildcard in a
CSP or a certificate (*.api.example.fr), a host glued to an ellipsis, a host
followed by a sentence that starts with no space, a CDN or S3 origin named
after the host, and a Windows share path, whose first label was cut by the
escape trim (\\\\fs01.ad.example.fr gave s01.ad.example.fr).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.extract import _get_subdomain_re

D = "example.fr"


def _subs(body, head=""):
    html = f"<html><head>{head}</head><body>{body}</body></html>"
    res = extract_all([{"html": html, "url": f"https://{D}/", "timestamp": "20220101000000"}], D)
    return {s["value"] for s in res["subdomains"]}


def test_a_wildcard_host_names_the_subdomain_under_it():
    csp = ('<meta http-equiv="Content-Security-Policy" content="img-src https://*.media.example.fr; '
           'connect-src https://*.api.example.fr">')
    assert _subs("x", csp) == {"media.example.fr", "api.example.fr"}
    assert _subs('<script>var allowed=["*.preprod.example.fr"];</script>') == {"preprod.example.fr"}
    assert _subs("<p>Certificat wildcard *.intra.example.fr</p>") == {"intra.example.fr"}


def test_a_host_glued_to_punctuation_is_found():
    assert _subs("<p>Accès VPN...vpn.example.fr (salariés)</p>") == {"vpn.example.fr"}
    assert _subs("<p>ici :extranet.example.fr.Pour toute question</p>") == {"extranet.example.fr"}
    assert _subs("<p>Hosts:\n-staging.example.fr</p>") == {"staging.example.fr"}


def test_a_cdn_or_bucket_origin_named_after_the_host_names_it():
    assert _subs('<script>var c="https://static.example.fr.edgesuite.net/l.png";</script>') == {"static.example.fr"}
    assert _subs("<p>Origin: origin-www.example.fr.cdn.cloudflare.net</p>") == {"origin-www.example.fr"}
    assert _subs('<img src="https://assets.example.fr.s3.amazonaws.com/l.png">') == {"assets.example.fr"}
    assert _subs("<p>https://files.example.fr.s3-website-eu-west-1.amazonaws.com/</p>") == {"files.example.fr"}


def test_a_windows_share_keeps_its_first_label():
    assert _subs("<p>Partage : \\\\fs01.ad.example.fr\\partage</p>") == {"fs01.ad.example.fr"}


def test_the_lookalikes_and_inner_starts_stay_refused():
    assert _subs("<p>go to shop.example.fr.attacker.net now</p>") == set()
    assert _subs("<p>shop.example.fr-secure.com</p>") == set()
    assert _subs("<p>see a.b.example.fr</p>") == {"a.b.example.fr"}
    assert _subs("<p>evil.example.fr.s3.amazonaws.com.attacker.net</p>") == set()


def test_the_origin_suffixes_stay_linear():
    pat = _get_subdomain_re(D)
    for text in ("x.example.fr.s3" + "-a" * 50000, "x.example.fr.s3" + ".a" * 50000 + ".amazonaws.co",
                 "*." * 50000 + "example.f", "." * 100000 + "x.example.f"):
        t0 = time.perf_counter()
        list(pat.finditer(text))
        assert time.perf_counter() - t0 < 0.5, text[:20]
