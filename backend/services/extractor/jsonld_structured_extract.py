"""JSON-LD structured-data extractor. writes to organisations, addresses, phones.

Unlike the per-category extractors, this walker fans its output across
multiple accumulator buckets because a single JSON-LD graph can describe
an organisation, its postal address, and its phone number together.
"""
from __future__ import annotations

import json
import re

from selectolax.parser import HTMLParser

from .helpers import canonicalize_phone_key, normalize_phone, phone_display, update_entity


# A "name" coming out of a JSON-LD Organization block must not be a URL.
# Some sites mis-populate the `name` slot with `@id` or the canonical URL
# (e.g. ``"name": "https://example.com/"``), and we want the human-
# readable label, not the URL.
_URL_SHAPED_NAME_RE = re.compile(r"^(?:https?:)?//|^www\.|/", re.IGNORECASE)


def _looks_like_url(name: str) -> bool:
    return bool(_URL_SHAPED_NAME_RE.search(name)) or " " not in name and "." in name and len(name) > 12 and name.lower().endswith((".com", ".org", ".net", ".io", ".fr", ".de", ".uk"))


def extract_jsonld_structured(
    tree: HTMLParser, raw_text: str, month: str, accum: dict, domain: str,
) -> None:
    """Iterate every ``<script type="application/ld+json">`` block."""
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except (json.JSONDecodeError, TypeError):
            continue
        walk_jsonld_structured(data, month, accum, domain)


# Schema.org @type values that carry organisation semantics. Kept as a
# curated frozenset rather than a tree-walk of subtypeOf because the
# JSON-LD payload itself never exposes that hierarchy. add new types here
# when a real-world site is observed using one (the test suite has a
# coverage check that surfaces unknown types via skip rather than fail).
_ORG_TYPES = frozenset({
    # generic
    "Organization", "LocalBusiness", "Corporation",
    # education
    "EducationalOrganization", "CollegeOrUniversity", "School",
    "ElementarySchool", "HighSchool", "MiddleSchool", "Preschool",
    # public sector / civic
    "GovernmentOrganization", "NGO", "PoliticalParty",
    # research / media
    "ResearchOrganization", "NewsMediaOrganization", "Library",
    # health
    "MedicalOrganization", "Hospital", "Pharmacy",
    # sport / arts
    "SportsOrganization", "SportsTeam", "MusicGroup", "PerformingGroup",
    "TheaterGroup", "DanceGroup",
    # business sub-types worth catching as orgs (LocalBusiness covers
    # most but Schema.org allows naming the leaf directly)
    "Restaurant", "Hotel", "Store", "ShoppingCenter",
})


def walk_jsonld_structured(data, month: str, accum: dict, domain: str) -> None:
    """Recursive walker. dispatches organisations, addresses, and phones."""
    if isinstance(data, dict):
        # @type is sometimes a list (e.g. ["Organization", "LocalBusiness"]).
        # Accept both the string form and any list element matching _ORG_TYPES.
        raw_type = data.get("@type", "")
        if isinstance(raw_type, list):
            schema_type = next((t for t in raw_type if t in _ORG_TYPES), "")
        else:
            schema_type = raw_type if raw_type in _ORG_TYPES else ""

        if schema_type:
            name = (data.get("name") or "").strip()
            # Reject URL-shaped values: some sites put their canonical URL
            # in `name` by mistake.
            if name and len(name) > 1 and not _looks_like_url(name):
                info = {"name": name, "type": schema_type}
                if data.get("url"):
                    info["url"] = data["url"]
                if data.get("logo"):
                    logo = data["logo"]
                    if isinstance(logo, str):
                        info["logo"] = logo
                    elif isinstance(logo, dict):
                        info["logo"] = logo.get("url", "")
                update_entity(accum.setdefault("organizations", {}), name.lower(), month, info)

        address = data.get("address")
        if isinstance(address, dict) and address.get("@type") == "PostalAddress":
            street = address.get("streetAddress", "")
            city = address.get("addressLocality", "")
            postal = address.get("postalCode", "")
            country = address.get("addressCountry", "")
            if street or city:
                addr_str = ", ".join(filter(None, [street, postal, city, country]))
                update_entity(
                    accum.setdefault("addresses", {}), addr_str.lower(), month,
                    {"street": street, "city": city, "postal_code": postal, "country": country},
                )

        phone = data.get("telephone", "")
        if phone and len(phone) >= 7:
            normalized = normalize_phone(phone)
            digits = re.sub(r"[^\d]", "", normalized)
            if 7 <= len(digits) <= 15:
                key = canonicalize_phone_key(digits)
                update_entity(
                    accum.setdefault("phones", {}), key, month,
                    {
                        "raw": phone, "normalized": normalized,
                        "value": phone_display(key, normalized),
                    },
                )

        for v in data.values():
            walk_jsonld_structured(v, month, accum, domain)
    elif isinstance(data, list):
        for item in data:
            walk_jsonld_structured(item, month, accum, domain)
