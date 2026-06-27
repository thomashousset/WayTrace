"""Tests for meta_info extraction."""
from selectolax.parser import HTMLParser

from services.extractor.meta_info_extract import extract_meta_info


def _run(html: str) -> dict:
    accum = {"meta_info": {}, "html_titles": {}}
    extract_meta_info(HTMLParser(html), "2025-06", accum)
    return accum["meta_info"]


def _run_full(html: str) -> dict:
    """Return both buckets (meta_info + html_titles) since the HTML <title>
    now lives in its own category."""
    accum = {"meta_info": {}, "html_titles": {}}
    extract_meta_info(HTMLParser(html), "2025-06", accum)
    return accum


# --- Meta tags positive cases -----------------------------------------------

def test_og_title():
    res = _run('<meta property="og:title" content="Welcome to Example">')
    assert any(k.startswith("og_title:") for k in res)
    entry = next(v for k, v in res.items() if k.startswith("og_title:"))
    assert "Welcome to Example" in entry["content"]


def test_og_type():
    res = _run('<meta property="og:type" content="website">')
    assert any(k.startswith("og_type:") for k in res)


def test_og_url():
    res = _run('<meta property="og:url" content="https://example.com/page">')
    assert any(k.startswith("og_url:") for k in res)


def test_og_description_merges_with_description_bucket():
    res = _run('<meta property="og:description" content="A descriptive paragraph about the site">')
    # og:description falls into the shared 'desc:' bucket
    assert any(k.startswith("desc:") for k in res)


def test_twitter_description_merges_with_description_bucket():
    res = _run('<meta name="twitter:description" content="Tweet-sized site summary">')
    assert any(k.startswith("desc:") for k in res)


def test_twitter_card():
    res = _run('<meta name="twitter:card" content="summary_large_image">')
    assert "twitter_card:summary_large_image" in res


def test_twitter_site():
    res = _run('<meta name="twitter:site" content="@ExampleOrg">')
    assert "twitter_site:@exampleorg" in res


def test_twitter_creator():
    res = _run('<meta name="twitter:creator" content="@AuthorName">')
    assert "twitter_creator:@authorname" in res


def test_author():
    res = _run('<meta name="author" content="Jane Doe">')
    assert any(k.startswith("author:") for k in res)
    entry = next(v for k, v in res.items() if k.startswith("author:"))
    assert "Jane Doe" in entry["content"]


def test_generator_wordpress():
    res = _run('<meta name="generator" content="WordPress 6.4.2">')
    assert any(k.startswith("generator:") for k in res)
    entry = next(v for k, v in res.items() if k.startswith("generator:"))
    assert "WordPress" in entry["content"]


def test_generator_hugo():
    res = _run('<meta name="generator" content="Hugo 0.110.0">')
    assert any(k.startswith("generator:") for k in res)


def test_viewport():
    res = _run('<meta name="viewport" content="width=device-width, initial-scale=1">')
    assert any(k.startswith("viewport:") for k in res)


def test_robots_noindex():
    res = _run('<meta name="robots" content="noindex, nofollow">')
    assert any(k.startswith("robots:") for k in res)


# --- HTML head content positive cases ---------------------------------------

def test_title_tag():
    # The HTML <title> now lands in the dedicated html_titles bucket, not meta_info.
    acc = _run_full('<head><title>Example Corp. Home</title></head>')
    assert not any(k.startswith("title:") for k in acc["meta_info"])
    titles = acc["html_titles"]
    assert any(k.startswith("title:") for k in titles)
    entry = next(v for k, v in titles.items() if k.startswith("title:"))
    assert "Example Corp" in entry["content"]


def test_canonical_link():
    res = _run('<link rel="canonical" href="https://example.com/page">')
    assert any(k.startswith("canonical:") for k in res)


def test_alternate_link_with_hreflang():
    res = _run('<link rel="alternate" hreflang="fr" href="https://example.com/fr/">')
    keys = list(res.keys())
    assert any(k.startswith("alternate:fr:") for k in keys)


def test_alternate_link_without_hreflang():
    res = _run('<link rel="alternate" href="https://example.com/rss.xml">')
    keys = list(res.keys())
    # Key is "alternate:https://..." when no hreflang
    assert any(k.startswith("alternate:https://") for k in keys)


def test_manifest_link():
    res = _run('<link rel="manifest" href="/manifest.json">')
    assert any(k.startswith("manifest:") for k in res)


def test_base_href():
    res = _run('<base href="https://example.com/">')
    assert any(k.startswith("base:") for k in res)


# --- Existing cases still work ----------------------------------------------

def test_og_site_name_still_works():
    res = _run('<meta property="og:site_name" content="Example Inc">')
    assert any(k.startswith("site_name:") for k in res)


def test_description_still_works():
    res = _run('<meta name="description" content="A descriptive blurb about the site">')
    assert any(k.startswith("desc:") for k in res)


def test_keywords_still_works():
    res = _run('<meta name="keywords" content="osint, security, reconnaissance">')
    assert any(k.startswith("keywords:") for k in res)


def test_og_image_cdn_still_works():
    res = _run('<meta property="og:image" content="https://cdn.example.com/og.png">')
    assert any(k.startswith("cdn:") for k in res)


# --- Negative / false-positive rejection ------------------------------------

def test_empty_content_skipped():
    res = _run('<meta name="generator" content="">')
    assert res == {}


def test_short_content_skipped():
    res = _run('<meta name="author" content="X">')
    # 1 char is below the min length of 3
    assert not any(k.startswith("author:") for k in res)


def test_short_title_skipped():
    res = _run('<head><title>OK</title></head>')
    # 2 chars is below min length of 3
    assert not any(k.startswith("title:") for k in res)


def test_unknown_meta_name_skipped():
    res = _run('<meta name="completely-unknown-tag" content="some value">')
    assert res == {}


def test_canonical_without_href_skipped():
    res = _run('<link rel="canonical">')
    assert not any(k.startswith("canonical:") for k in res)


def test_non_meta_link_skipped():
    # 'stylesheet' rel is not one we track for meta_info
    res = _run('<link rel="stylesheet" href="/css/main.css">')
    assert not any(k.startswith(("canonical:", "alternate:", "manifest:", "base:")) for k in res)


def test_base_without_href_skipped():
    res = _run('<base>')
    assert not any(k.startswith("base:") for k in res)


def test_multiple_meta_all_captured():
    html = """
    <head>
      <title>Big Corp</title>
      <meta name="generator" content="Hugo 0.110">
      <meta name="author" content="Jane Doe">
      <meta name="viewport" content="width=device-width">
      <meta name="robots" content="index, follow">
      <meta property="og:title" content="Big Corp Home">
      <meta property="og:type" content="website">
      <meta name="twitter:card" content="summary">
      <link rel="canonical" href="https://bigcorp.com/">
    </head>
    """
    acc = _run_full(html)
    res = acc["meta_info"]
    expected_prefixes = [
        "generator:", "author:", "viewport:", "robots:",
        "og_title:", "og_type:", "twitter_card:summary", "canonical:",
    ]
    for prefix in expected_prefixes:
        assert any(k.startswith(prefix) for k in res), f"Missing prefix {prefix}"
    # The HTML <title> is now split out into its own bucket.
    assert any(k.startswith("title:") for k in acc["html_titles"])
