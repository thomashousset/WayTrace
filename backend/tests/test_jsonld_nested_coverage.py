"""Coverage proof: every OSINT pivot inside a wrapped JSON-LD type
(BlogPosting, VideoObject, Article, Event, ...) must still bubble up
into persons / organizations / addresses / phones.

This guards the decision to delete the legacy `og_twitter` category from
the database without losing pivot data. The historical og_twitter bucket
stored full JSON-LD blobs as raw text. that lookup style is gone, but
the recursive walkers in `persons_extract._walk_jsonld_authors` and
`jsonld_structured_extract.walk_jsonld_structured` should already lift
every actionable pivot out, regardless of the wrapper @type.

If a test here fails after a refactor, do not ship the og_twitter wipe
until coverage is restored.
"""
from __future__ import annotations

from selectolax.parser import HTMLParser

from services.extractor.persons_extract import extract_persons
from services.extractor.jsonld_structured_extract import extract_jsonld_structured


def _make_tree(jsonld_payload: str) -> HTMLParser:
    """Wrap a JSON-LD blob in a minimal HTML page selectolax can parse."""
    html = (
        '<html><head>'
        '<script type="application/ld+json">' + jsonld_payload + '</script>'
        '</head><body></body></html>'
    )
    return HTMLParser(html)


def _fresh_accum() -> dict:
    return {
        "persons": {}, "organizations": {}, "addresses": {}, "phones": {},
    }


# ---------------------------------------------------------------------------
# BlogPosting. the most common @type stored historically in og_twitter.
# Should yield: author -> persons, publisher -> organizations.
# ---------------------------------------------------------------------------

BLOG_POSTING = """
{
  "@context": "https://schema.org",
  "@type": "BlogPosting",
  "headline": "How we picked a CDN",
  "datePublished": "2024-03-12",
  "author": {
    "@type": "Person",
    "name": "Marie Dupont"
  },
  "publisher": {
    "@type": "Organization",
    "name": "Acme Labs",
    "url": "https://acme.example"
  }
}
"""


def test_blogposting_author_lands_in_persons():
    tree = _make_tree(BLOG_POSTING)
    accum = _fresh_accum()
    extract_persons(tree, "", "2024-03", accum, domain="acme.example")
    extract_jsonld_structured(tree, "", "2024-03", accum, "acme.example")
    names = [p["name"] for p in accum["persons"].values()]
    assert "Marie Dupont" in names, f"persons={names}"


def test_blogposting_publisher_lands_in_organizations():
    tree = _make_tree(BLOG_POSTING)
    accum = _fresh_accum()
    extract_jsonld_structured(tree, "", "2024-03", accum, "acme.example")
    org_names = [o["name"] for o in accum["organizations"].values()]
    assert "Acme Labs" in org_names, f"organizations={org_names}"


# ---------------------------------------------------------------------------
# VideoObject (also seen in oteria.fr og_twitter). uploader -> persons.
# ---------------------------------------------------------------------------

VIDEO_OBJECT = """
{
  "@context": "https://schema.org",
  "@type": "VideoObject",
  "name": "Q3 keynote",
  "description": "Live recording of the keynote",
  "uploadDate": "2023-09-01",
  "contentUrl": "https://cdn.example/q3.mp4",
  "author": {
    "@type": "Person",
    "name": "Pierre Lambert"
  }
}
"""


def test_videoobject_author_lands_in_persons():
    tree = _make_tree(VIDEO_OBJECT)
    accum = _fresh_accum()
    extract_persons(tree, "", "2023-09", accum, domain="example.com")
    names = [p["name"] for p in accum["persons"].values()]
    assert "Pierre Lambert" in names, f"persons={names}"


# ---------------------------------------------------------------------------
# Event with a nested PostalAddress and Organization. organizer -> orgs,
# location -> addresses. Proves the recursive walker reaches arbitrary
# depth.
# ---------------------------------------------------------------------------

EVENT_WITH_NESTED = """
{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "Cyber Conf 2024",
  "startDate": "2024-06-15",
  "organizer": {
    "@type": "Organization",
    "name": "Conf Org Ltd",
    "url": "https://conforg.example"
  },
  "location": {
    "@type": "Place",
    "name": "La Defense",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "12 rue de la Paix",
      "addressLocality": "Paris",
      "postalCode": "75002",
      "addressCountry": "FR"
    }
  }
}
"""


