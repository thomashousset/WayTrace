"""A highlight describes a group of findings, and the report has to work out
which individual values it refers to.

It could not, because a highlight only named its category. The frontend fell
back to giving every finding in that category the first highlight's severity,
so a Google browser key the engine had deliberately separated as
public-by-design inherited LEAK from the Stripe secret key beside it. A badge
built on that reads "leak" next to a key that is meant to be published.

The fix is for a highlight to carry the values it covers when it only covers
some of them.
"""
from services.extractor.highlights import compute_highlights


def _by_cat(highlights):
    return {h["category"]: h for h in highlights}


def test_a_partial_highlight_names_the_values_it_covers():
    results = {
        "api_keys": [
            {"value": "sk_live_SECRET", "tier": "secret", "type": "Stripe",
             "first_seen": "2019-03", "last_seen": "2023-11", "occurrences": 6},
            {"value": "AIzaSyPUBLIC", "tier": "public", "type": "Google",
             "first_seen": "2016-03", "last_seen": "2020-11", "occurrences": 3},
        ],
    }
    cats = _by_cat(compute_highlights(results, "example.com"))

    leak = cats["api_keys"]
    assert leak["severity"] == "LEAK"
    assert leak["values"] == ["sk_live_SECRET"], (
        "the leak highlight covers only the secret key, so it must say so; "
        "without this the public key inherits LEAK"
    )

    pivot = cats["api_keys_public"]
    assert pivot["severity"] == "PIVOT"
    assert pivot["values"] == ["AIzaSyPUBLIC"]


def test_the_two_tiers_never_claim_the_same_value():
    results = {
        "api_keys": [
            {"value": "sk_live_A", "tier": "secret", "type": "Stripe"},
            {"value": "sk_live_B", "tier": "secret", "type": "Stripe"},
            {"value": "AIzaSy_C", "tier": "public", "type": "Google"},
        ],
    }
    cats = _by_cat(compute_highlights(results, "example.com"))
    secret = set(cats["api_keys"]["values"])
    public = set(cats["api_keys_public"]["values"])
    assert not (secret & public), "a value cannot be both secret and public-by-design"
    assert secret == {"sk_live_A", "sk_live_B"}
    assert public == {"AIzaSy_C"}


def test_a_whole_category_highlight_leaves_values_empty():
    """When a highlight really does describe everything in its category there is
    nothing to narrow, and the reader can safely apply it to the lot."""
    results = {"cloud_buckets": [{"value": "s3://a"}, {"value": "s3://b"}]}
    h = _by_cat(compute_highlights(results, "example.com"))["cloud_buckets"]
    assert h["severity"] == "LEAK"
    assert h.get("values") in (None, [], ["s3://a", "s3://b"])


def test_every_partial_highlight_names_its_values():
    """The audit that found this: a highlight whose detail line iterates a
    filtered list describes a subset, so it has to say which one. Sixteen did
    not, including the two sitemap highlights that carry different severities
    for different values in the same category.
    """
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "services" / "extractor" / "highlights.py"
    text = src.read_text(encoding="utf-8")
    offenders = []
    for m in re.finditer(r'_add\(\s*\n\s*"(\w+)",\s*"([a-z_]+)",\n(.*?)\n        \)', text, re.S):
        sev, cat, body = m.groups()
        iterated = re.findall(r"for \w+ in (\w+)[\[\)]", body)
        # Iterating something other than the category itself means a subset.
        if iterated and iterated[0] != cat and "values=" not in body:
            offenders.append(f"{sev} {cat} (over {iterated[0]})")
    assert not offenders, (
        "these describe part of a category without naming which part:\n  "
        + "\n  ".join(offenders)
    )


def test_two_severities_in_one_category_stay_separable():
    """sitemaps_and_robots emits a PIVOT for disclosures and a CONTEXT for the
    rest. Without values a reader has to pick one and apply it to both."""
    results = {
        "sitemaps_and_robots": [
            {"value": "/robots.txt", "disallowed": ["/admin"]},
            {"value": "/sitemap.xml"},
        ],
    }
    hs = [h for h in compute_highlights(results, "example.com")
          if h["category"] == "sitemaps_and_robots"]
    for h in hs:
        assert "values" in h, f"{h['severity']} highlight must name its values"
    claimed = [v for h in hs for v in h["values"]]
    assert len(claimed) == len(set(claimed)), "a value cannot be in two severities"
