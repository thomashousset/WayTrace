"""CMS signals the foreign-link rule lost, and must count again.

Dropping navigation links to other hosts (36991e9) also dropped the
target's own signals: the stock Joomla generator was never parsed ("Joomla!
- Open Source Content Management"), so a joomla.org "Powered by" link was
the only signal and it went; a "Propulsé par Drupal" footer went the same
way; and links that are the target's under another name (a Photon proxy
URL carrying the target's path, a host written with a trailing dot, the
target's own WP Engine or wordpress.com install) stopped counting.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _techs(html, head=""):
    page = f"<html><head>{head}</head><body>{html}</body></html>"
    res = extract_all([{"html": page, "url": "https://acme.fr/", "timestamp": "20220101000000"}], "acme.fr")
    return {t["technology"]: t.get("version") for t in res["technologies"]}


def test_the_stock_joomla_generator_names_joomla():
    assert "Joomla" in _techs("x", '<meta name="generator" content="Joomla! - Open Source Content Management">')
    assert _techs("x", '<meta name="generator" content="Joomla! 1.5 - Open Source Content Management">')["Joomla"] == "1.5"


@pytest.mark.parametrize("html, tech", [
    ('<div id="footer">Powered by <a href="https://www.joomla.org">Joomla!</a>. Valid XHTML.</div>', "Joomla"),
    ('<footer><span>Propulsé par <a href="https://www.drupal.org">Drupal</a></span></footer>', "Drupal"),
    ('<a href="https://i0.wp.com/acme.fr/wp-content/uploads/a.jpg"><img src="data:x"></a>', "WordPress"),
    ('<a href="https://acme.fr.:443/wp-content/uploads/a.pdf">A</a>', "WordPress"),
    ('<a href="https://acme.wpengine.com/wp-content/uploads/a.pdf">A</a>', "WordPress"),
])
def test_the_targets_own_links_count(html, tech):
    assert tech in _techs(html)


@pytest.mark.parametrize("html", [
    '<a href="https://blog.other.com/wp-content/uploads/x.pdf">PDF</a>',
    '<p>Annuaire : <a href="https://www.joomla.org">Joomla</a>, un CMS libre.</p>',
    '<li><a href="https://www.drupal.org/project/views">Views</a></li>',
    '<a href="https://i0.wp.com/other.com/wp-content/a.jpg">x</a>',
    '<a href="https://acmeinc.wpengine.com/wp-content/a.pdf">x</a>',
])
def test_other_sites_still_do_not_count(html):
    t = _techs(html)
    assert not ({"WordPress", "Joomla", "Drupal"} & set(t))
