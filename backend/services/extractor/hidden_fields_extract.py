# backend/services/extractor/hidden_fields_extract.py
"""Hidden form field extraction from HTML content."""
from __future__ import annotations

from selectolax.parser import HTMLParser

NOISE_NAMES: frozenset[str] = frozenset({
    "__VIEWSTATE",
    "__EVENTVALIDATION",
    "__REQUESTVERIFICATIONTOKEN",
    "__VIEWSTATEGENERATOR",
    "__PREVIOUSPAGE",
    "__EVENTARGUMENT",
    "__EVENTTARGET",
    "utf8",
    "__REQUESTDIGEST",
})


def extract_hidden_fields(html: str, tree: HTMLParser | None = None) -> list[dict]:
    """Parse HTML and return hidden input field name/value pairs.

    *tree* can be supplied by the orchestrator to avoid re-parsing the
    same HTML that was already parsed for the other per-category
    extractors. a big win on a 500-page scan.

    Filters:
    - Skips inputs with no name attribute
    - Skips empty values or values shorter than 3 characters
    - Skips known ASP.NET / Rails / SharePoint noise field names
    - Truncates values to 200 characters

    Returns a list of dicts with keys: name, value, form_action.
    """
    if tree is None:
        if not html:
            return []
        tree = HTMLParser(html)
    results: list[dict] = []

    for node in tree.css("input"):
        attrs = node.attributes
        input_type = (attrs.get("type") or "").strip().upper()
        if input_type != "HIDDEN":
            continue

        name = attrs.get("name")
        if not name:
            continue

        if name in NOISE_NAMES:
            continue

        value = attrs.get("value") or ""
        if len(value) < 3:
            continue

        # Walk up to find the nearest parent <form> and grab its action.
        form_action: str | None = None
        parent = node.parent
        while parent is not None:
            if parent.tag == "form":
                form_action = (parent.attributes or {}).get("action") or None
                break
            parent = parent.parent

        results.append({
            "name": name,
            "value": value[:200],
            "form_action": form_action,
        })

    return results
