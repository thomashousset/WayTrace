"""A highlight that names no value makes its severity cover the whole category.

compute_highlights._add says it plainly: *values* must be given whenever a
highlight describes only some of its category, because a reader who cannot
tell which findings are meant has to apply the severity to all of them. That
is how a public-by-design key once ended up labelled a leak next to a secret
one.

Passing the wrong key defeats that just as thoroughly as passing nothing.
_add drops falsy entries, so `[item.get("value")]` over a category that
stores its value under "url" collapses to `[]`: the highlight still announces
"2 favicon change(s) detected" and still names none of them. Nothing raises,
nothing logs, and the report looks complete.

Every category below stores its canonical value under a key that is NOT
"value", so each of these fails if the highlight hardcodes "value".
"""
from services.extractor.highlights import compute_highlights

_SEEN = {"first_seen": "2001-03", "last_seen": "2002-06", "occurrences": 4}


def _values_for(results: dict, category: str) -> list:
    for h in compute_highlights(results, "example.com"):
        if h["category"] == category:
            return h.get("values", [])
    raise AssertionError(f"no highlight was produced for {category}")


def test_favicons_are_named_by_url() -> None:
    """favicon_extract writes {"url": ..., "type": ..., "sizes": ...}."""
    results = {"favicons": [
        {"url": "https://example.com/old.ico", "first_seen": "2001-03",
         "last_seen": "2002-06", "occurrences": 4},
    ]}
    assert _values_for(results, "favicons") == ["https://example.com/old.ico"]


def test_social_profiles_are_named_by_url() -> None:
    """This highlight files under "outreach", a grouping and not a category.

    item_value resolves by extractor category, so a display grouping has to
    say which category its items really come from or it names nothing.
    """
    results = {"social_profiles": [
        {"url": "https://twitter.com/example", "platform": "twitter", **_SEEN},
    ]}
    assert _values_for(results, "outreach") == ["https://twitter.com/example"]


def test_analytics_trackers_are_named_by_id() -> None:
    results = {"analytics_trackers": [
        {"id": "UA-12345-1", "type": "google_analytics", **_SEEN},
    ]}
    assert _values_for(results, "analytics_trackers") == ["UA-12345-1"]


def test_internal_ips_are_named_by_ip() -> None:
    results = {"internal_ips": [{"ip": "10.0.4.17", **_SEEN}]}
    assert _values_for(results, "internal_ips") == ["10.0.4.17"]


def test_jwt_tokens_are_named_by_token() -> None:
    results = {"jwt_tokens": [
        {"token": "eyJhbGciOiJIUzI1NiJ9.e30.sig", "alg": "HS256", **_SEEN},
    ]}
    assert _values_for(results, "jwt_tokens") == ["eyJhbGciOiJIUzI1NiJ9.e30.sig"]


def test_crypto_addresses_are_named_by_address() -> None:
    results = {"crypto_addresses": [
        {"address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "type": "bitcoin", **_SEEN},
    ]}
    assert _values_for(results, "crypto_addresses") == [
        "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    ]


def test_verification_tags_are_named_by_verification_id() -> None:
    results = {"verification_tags": [
        {"verification_id": "abc123def", "provider": "google", **_SEEN},
    ]}
    assert _values_for(results, "verification_tags") == ["abc123def"]


def test_js_urls_are_named_by_url() -> None:
    """Only the sensitive-looking ones are highlighted, hence the /api path."""
    results = {"js_urls": [
        {"url": "https://example.com/api/config.js", **_SEEN},
    ]}
    assert _values_for(results, "js_urls") == ["https://example.com/api/config.js"]


def test_directory_listings_are_named_by_path() -> None:
    results = {"directory_listings": [{"path": "/backup/", **_SEEN}]}
    assert _values_for(results, "directory_listings") == ["/backup/"]


def test_endpoints_are_named_by_path() -> None:
    results = {"endpoints": [{"path": "/admin/login", **_SEEN}]}
    assert _values_for(results, "endpoints") == ["/admin/login"]


def test_a_highlight_never_announces_a_count_it_cannot_name() -> None:
    """The property behind every case above, stated once.

    If a highlight carries a values list at all, it is because it covers a
    subset, and an empty list for a non-empty category is the silent failure
    this whole file exists to catch.
    """
    results = {
        "favicons": [{"url": "https://example.com/a.ico", "first_seen": "2001-03",
                      "last_seen": "2002-06", "occurrences": 2}],
        "social_profiles": [{"url": "https://t.me/example", "platform": "telegram", **_SEEN}],
        "jwt_tokens": [{"token": "eyJhbGciOiJIUzI1NiJ9.e30.s", "alg": "HS256", **_SEEN}],
        "internal_ips": [{"ip": "192.168.1.9", **_SEEN}],
        "analytics_trackers": [{"id": "GTM-ABC", "type": "gtm", **_SEEN}],
        "js_urls": [{"url": "https://example.com/admin/app.js", **_SEEN}],
    }
    empty = [
        h["title"] for h in compute_highlights(results, "example.com")
        if "values" in h and not h["values"]
    ]
    assert not empty, f"highlights naming nothing: {empty}"


def test_a_display_label_must_say_which_category_its_items_come_from() -> None:
    """item_value resolves by extractor category, so a grouping has to declare.

    Two _add labels are groupings and not categories: "outreach" collects
    social profiles and outgoing links, "api_keys_public" is the non-secret
    half of api_keys. Both resolve to item.get("value") by fallback if they
    stay silent, which is right for one of them today and by accident. A new
    grouping added without value_category would name nothing and no test
    would notice, so the rule is checked here rather than remembered.
    """
    import re
    from pathlib import Path

    from services.extractor.finalize import ALL_CATEGORIES

    src = (
        Path(__file__).resolve().parents[1]
        / "services" / "extractor" / "highlights.py"
    ).read_text(encoding="utf-8")

    offenders = []
    for m in re.finditer(r'_add\(\s*\n\s*"\w+",\s*"([a-z_]+)",(.*?)\n        \)', src, re.S):
        label, body = m.groups()
        if label in ALL_CATEGORIES or "items=" not in body:
            continue
        if "value_category=" not in body:
            offenders.append(label)
    assert not offenders, (
        "these labels are not extractor categories and do not say which "
        f"category their items come from: {offenders}"
    )
