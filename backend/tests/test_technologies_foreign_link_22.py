"""A link to another site's WordPress does not make the target WordPress.

<a href="https://blog.other.com/wp-content/uploads/x.pdf"> tagged the target
as WordPress. Navigation links to another host no longer count; the
target's own links, its assets on a CDN host, and class names still do.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _techs(html):
    res = extract_all([{"html": html, "url": "https://acme.fr/", "timestamp": "20220101000000"}], "acme.fr")
    return {t["technology"] for t in res["technologies"]}


def test_a_link_to_another_sites_wordpress_is_not_the_target():
    assert "WordPress" not in _techs('<a href="https://blog.other.com/wp-content/uploads/x.pdf">PDF</a>')


@pytest.mark.parametrize("html", [
    '<a href="/wp-content/uploads/x.pdf">PDF</a>',
    '<a href="https://www.acme.fr/wp-content/uploads/x.pdf">PDF</a>',
    '<a href="https://blog.acme.fr/wp-content/x.pdf">PDF</a>',
    '<img src="https://i0.wp.com/acme.fr/wp-content/uploads/logo.png">',
    '<link rel="stylesheet" href="https://d1x.cloudfront.net/wp-content/themes/a/style.css">',
    '<div class="wp-content-wrapper">x</div>',
])
def test_the_targets_own_signals_still_count(html):
    assert "WordPress" in _techs(html)
