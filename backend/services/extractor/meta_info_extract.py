"""Meta/OpenGraph/Twitter/link info extractor.

All entries land in a single accumulator bucket (``meta_info``) keyed by
``{kind}:{normalized_content}``. See the docstring of ``extract_meta_info``
below for the kinds currently emitted.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from .helpers import update_entity


def extract_meta_info(tree: HTMLParser, month: str, accum: dict) -> None:
    """Extract site-level meta info and HTML head content.

    Covers (as prefixed keys in the meta_info accumulator):
      - site_name:     og:site_name
      - cdn:           og:image / twitter:image host
      - desc:          description / og:description / twitter:description
      - keywords:      keywords
      - og_title:      og:title (page-level)
      - og_type:       og:type
      - og_url:        og:url
      - twitter_card:  twitter:card
      - twitter_site:  twitter:site handle
      - twitter_creator: twitter:creator handle
      - author:        author
      - generator:     generator (very useful tech fingerprint)
      - viewport:      viewport
      - robots:        robots directives
      - title:         <title> tag text
      - canonical:     <link rel="canonical">
      - alternate:     <link rel="alternate"> href + hreflang
      - manifest:      <link rel="manifest"> href
      - base:          <base href>
    """
    for node in tree.css("meta[property], meta[name]"):
        prop = (node.attributes.get("property") or node.attributes.get("name") or "")
        content = (node.attributes.get("content") or "").strip()
        if not content or len(content) < 3:
            continue
        prop_lower = prop.lower()

        if prop_lower == "og:site_name":
            update_entity(accum["meta_info"], f"site_name:{content.lower()}", month,
                          {"property": prop, "content": content})
        elif prop_lower == "og:title":
            update_entity(accum["meta_info"], f"og_title:{content[:80].lower()}", month,
                          {"property": prop, "content": content[:300]})
        elif prop_lower == "og:type":
            update_entity(accum["meta_info"], f"og_type:{content.lower()}", month,
                          {"property": prop, "content": content[:120]})
        elif prop_lower == "og:url":
            update_entity(accum["meta_info"], f"og_url:{content.lower()}", month,
                          {"property": prop, "content": content[:300]})

        elif prop_lower in ("og:image", "twitter:image"):
            cdn_match = re.match(r"https?://([^/]+)", content)
            if cdn_match:
                cdn = cdn_match.group(1)
                update_entity(accum["meta_info"], f"cdn:{cdn.lower()}", month,
                              {"property": prop, "content": content[:300], "cdn_host": cdn})

        elif prop_lower in ("description", "og:description", "twitter:description"):
            short = content[:80].lower()
            update_entity(accum["meta_info"], f"desc:{short}", month,
                          {"property": prop, "content": content[:500]})

        elif prop_lower == "keywords":
            update_entity(accum["meta_info"], f"keywords:{content[:80].lower()}", month,
                          {"property": prop, "content": content[:500]})

        elif prop_lower == "twitter:card":
            update_entity(accum["meta_info"], f"twitter_card:{content.lower()}", month,
                          {"property": prop, "content": content[:80]})
        elif prop_lower == "twitter:site":
            update_entity(accum["meta_info"], f"twitter_site:{content.lower()}", month,
                          {"property": prop, "content": content[:80]})
        elif prop_lower == "twitter:creator":
            update_entity(accum["meta_info"], f"twitter_creator:{content.lower()}", month,
                          {"property": prop, "content": content[:80]})

        elif prop_lower == "author":
            update_entity(accum["meta_info"], f"author:{content[:80].lower()}", month,
                          {"property": prop, "content": content[:200]})
        elif prop_lower == "generator":
            update_entity(accum["meta_info"], f"generator:{content[:80].lower()}", month,
                          {"property": prop, "content": content[:200]})

        elif prop_lower == "viewport":
            update_entity(accum["meta_info"], f"viewport:{content[:80].lower()}", month,
                          {"property": prop, "content": content[:200]})
        elif prop_lower == "robots":
            update_entity(accum["meta_info"], f"robots:{content[:80].lower()}", month,
                          {"property": prop, "content": content[:200]})

    # The HTML <title> goes into its own bucket so investigators can read the
    # page-title history without the viewport/robots/og noise that clutters
    # meta_info (Boris feedback: titles deserve a dedicated tab).
    title_node = tree.css_first("head > title")
    if title_node is not None:
        title_text = (title_node.text() or "").strip()
        if title_text and len(title_text) >= 3:
            update_entity(accum["html_titles"], f"title:{title_text[:120].lower()}", month,
                          {"property": "title", "content": title_text[:300]})

    for node in tree.css("link[rel]"):
        rel = (node.attributes.get("rel") or "").strip().lower()
        href = (node.attributes.get("href") or "").strip()
        if not href or len(href) < 3:
            continue
        if rel == "canonical":
            update_entity(accum["meta_info"], f"canonical:{href.lower()}", month,
                          {"property": "link:canonical", "content": href[:500]})
        elif rel == "alternate":
            hreflang = (node.attributes.get("hreflang") or "").strip().lower()
            key = f"alternate:{hreflang}:{href.lower()}" if hreflang else f"alternate:{href.lower()}"
            update_entity(accum["meta_info"], key, month,
                          {"property": "link:alternate", "content": href[:500], "hreflang": hreflang})
        elif rel == "manifest":
            update_entity(accum["meta_info"], f"manifest:{href.lower()}", month,
                          {"property": "link:manifest", "content": href[:500]})

    base_node = tree.css_first("base[href]")
    if base_node is not None:
        base_href = (base_node.attributes.get("href") or "").strip()
        if base_href and len(base_href) >= 3:
            update_entity(accum["meta_info"], f"base:{base_href.lower()}", month,
                          {"property": "base", "content": base_href[:500]})
