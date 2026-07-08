"""Integration test: Phase A new extraction categories."""
from services.extractor import extract_all


RICH_HTML = """
<html>
<head>
    <link rel="icon" href="/favicon.ico">
    <link rel="apple-touch-icon" href="/apple-touch-icon.png">
</head>
<body>
    <p>Donate BTC: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa</p>
    <p>ETH: 0x742d35CC6634c0532925a3B844bc9e7595F2Bd28</p>

    <a href="https://twitter.com/testhandle">Twitter</a>
    <a href="https://github.com/testorg">GitHub</a>
    <a href="https://discord.gg/testserver">Discord</a>
    <a href="https://partner-site.com/about">Partner</a>

    <link rel="stylesheet" href="https://cdn.shopify.com/s/files/theme.css">
    <script>/* WPServeur Tracker */</script>

    <script>ym(87654321, "init", {clickmap: true})</script>
</body>
</html>
"""


def test_crypto_addresses_extracted():
    pages = [{"html": RICH_HTML, "url": "http://example.com/", "timestamp": "20230615120000"}]
    results = extract_all(pages, "example.com")
    assert len(results["crypto_addresses"]) >= 2
    types = {c["type"] for c in results["crypto_addresses"]}
    assert "btc" in types
    assert "eth" in types


def test_favicons_extracted():
    pages = [{"html": RICH_HTML, "url": "http://example.com/", "timestamp": "20230615120000"}]
    results = extract_all(pages, "example.com")
    assert len(results["favicons"]) >= 2
    urls = {f["url"] for f in results["favicons"]}
    # Relative href is now resolved against the page URL. preserves the host.
    assert "http://example.com/favicon.ico" in urls


def test_outgoing_links_extracted():
    pages = [{"html": RICH_HTML, "url": "http://example.com/", "timestamp": "20230615120000"}]
    results = extract_all(pages, "example.com")
    # Social links are deduped into Social profiles; Outgoing links keeps only
    # non-social external domains (single source of truth per RETEX n2).
    cats = {o["category"] for o in results["outgoing_links"]}
    assert "social" not in cats
    assert "other" in cats
    assert any(o["domain"] == "partner-site.com" for o in results["outgoing_links"])
    # The social links (twitter/github/discord) now surface under Social profiles.
    social_services = {s["platform"] for s in results["social_profiles"]}
    assert {"twitter", "github", "discord"} <= social_services


def test_fbcom_routed_to_social_not_persons():
    # RETEX n2: fb.com is the facebook shortener; a fb.com profile URL must be
    # a Social profile, and a Facebook URL sitting in <meta author> must not
    # leak into Named persons.
    html = (
        '<html><head>'
        '<meta name="author" content="http://fb.com/john.doe">'
        '</head><body>'
        '<a href="http://fb.com/john.doe">Our page</a>'
        '<a href="https://www.pinterest.com/astroriahd/">Pinterest</a>'
        '</body></html>'
    )
    pages = [{"html": html, "url": "http://example.com/", "timestamp": "20230615120000"}]
    results = extract_all(pages, "example.com")
    social = {s["platform"] for s in results["social_profiles"]}
    assert "facebook" in social
    assert "pinterest" in social
    # No person value should be a URL, and the fb handle must not be a person.
    for p in results["persons"]:
        assert "/" not in p["name"] and "fb.com" not in p["name"].lower()
    # Social links are not duplicated in outgoing.
    assert not any(o["category"] == "social" for o in results["outgoing_links"])


def test_hosting_detected():
    pages = [{"html": RICH_HTML, "url": "http://example.com/", "timestamp": "20230615120000"}]
    results = extract_all(pages, "example.com")
    assert len(results["hosting"]) >= 1
    providers = {h["provider"] for h in results["hosting"]}
    assert "Shopify" in providers or "WPServeur" in providers


def test_yandex_metrica_detected():
    pages = [{"html": RICH_HTML, "url": "http://example.com/", "timestamp": "20230615120000"}]
    results = extract_all(pages, "example.com")
    trackers = {t["type"] for t in results["analytics_trackers"]}
    assert "Yandex_Metrica" in trackers


def test_existing_categories_unbroken():
    html = '<html><body><a href="mailto:admin@acme.io">Mail</a><p>+33 1 42 68 53 00</p></body></html>'
    pages = [{"html": html, "url": "http://acme.io/", "timestamp": "20230101120000"}]
    results = extract_all(pages, "acme.io")
    assert len(results["emails"]) >= 1
    assert len(results["phones"]) >= 1