def test_event_organizer_lands_in_organizations():
    tree = _make_tree(EVENT_WITH_NESTED)
    accum = _fresh_accum()
    extract_jsonld_structured(tree, "", "2024-06", accum, "conforg.example")
    org_names = [o["name"] for o in accum["organizations"].values()]
    assert "Conf Org Ltd" in org_names, f"organizations={org_names}"


def test_event_nested_postaladdress_lands_in_addresses():
    tree = _make_tree(EVENT_WITH_NESTED)
    accum = _fresh_accum()
    extract_jsonld_structured(tree, "", "2024-06", accum, "conforg.example")
    addr_keys = list(accum["addresses"].keys())
    assert any("12 rue de la paix" in k for k in addr_keys), f"addresses={addr_keys}"
    assert any("75002" in k for k in addr_keys), f"addresses={addr_keys}"


# ---------------------------------------------------------------------------
# Bare string `author` field (legacy/short JSON-LD form). still routed.
# ---------------------------------------------------------------------------

BARE_AUTHOR_STRING = """
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "Quick note",
  "author": "Sophie Martin"
}
"""


def test_bare_string_author_lands_in_persons():
    tree = _make_tree(BARE_AUTHOR_STRING)
    accum = _fresh_accum()
    extract_persons(tree, "", "2024-01", accum, domain="example.com")
    names = [p["name"] for p in accum["persons"].values()]
    assert "Sophie Martin" in names, f"persons={names}"


# ---------------------------------------------------------------------------
# CollegeOrUniversity (oteria.fr's actual schema). Walker accepts it as
# Organization-equivalent.
# ---------------------------------------------------------------------------

COLLEGE_ORG = """
{
  "@context": "https://schema.org",
  "@type": "CollegeOrUniversity",
  "name": "Oteria Cyber School",
  "url": "https://www.oteria.fr/",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "1 av de la Republique",
    "addressLocality": "Boulogne",
    "postalCode": "92100",
    "addressCountry": "FR"
  },
  "telephone": "+33 1 84 60 02 22"
}
"""


def test_collegeoruniversity_yields_org_address_and_phone():
    tree = _make_tree(COLLEGE_ORG)
    accum = _fresh_accum()
    extract_jsonld_structured(tree, "", "2023-01", accum, "oteria.fr")

    # Note: walker matches on @type in {Organization, LocalBusiness,
    # Corporation, EducationalOrganization}. CollegeOrUniversity is a
    # subtype of EducationalOrganization in Schema.org, but the walker
    # checks string equality. If this assertion fails, the walker needs
    # to accept CollegeOrUniversity (a real-world @type seen on
    # oteria.fr).
    org_names = [o["name"] for o in accum["organizations"].values()]
    addr_keys = list(accum["addresses"].keys())
    phone_keys = list(accum["phones"].keys())

    # Address + phone must come through regardless of the org @type
    # (they are detected on their own field shape, not the parent type).
    assert any("1 av de la republique" in k for k in addr_keys), (
        f"address missing. accum.addresses={addr_keys}"
    )
    assert phone_keys, f"phone missing. accum.phones={phone_keys}"

    # Organisation must surface too. CollegeOrUniversity is a real-world
    # @type (oteria.fr uses it) and lives in the curated _ORG_TYPES set.
    assert "Oteria Cyber School" in org_names, f"organizations={org_names}"


# ---------------------------------------------------------------------------
# @type as a list (Schema.org allows ["Organization", "LocalBusiness"]).
# Walker should match if any list element is an org type.
# ---------------------------------------------------------------------------

ORG_TYPE_AS_LIST = """
{
  "@context": "https://schema.org",
  "@type": ["Organization", "LocalBusiness"],
  "name": "Multi Type Co"
}
"""


def test_org_type_as_list_lands_in_organizations():
    tree = _make_tree(ORG_TYPE_AS_LIST)
    accum = _fresh_accum()
    extract_jsonld_structured(tree, "", "2024-01", accum, "example.com")
    org_names = [o["name"] for o in accum["organizations"].values()]
    assert "Multi Type Co" in org_names, f"organizations={org_names}"
