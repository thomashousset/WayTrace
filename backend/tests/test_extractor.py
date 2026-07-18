import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import (
    EMAIL_RE,
    PHONE_RE,
    TRACKER_PATTERNS,
    SOCIAL_PATTERNS,
    S3_RE,
    GCS_RE,
    AZURE_RE,
    DO_SPACES_RE,
    AWS_KEY_RE,
    GOOGLE_API_RE,
    STRIPE_RE,
    TWILIO_RE,
    SENDGRID_RE,
    SLACK_WEBHOOK_RE,
    GITHUB_TOKEN_RE,
    ALL_CATEGORIES,
    extract_all,
    finalize_accum,
    compute_highlights,
    _is_email_excluded,
    _strip_wayback_artifacts,
)


# ---------------------------------------------------------------------------
# Email regex
# ---------------------------------------------------------------------------


class TestEmailRegex:
    def test_valid_emails(self):
        valid = [
            "user@example.com",
            "john.doe@company.co.uk",
            "admin+tag@domain.org",
            "test-user@sub.domain.com",
            "info@startup.io",
        ]
        for email in valid:
            assert EMAIL_RE.search(email), f"Should match: {email}"

    def test_invalid_emails(self):
        """Strings that should not contain any full-address match."""
        invalid = [
            "not-an-email",
            "@domain.com",
            "user@",
            "user@.com",
        ]
        for text in invalid:
            assert EMAIL_RE.fullmatch(text) is None, (
                f"Unexpected full email match: {text}"
            )

    def test_excluded_emails(self):
        # company.com is now a placeholder domain. use a real-sounding
        # domain in the positive case.
        assert _is_email_excluded("noreply@realsite.io")
        assert _is_email_excluded("no-reply@realsite.io")
        assert _is_email_excluded("example@example.com")
        assert _is_email_excluded("icon@2x.png")
        # Placeholder domain matches regardless of local part
        assert _is_email_excluded("any@company.com")
        assert _is_email_excluded("any@yourdomain.com")
        assert not _is_email_excluded("admin@realsite.io")


class TestSecretPatternsExtended:
    """Cover the extended API_KEY_PATTERNS added after OSINT audit."""

    def test_aws_asia_temp_key_matched(self):
        from services.extractor.patterns import AWS_KEY_RE
        assert AWS_KEY_RE.search("token=ASIAIOSFODNN7EXAMPLE end")

    def test_aws_aroa_iam_role_id_matched(self):
        from services.extractor.patterns import AWS_KEY_RE
        # Real-world format: AROA + 16 alphanumerics = 20 chars.
        assert AWS_KEY_RE.search("AROA" + "BCDEFGHIJKLMNOPQ")

    def test_github_fine_grained_pat_matched(self):
        from services.extractor.patterns import GITHUB_TOKEN_RE
        token = "github_pat_" + "A" * 82
        assert GITHUB_TOKEN_RE.search(token)

    def test_github_classic_token_matched(self):
        from services.extractor.patterns import GITHUB_TOKEN_RE
        token = "ghp_" + "A" * 36
        assert GITHUB_TOKEN_RE.search(token)

    def test_stripe_restricted_key_matched(self):
        from services.extractor.patterns import STRIPE_RE
        assert STRIPE_RE.search("rk_live_" + "a" * 24)

    def test_slack_bot_token_matched(self):
        from services.extractor.patterns import SLACK_TOKEN_RE
        token = "xoxb-" + "1" * 12 + "-" + "2" * 13 + "-" + "A" * 16
        assert SLACK_TOKEN_RE.search(token)

    def test_google_oauth_client_id_matched(self):
        from services.extractor.patterns import GOOGLE_OAUTH_CLIENT_RE
        cid = "123456789012-" + "a" * 32 + ".apps.googleusercontent.com"
        assert GOOGLE_OAUTH_CLIENT_RE.search(cid)

    def test_twilio_account_sid_matched(self):
        from services.extractor.patterns import TWILIO_RE
        assert TWILIO_RE.search("AC" + "a" * 32)


class TestInternalIPExtended:
    """Link-local + CGNAT additions to INTERNAL_IP_RE."""

    def test_link_local_matched(self):
        from services.extractor.patterns import INTERNAL_IP_RE
        # AWS IMDS. the signal pentesters care about
        assert INTERNAL_IP_RE.search("http://169.254.169.254/latest/meta-data/")

    def test_cgnat_matched(self):
        from services.extractor.patterns import INTERNAL_IP_RE
        assert INTERNAL_IP_RE.search("host=100.64.1.5 port=443")

    def test_public_ip_not_matched(self):
        from services.extractor.patterns import INTERNAL_IP_RE
        assert not INTERNAL_IP_RE.search("visit 8.8.8.8 for DNS")


# ---------------------------------------------------------------------------
# Phone regex
# ---------------------------------------------------------------------------


class TestPhoneRegex:
    def test_valid_phones(self):
        valid = [
            "+33 1 42 68 53 00",
            "+1-202-555-0147",
            "(212) 555-1234",
            "+44 20 7946 0958",
            "01 42 68 53 00",
        ]
        for phone in valid:
            assert PHONE_RE.search(phone), f"Should match: {phone}"

    def test_short_numbers_rejected(self):
        short = ["123", "1234", "12-34", "555"]
        for num in short:
            match = PHONE_RE.search(num)
            if match:
                import re
                digits = re.sub(r"[^\d]", "", match.group())
                assert len(digits) < 8


# ---------------------------------------------------------------------------
# Tracker patterns
# ---------------------------------------------------------------------------


class TestTrackerPatterns:
    def test_ga_universal(self):
        assert TRACKER_PATTERNS["GA_Universal"].search("UA-12345678-1")
        assert TRACKER_PATTERNS["GA_Universal"].search("UA-1234-2")
        assert not TRACKER_PATTERNS["GA_Universal"].search("UA-12-1")

    def test_ga4(self):
        # Real GA4 IDs are always exactly 10 base36 chars after "G-".
        assert TRACKER_PATTERNS["GA4"].search("G-1JG9C4QQK8")
        assert TRACKER_PATTERNS["GA4"].search("G-ABCDEFGHIJ")
        assert not TRACKER_PATTERNS["GA4"].search("G-short")
        # 8 chars too short, 11 chars too long under the tightened bound.
        assert not TRACKER_PATTERNS["GA4"].search("G-12345678")
        assert not TRACKER_PATTERNS["GA4"].search("G-12345678901")

    def test_hotjar(self):
        # Hotjar site IDs are 5-8 digits; bound aligns with analytics_ids.
        assert TRACKER_PATTERNS["Hotjar"].search('hjid: "1234567"')
        assert not TRACKER_PATTERNS["Hotjar"].search('hjid:"1234"')

    def test_gtm(self):
        assert TRACKER_PATTERNS["GTM"].search("GTM-ABCDE")
        assert TRACKER_PATTERNS["GTM"].search("GTM-ABC12DE")
        assert not TRACKER_PATTERNS["GTM"].search("GTM-AB")

    def test_meta_pixel(self):
        assert TRACKER_PATTERNS["Meta_Pixel"].search(
            "fbq('init', '12345678901234')"
        )

    def test_google_ads(self):
        assert TRACKER_PATTERNS["Google_Ads"].search("AW-123456789")


# ---------------------------------------------------------------------------
# Social patterns
# ---------------------------------------------------------------------------


class TestSocialPatterns:
    def test_twitter(self):
        assert SOCIAL_PATTERNS["twitter"].search(
            "https://twitter.com/elonmusk"
        )
        assert not SOCIAL_PATTERNS["twitter"].search(
            "https://twitter.com/share"
        )
        assert not SOCIAL_PATTERNS["twitter"].search(
            "https://twitter.com/intent"
        )

    def test_linkedin(self):
        m = SOCIAL_PATTERNS["linkedin"].search(
            "https://linkedin.com/in/johndoe"
        )
        assert m and m.group(1) == "johndoe"

        m2 = SOCIAL_PATTERNS["linkedin"].search(
            "https://linkedin.com/company/acme-corp"
        )
        assert m2 and m2.group(1) == "acme-corp"

    def test_facebook_excludes_share(self):
        assert not SOCIAL_PATTERNS["facebook"].search(
            "https://facebook.com/sharer"
        )
        assert SOCIAL_PATTERNS["facebook"].search(
            "https://facebook.com/johndoe"
        )

    def test_telegram(self):
        m = SOCIAL_PATTERNS["telegram"].search("https://t.me/channelname")
        assert m and m.group(1) == "channelname"

    def test_github(self):
        m = SOCIAL_PATTERNS["github"].search("https://github.com/thomashousset")
        assert m and m.group(1) == "thomashousset"


# ---------------------------------------------------------------------------
# Wayback artifact stripping
# ---------------------------------------------------------------------------


class TestWaybackStripping:
    def test_strip_toolbar(self):
        html = """<html><body>
        <!-- BEGIN WAYBACK TOOLBAR INSERT -->
        <div id="wm-ipp">toolbar content</div>
        <!-- END WAYBACK TOOLBAR INSERT -->
        <h1>Real content</h1>
        </body></html>"""
        cleaned = _strip_wayback_artifacts(html)
        assert "WAYBACK TOOLBAR" not in cleaned
        assert "Real content" in cleaned

    def test_strip_wayback_scripts(self):
        html = '<script src="/_static/js/wm.js"></script><p>content</p>'
        cleaned = _strip_wayback_artifacts(html)
        assert "/_static/" not in cleaned
        assert "content" in cleaned


# ---------------------------------------------------------------------------
# Cloud bucket patterns
# ---------------------------------------------------------------------------


class TestCloudBuckets:
    def test_s3(self):
        assert S3_RE.search("mybucket.s3.amazonaws.com/file.txt")
        assert S3_RE.search("my-bucket.s3-us-east-1.amazonaws.com/file")

    def test_gcs(self):
        assert GCS_RE.search("storage.googleapis.com/my-bucket")

    def test_azure(self):
        assert AZURE_RE.search("myaccount.blob.core.windows.net/container")

    def test_s3_any_region(self):
        assert S3_RE.search("mybucket.s3.ap-northeast-1.amazonaws.com/file")
        assert S3_RE.search("mybucket.s3-eu-central-1.amazonaws.com/file")

    def test_do_spaces(self):
        assert DO_SPACES_RE.search("mybucket.nyc3.digitaloceanspaces.com/file.txt")


# ---------------------------------------------------------------------------
# API key patterns
# ---------------------------------------------------------------------------


class TestAPIKeys:
    def test_aws_key(self):
        assert AWS_KEY_RE.search("AKIAIOSFODNN7EXAMPLE")

    def test_google_api_key(self):
        assert GOOGLE_API_RE.search("AIza" + "a" * 35)

    def test_stripe_key(self):
        prefix_secret = "sk" + "_" + "test" + "_" + "X" * 24
        prefix_public = "pk" + "_" + "live" + "_" + "X" * 24
        assert STRIPE_RE.search(prefix_secret)
        assert STRIPE_RE.search(prefix_public)

    def test_twilio_key(self):
        assert TWILIO_RE.search("SK" + "a" * 32)

    def test_sendgrid_key(self):
        key = "SG." + "a" * 22 + "." + "b" * 43
        assert SENDGRID_RE.search(key)

    def test_slack_webhook(self):
        assert SLACK_WEBHOOK_RE.search(
            "hooks.slack.com/services/T0123ABCD/B0123ABCD/abc123XYZ456"
        )

    def test_github_token(self):
        assert GITHUB_TOKEN_RE.search("ghp_" + "A" * 36)
        assert GITHUB_TOKEN_RE.search("gho_" + "B" * 36)


# ---------------------------------------------------------------------------
# Technologies detection
# ---------------------------------------------------------------------------


class TestTechFromScripts:
    def test_jquery_detected(self):
        html = """<html><head>
        <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
        </head><body></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "jQuery" in techs

    def test_react_detected(self):
        html = """<html><head>
        <script src="/static/js/react.min.js"></script>
        </head><body></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "React" in techs

    def test_bootstrap_from_css(self):
        html = """<html><head>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css" rel="stylesheet">
        </head><body></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "Bootstrap" in techs

    def test_nextjs_detected(self):
        html = """<html><head>
        <script src="/_next/static/chunks/main.js"></script>
        </head><body></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "Next.js" in techs

    def test_svelte_detected_from_real_asset(self):
        html = """<html><head>
        <script src="https://cdn.jsdelivr.net/npm/svelte@4.2.0/internal/index.js"></script>
        <link href="/assets/svelte.min.css" rel="stylesheet">
        </head><body></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "Svelte" in techs

    def test_svelte_not_detected_from_blog_slug(self):
        # "svelte" buried in a marketing/blog slug or a longer asset name is
        # a topic mention, not evidence the site runs Svelte.
        html = """<html><head>
        <link href="/blog/why-we-left-svelte/style.css" rel="stylesheet">
        <script src="/assets/svelte-island-revolution-page.js"></script>
        </head><body></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "Svelte" not in techs


class TestCMSClassIndicatorScope:
    """CMS_CLASS_INDICATORS must scan DOM attribute values, never raw prose."""

    def test_drupal_prose_mention_does_not_tag(self):
        html = """<html><body><p>We migrated from Drupal to Hugo last year
        for performance reasons; Drupal had served us well.</p></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "Drupal" not in techs

    def test_joomla_blog_post_does_not_tag(self):
        html = """<html><body><article>Joomla and WordPress are both PHP CMSes.
        We picked neither.</article></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "Joomla" not in techs
        assert "WordPress" not in techs

    def test_drupal_class_attribute_tags(self):
        html = """<html><body class="drupal page-front"><div>real Drupal site</div></body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "Drupal" in techs

    def test_wordpress_wp_content_in_link_href_tags(self):
        html = """<html><head>
        <link rel="stylesheet" href="/wp-content/themes/twentytwentyfour/style.css">
        </head></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "WordPress" in techs

    def test_wordpress_wp_includes_in_script_src_tags(self):
        html = """<html><head>
        <script src="/wp-includes/js/jquery/jquery.min.js"></script>
        </head></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        techs = [t["technology"] for t in results["technologies"]]
        assert "WordPress" in techs


class TestPersonsLeafScope:
    """Person extraction must reject bio paragraphs and only emit name-shaped strings."""

    def test_real_byline_in_span_tags(self):
        html = '<article><span class="author-name">Jane Doe</span></article>'
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        assert any(p["name"] == "Jane Doe" for p in results["persons"])

    def test_a_rel_author_captured(self):
        html = '<article>By <a rel="author" href="/u/jane">Jane Doe</a></article>'
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        assert any(p["name"] == "Jane Doe" for p in results["persons"])

    def test_author_bio_div_not_captured_as_person(self):
        html = """<div class="author-bio">
        <h3>Jane Doe</h3>
        <p>Jane is a senior engineer at Example Corp who has been working on
        distributed systems for over fifteen years. She lives in Paris with
        her two cats and writes about distributed consensus algorithms.</p>
        </div>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        # The <div class="author-bio"> wrapper itself must NOT emit the
        # whole bio paragraph as a "person name". The leaf-scoped selectors
        # don't include div.
        names = [p["name"] for p in results["persons"]]
        assert not any("senior engineer" in n.lower() for n in names)
        assert not any("distributed" in n.lower() for n in names)

    def test_byline_with_role_suffix_rejected(self):
        # "Jane Doe - SEO Expert" is 5 tokens and includes non-name punctuation.
        html = '<span class="byline">Jane Doe - SEO Expert at Acme</span>'
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        names = [p["name"] for p in results["persons"]]
        # The hyphen surrounded by spaces is a 1-char "-" token that
        # _NAME_TOKEN_RE rejects, so this whole string is dropped.
        assert "Jane Doe - SEO Expert at Acme" not in names

    def test_too_many_words_rejected(self):
        html = '<span class="author">Jane Mary Anne Catherine Elizabeth Doe</span>'
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        names = [p["name"] for p in results["persons"]]
        # 6 tokens > 5-token cap.
        assert "Jane Mary Anne Catherine Elizabeth Doe" not in names

    def test_french_name_with_accents_captured(self):
        html = '<span class="author">Élise François</span>'
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        assert any(p["name"] == "Élise François" for p in results["persons"])


# ---------------------------------------------------------------------------
# Form actions merged into endpoints
# ---------------------------------------------------------------------------


class TestFormActionsInEndpoints:
    def test_internal_form_action_in_endpoints(self):
        html = """<html><body>
        <form action="/api/login" method="POST"><input type="text"></form>
        <form action="/submit" method="POST"><input type="text"></form>
        </body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        paths = [e["path"] for e in results["endpoints"]]
        assert "/api/login" in paths
        assert "/submit" in paths

    def test_external_form_action_not_in_endpoints(self):
        html = """<html><body>
        <form action="https://external.com/submit"><input type="text"></form>
        </body></html>"""
        pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "example.com")
        paths = [e["path"] for e in results["endpoints"]]
        assert "/submit" not in paths


# ---------------------------------------------------------------------------
# Integration: extract_all
# ---------------------------------------------------------------------------


def test_extract_all_basic():
    html = """
    <html>
    <head>
        <meta name="author" content="John Doe">
        <meta name="generator" content="WordPress 5.9">
    </head>
    <body>
        <a href="/about">About</a>
        <a href="https://twitter.com/testuser">Twitter</a>
        <p>Contact: admin@testcorp.io</p>
        <p>Phone: +33 1 42 68 53 00</p>
        <script>
            fbq('init', '12345678901234');
        </script>
    </body>
    </html>
    """
    pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "testcorp.io")

    assert any(e["path"] == "/about" for e in results["endpoints"])
    assert any(e["value"] == "admin@testcorp.io" for e in results["emails"])
    assert any(e["platform"] == "twitter" for e in results["social_profiles"])
    assert any(e["name"] == "John Doe" for e in results["persons"])
    assert any(e["technology"] == "WordPress" for e in results["technologies"])
    assert any(e["type"] == "Meta_Pixel" for e in results["analytics_trackers"])


def test_extract_all_empty_pages():
    pages = [{"html": None, "url": "https://example.com/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "example.com")
    assert results["emails"] == []
    assert results["endpoints"] == []
    assert results["cloud_buckets"] == []
    assert results["api_keys"] == []
    assert results["subdomains"] == []
    assert results["analytics_trackers"] == []
    assert results["social_profiles"] == []
    assert results["technologies"] == []
    assert results["persons"] == []
    assert results["phones"] == []


def test_extract_all_has_all_categories():
    """Verify extract_all returns all expected category keys."""
    pages = [{"html": "<html><body>test</body></html>", "url": "https://example.com/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "example.com")
    expected_keys = {
        "emails", "subdomains", "api_keys", "cloud_buckets",
        "analytics_trackers", "endpoints", "assets", "social_profiles",
        "technologies", "persons", "phones",
        "jwt_tokens", "directory_listings",
        "organizations", "addresses", "linked_documents",
        "html_comments", "meta_info",
        "hidden_fields", "internal_ips", "adsense_ids",
        "verification_tags", "iframe_sources", "js_urls",
        "connection_strings",
        "crypto_addresses", "favicons", "outgoing_links", "hosting",
        "http_headers", "french_business_ids",
        "analytics_ids", "cookie_consent", "rss_feeds",
        "github_repos", "sitemaps_and_robots", "pgp_keys",
        "bug_bounty_programs", "captcha_providers", "status_pages",
        "job_boards", "auth_providers", "html_titles",
    }
    assert set(results.keys()) == expected_keys


# ---------------------------------------------------------------------------
# compute_highlights
# ---------------------------------------------------------------------------


class TestComputeHighlights:
    def test_empty_results(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        highlights = compute_highlights(results, "example.com")
        assert highlights == []

    def test_api_keys_are_leak(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["api_keys"] = [
            {"type": "AWS", "value": "AKIAIOSFODNN7EXAMPLE", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "example.com")
        leak = [h for h in highlights if h["severity"] == "LEAK"]
        assert len(leak) == 1
        assert "API key" in leak[0]["title"]

    def test_cloud_buckets_are_leak(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["cloud_buckets"] = [
            {"value": "mybucket.s3.amazonaws.com", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "example.com")
        leak = [h for h in highlights if h["severity"] == "LEAK"]
        assert len(leak) == 1
        assert "bucket" in leak[0]["title"]

    def test_named_internal_emails_are_pivot(self):
        """Dotted/hyphenated internal mailboxes = PIVOT; gmail externe ignored."""
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["emails"] = [
            {"value": "jane.doe@example.com", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 3},
            {"value": "user@gmail.com", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "example.com")
        pivot = [h for h in highlights if h["severity"] == "PIVOT" and h["category"] == "emails"]
        assert len(pivot) == 1
        assert "1 named mailbox" in pivot[0]["title"]

    def test_generic_mailbox_is_not_pivot(self):
        """Public mailboxes like contact@/info@ don't qualify as PIVOT."""
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["emails"] = [
            {"value": "contact@example.com", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 5},
        ]
        highlights = compute_highlights(results, "example.com")
        pivot_emails = [h for h in highlights if h["severity"] == "PIVOT" and h["category"] == "emails"]
        assert pivot_emails == []

    def test_severity_ordering(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["api_keys"] = [
            {"type": "AWS", "value": "AKIAIOSFODNN7EXAMPLE", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        results["hosting"] = [
            {"provider": "Cloudflare", "signal": "meta/script", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        results["persons"] = [
            {"name": "John Doe", "context": "meta:author", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "example.com")
        severities = [h["severity"] for h in highlights]
        # LEAK must come before PIVOT which comes before CONTEXT
        assert severities.index("LEAK") < severities.index("PIVOT")
        assert severities.index("PIVOT") < severities.index("CONTEXT")

    def test_tech_change_is_pivot(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["technologies"] = [
            {"technology": "WordPress", "version": "5.9", "first_seen": "2020-01", "last_seen": "2023-06", "occurrences": 10},
        ]
        highlights = compute_highlights(results, "example.com")
        pivot = [h for h in highlights if h["severity"] == "PIVOT" and h["category"] == "technologies"]
        assert len(pivot) == 1
        assert "technology change" in pivot[0]["title"]

    def test_highlight_structure(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["subdomains"] = [
            {"value": "staging.example.com", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "example.com")
        assert len(highlights) >= 1
        h = highlights[0]
        assert "severity" in h
        assert "category" in h
        assert "title" in h
        assert "detail" in h
        assert "pivot_tip" in h

    def test_github_repos_are_pivot(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["github_repos"] = [
            {"owner": "acme", "repo": "widget",
             "pivot_url": "https://github.com/acme/widget",
             "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 2},
        ]
        highlights = compute_highlights(results, "example.com")
        pivot = [h for h in highlights if h["severity"] == "PIVOT" and h["category"] == "github_repos"]
        assert len(pivot) == 1
        assert "GitHub repo" in pivot[0]["title"]

    def test_pgp_keys_are_pivot(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["pgp_keys"] = [
            {"kind": "fingerprint", "identifier": "ABCDEF0123456789",
             "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "example.com")
        pivot = [h for h in highlights if h["severity"] == "PIVOT" and h["category"] == "pgp_keys"]
        assert len(pivot) == 1

    def test_security_txt_is_pivot_disclosure(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["sitemaps_and_robots"] = [
            {"url": "https://x.com/.well-known/security.txt", "kind": "security",
             "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
            {"url": "https://x.com/sitemap.xml", "kind": "sitemap",
             "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "x.com")
        pivot = [h for h in highlights if h["severity"] == "PIVOT" and h["category"] == "sitemaps_and_robots"]
        ctx = [h for h in highlights if h["severity"] == "CONTEXT" and h["category"] == "sitemaps_and_robots"]
        assert len(pivot) == 1, "security.txt should generate a PIVOT highlight"
        assert len(ctx) == 1, "sitemap.xml should generate a CONTEXT highlight"

    def test_cookie_consent_is_context(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["cookie_consent"] = [
            {"platform": "iubenda", "account_id": "1234567",
             "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "x.com")
        ctx = [h for h in highlights if h["severity"] == "CONTEXT" and h["category"] == "cookie_consent"]
        assert len(ctx) == 1
        assert "iubenda" in ctx[0]["title"]

    def test_rss_feeds_are_context(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["rss_feeds"] = [
            {"url": "https://x.com/feed/", "feed_type": "rss",
             "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
        ]
        highlights = compute_highlights(results, "x.com")
        ctx = [h for h in highlights if h["severity"] == "CONTEXT" and h["category"] == "rss_feeds"]
        assert len(ctx) == 1

    def test_trackers_are_pivot(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["analytics_trackers"] = [
            {"type": "GA_Universal", "id": "UA-12345678-1", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 5},
        ]
        highlights = compute_highlights(results, "example.com")
        tracker_hl = [h for h in highlights if h["category"] == "analytics_trackers"]
        assert len(tracker_hl) == 1
        assert tracker_hl[0]["severity"] == "PIVOT"
        assert "tracker" in tracker_hl[0]["title"]

    def test_sensitive_endpoints_are_pivot(self):
        results = {cat: [] for cat in ALL_CATEGORIES}
        results["endpoints"] = [
            {"path": "/api/users", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 3},
            {"path": "/admin/dashboard", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 1},
            {"path": "/about", "first_seen": "2022-01", "last_seen": "2022-06", "occurrences": 2},
        ]
        highlights = compute_highlights(results, "example.com")
        endpoint_hl = [h for h in highlights if h["category"] == "endpoints"]
        assert len(endpoint_hl) == 1
        assert endpoint_hl[0]["severity"] == "PIVOT"
        assert "admin" in endpoint_hl[0]["title"].lower() or "api" in endpoint_hl[0]["title"].lower()


# ---------------------------------------------------------------------------
# finalize_accum with categories filter
# ---------------------------------------------------------------------------


class TestFinalizeAccumCategories:
    def _make_accum_with_data(self):
        """Create an accumulator with some data in emails and phones."""
        accum = {cat: {} for cat in ALL_CATEGORIES}
        accum["emails"]["admin@test.com"] = {
            "first_seen": "2022-01",
            "last_seen": "2022-06",
            "occurrences": 3,
            "value": "admin@test.com",
        }
        accum["phones"]["+33142685300"] = {
            "first_seen": "2022-01",
            "last_seen": "2022-06",
            "occurrences": 1,
            "raw": "+33 1 42 68 53 00",
            "normalized": "+33142685300",
        }
        return accum

    def test_categories_none_returns_all(self):
        accum = self._make_accum_with_data()
        results = finalize_accum(accum, categories=None)
        assert len(results["emails"]) == 1
        assert len(results["phones"]) == 1

    def test_categories_filter_includes(self):
        accum = self._make_accum_with_data()
        results = finalize_accum(accum, categories=["emails"])
        assert len(results["emails"]) == 1
        assert results["phones"] == []

    def test_categories_filter_excludes_all(self):
        accum = self._make_accum_with_data()
        results = finalize_accum(accum, categories=["subdomains"])
        assert results["emails"] == []
        assert results["phones"] == []
        assert results["subdomains"] == []

    def test_categories_empty_list_returns_nothing(self):
        accum = self._make_accum_with_data()
        results = finalize_accum(accum, categories=[])
        for cat in ALL_CATEGORIES:
            assert results[cat] == []

    def test_extract_all_with_categories(self):
        html = """<html><body>
        <p>Contact: admin@testcorp.io</p>
        <p>Phone: +33 1 42 68 53 00</p>
        </body></html>"""
        pages = [{"html": html, "url": "https://testcorp.io/", "timestamp": "20220601120000"}]
        results = extract_all(pages, "testcorp.io", categories=["emails"])
        assert len(results["emails"]) >= 1
        assert results["phones"] == []


# ---------------------------------------------------------------------------
# Phone context-based filtering (guards against port ranges / RFC numbers)
# ---------------------------------------------------------------------------


def test_phone_context_keyword_accepts_dash_groups():
    """'Phone: 01 42 68 53 00' passes: keyword within 40 chars."""
    html = "<html><body>Phone: 01 42 68 53 00</body></html>"
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert len(results["phones"]) == 1


def test_phone_no_context_rejects_port_ranges():
    """'Valid ports: 1024-65535, 256-2047' must not become 'phones'."""
    html = "<html><body>Valid ports: 1024-65535, 256-2047</body></html>"
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert results["phones"] == []


def test_phone_international_prefix_bypasses_context():
    """A leading '+' is enough signal, no keyword needed."""
    html = "<html><body>Reach +33 1 42 68 53 00 today.</body></html>"
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert len(results["phones"]) == 1


def test_phone_paren_area_code_bypasses_context():
    """Parenthesised area codes also carry enough signal."""
    html = "<html><body>Reach us anytime: (212) 555-1234 today.</body></html>"
    pages = [{"html": html, "url": "https://x.com/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.com", categories=["phones"])
    assert len(results["phones"]) == 1


def test_phone_tel_link_always_counts():
    """<a href='tel:...'> is explicit intent. no regex / context filter."""
    html = '<html><body><a href="tel:+33142685300">Call</a></body></html>'
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert len(results["phones"]) == 1


# ---------------------------------------------------------------------------
# Phones. extended: data-attrs, JSON blobs, noise-tag stripping, FR shape
# ---------------------------------------------------------------------------


def test_phone_french_shape_bypasses_context():
    """A bare 10-digit FR number (0[1-79]########) has enough signal alone."""
    html = (
        "<html><body>Vous pouvez nous joindre au 01 76 40 32 26 "
        "pour toute question.</body></html>"
    )
    pages = [{"html": html, "url": "https://x.fr/contact",
              "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    digits = [p["normalized"] for p in results["phones"]]
    assert "0176403226" in digits


def test_phone_french_keyword_joindre():
    """'joindre' is a French signal word (added)."""
    html = ("<html><body>Pour nous joindre : 02 73 97 26 02"
            "</body></html>")
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert any(p["normalized"] == "0273972602" for p in results["phones"])


def test_phone_data_attribute_extracted():
    """<a data-phone='+33...'> click-to-call buttons are explicit intent."""
    html = (
        '<html><body>'
        '<a class="call-btn" data-phone="+33142685300">Call us</a>'
        '<span data-tel="0176403226">tel</span>'
        '</body></html>'
    )
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    digits = {p["normalized"] for p in results["phones"]}
    assert "+33142685300" in digits
    assert "0176403226" in digits


def test_phone_json_blob_telephone_key_extracted():
    """`"telephone":"…"` inside any inline <script>, not just JSON-LD."""
    html = (
        '<html><body><script>'
        'window.__INITIAL__ = {"contact":{"telephone":"+33 1 42 68 53 00",'
        '"email":"hi@x.fr"}};'
        '</script></body></html>'
    )
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert any(p["normalized"] == "+33142685300" for p in results["phones"])


def test_phone_json_blob_phone_key_variants():
    """`phone`, `phoneNumber`, `mobile` keys are all picked up."""
    html = (
        '<html><body><script>'
        'var data = {"phone":"0149019607","phoneNumber":"+33 6 51 67 37 16",'
        '"mobile":"0670548268"};'
        '</script></body></html>'
    )
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    digits = {p["normalized"] for p in results["phones"]}
    assert "0149019607" in digits
    assert "+33651673716" in digits
    assert "0670548268" in digits


def test_phone_svg_path_coords_rejected():
    """SVG <path d='M0 123.78 90.69 226'> coords must not become phones."""
    html = (
        '<html><body>'
        '<svg viewBox="0 0 512 512">'
        '<path d="M504 256C504 119 0 123.78 90.69 226.38 209.25"/>'
        '</svg>'
        'Other text without phone keywords.'
        '</body></html>'
    )
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert results["phones"] == []


def test_phone_css_rgba_decimal_rejected():
    """CSS rgba(255,255,255,0.14901960784313725) must not become a phone."""
    html = (
        '<html><body>'
        '<style>.x{box-shadow:0 4px 20px 0 rgba(255,255,255,0.14901960784313725)}</style>'
        'Other text without phone keywords.'
        '</body></html>'
    )
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert results["phones"] == []


def test_phone_unix_timestamp_in_js_rejected():
    """Sedo-parking-style timestamp blobs (e.g. '1457720482…') must be rejected."""
    html = (
        '<html><body><script>'
        "var fb_csa = '14577204827669601e3e70c896d4df9b5dac5e962c';"
        "var did = '202678691';"
        '</script></body></html>'
    )
    pages = [{"html": html, "url": "https://2600.eu/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "2600.eu", categories=["phones"])
    assert results["phones"] == []


def test_phone_version_number_rejected():
    """'Version 2.10.5.3' is a version, not a phone."""
    html = "<html><body>Version 2.10.5.3 released today.</body></html>"
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert results["phones"] == []


def test_phone_date_in_visible_text_rejected():
    """'08-11-2016 15:00' is a date, must not become a phone."""
    html = "<html><body>generated 08-11-2016 15:00</body></html>"
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    assert results["phones"] == []


def test_phone_french_premium_08_rejected_without_keyword():
    """0857… (08 premium) without any keyword must NOT pass FR validator."""
    html = "<html><body>Random ID 08 57 06 12 35 in a sentence.</body></html>"
    pages = [{"html": html, "url": "https://x.fr/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "x.fr", categories=["phones"])
    # No phone keyword nearby, no '+', no '('. and FR validator excludes 08 -
    # so this must not be extracted.
    assert results["phones"] == []


# ---------------------------------------------------------------------------
# Internal IP documentation-context filter
# ---------------------------------------------------------------------------


def test_internal_ip_documentation_context_filtered():
    """IPs inside RFC/registry prose must not be reported as leaks."""
    html = (
        "<html><body>"
        "10.0.0.0/8 reserved for Private-Use Networks per RFC1918."
        "</body></html>"
    )
    pages = [{"html": html, "url": "https://iana.org/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "iana.org", categories=["internal_ips"])
    assert results["internal_ips"] == []


def test_internal_ip_ops_context_still_extracted():
    """Same IP in an ops/log context is kept. the filter is narrow."""
    html = "<html><body>DB host: 10.0.0.5 (primary)</body></html>"
    pages = [{"html": html, "url": "https://corp.example/", "timestamp": "20220601120000"}]
    results = extract_all(pages, "example.com", categories=["internal_ips"])
    assert len(results["internal_ips"]) == 1
    assert results["internal_ips"][0]["ip"] == "10.0.0.5"


# ---------------------------------------------------------------------------
# Favicons
# ---------------------------------------------------------------------------


from services.extractor.favicon_extract import extract_favicons  # noqa: E402


class TestFaviconsPositive:
    def test_link_rel_icon(self):
        html = '<html><head><link rel="icon" href="/favicon.ico"></head></html>'
        out = extract_favicons(html, page_url="https://example.com/page")
        assert any(f["url"] == "https://example.com/favicon.ico"
                   and f["type"] == "favicon" for f in out)

    def test_apple_touch_icon_with_sizes(self):
        html = ('<link rel="apple-touch-icon" sizes="180x180" '
                'href="https://cdn.example.com/apple.png">')
        out = extract_favicons(html, page_url="https://example.com/")
        match = [f for f in out if f["type"] == "apple-touch-icon"]
        assert match and match[0]["sizes"] == "180x180"

    def test_mask_icon_safari_pinned(self):
        html = '<link rel="mask-icon" href="/safari.svg" color="#000">'
        out = extract_favicons(html, page_url="https://2600.eu/")
        assert any(f["type"] == "mask-icon"
                   and f["url"].endswith("/safari.svg") for f in out)

    def test_msapplication_tileimage(self):
        html = ('<meta name="msapplication-TileImage" '
                'content="https://www.2600.eu/wp-content/uploads/2024/09/'
                'cropped-logo-fonce-1-270x270.png">')
        out = extract_favicons(html, page_url="https://www.2600.eu/")
        assert any(f["type"] == "ms-tile-image" for f in out)

    def test_link_rel_manifest(self):
        html = '<link rel="manifest" href="/site.webmanifest">'
        out = extract_favicons(html, page_url="https://example.com/x")
        assert any(f["type"] == "manifest"
                   and f["url"] == "https://example.com/site.webmanifest"
                   for f in out)

    def test_alternate_icon_and_shortcut(self):
        html = ('<link rel="shortcut icon" href="/fav.ico">'
                '<link rel="alternate icon" href="/alt.png">')
        out = extract_favicons(html, page_url="https://example.com/")
        urls = {f["url"] for f in out}
        assert "https://example.com/fav.ico" in urls
        assert "https://example.com/alt.png" in urls

    def test_jsonld_logo(self):
        html = '''<script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Organization",
         "logo":"https://www.2600.eu/wp-content/uploads/logo-fonce.png"}
        </script>'''
        out = extract_favicons(html, page_url="https://www.2600.eu/")
        assert any(f["type"] == "logo:json-ld" for f in out)

    def test_og_image_with_logo_hint(self):
        html = ('<meta property="og:image" '
                'content="https://www.2600.eu/wp-content/uploads/'
                '2024/09/logo-fonce.png">')
        out = extract_favicons(html, page_url="https://www.2600.eu/")
        assert any(f["type"] == "logo:og-image" for f in out)

    def test_relative_url_resolved_against_base_href(self):
        html = ('<head><base href="https://cdn.example.com/assets/">'
                '<link rel="icon" href="favicon.png"></head>')
        out = extract_favicons(html, page_url="https://example.com/whatever")
        assert any(f["url"] == "https://cdn.example.com/assets/favicon.png"
                   for f in out)


class TestFaviconsNegative:
    def test_regular_img_ignored(self):
        html = '<img src="/photo.jpg" alt="hero">'
        out = extract_favicons(html, page_url="https://example.com/")
        assert out == []

    def test_stylesheet_link_ignored(self):
        html = '<link rel="stylesheet" href="/style.css">'
        out = extract_favicons(html, page_url="https://example.com/")
        assert out == []

    def test_wayback_artifact_skipped(self):
        html = ('<link rel="icon" '
                'href="https://web.archive.org/_static/img/wayback.png">')
        out = extract_favicons(html, page_url="https://example.com/")
        assert out == []

    def test_empty_href_ignored(self):
        html = '<link rel="icon" href="">'
        out = extract_favicons(html, page_url="https://example.com/")
        assert out == []

    def test_og_image_hero_photo_ignored(self):
        # Hero photo (a person), no logo/icon hint in the filename. skipped.
        html = ('<meta property="og:image" '
                'content="https://example.com/uploads/Charlie-Bromberg.png">')
        out = extract_favicons(html, page_url="https://example.com/")
        assert out == []

    def test_dns_prefetch_link_ignored(self):
        html = '<link rel="dns-prefetch" href="//cdn.example.com">'
        out = extract_favicons(html, page_url="https://example.com/")
        assert out == []

    def test_meta_unrelated_ignored(self):
        html = '<meta name="viewport" content="width=device-width">'
        out = extract_favicons(html, page_url="https://example.com/")
        assert out == []

    def test_dedup_exact_url(self):
        html = ('<link rel="icon" href="/favicon.ico">'
                '<link rel="shortcut icon" href="/favicon.ico">')
        out = extract_favicons(html, page_url="https://example.com/")
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Subdomains. DOM, JSON-LD, srcset, inline JS, CDX-URL mining
# ---------------------------------------------------------------------------


class TestSubdomainSources:
    """Coverage for the multi-source subdomain harvester.

    Five positive sources (anchor href, JSON-LD sameAs, srcset, inline JS
    string, CDX snapshot URL) and five false-positive families (apex,
    foreign-domain look-alike, asset filename containing the apex, case
    variants, trailing dot, IDN homograph).
    """

    @staticmethod
    def _values(results):
        return {e["value"] for e in results["subdomains"]}

    # --- Positive cases -----------------------------------------------------

    def test_subdomain_in_anchor_href(self):
        html = '<html><body><a href="https://api.acme.io/v1/health">api</a></body></html>'
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        assert "api.acme.io" in self._values(results)

    def test_subdomain_in_jsonld_sameas(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Organization",'
            '"sameAs":["https://blog.acme.io/feed","https://shop.acme.io/"]}'
            "</script></head><body></body></html>"
        )
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert "blog.acme.io" in vals
        assert "shop.acme.io" in vals

    def test_subdomain_in_srcset(self):
        html = (
            '<html><body><img srcset="https://cdn.acme.io/a.jpg 1x, '
            'https://cdn2.acme.io/a.jpg 2x"></body></html>'
        )
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert "cdn.acme.io" in vals
        assert "cdn2.acme.io" in vals

    def test_subdomain_in_inline_js_string(self):
        # Inline JS often holds API base URLs as string literals. the
        # raw-text branch must catch these.
        html = (
            '<html><body><script>'
            'var cfg = {"endpoint":"https://api.acme.io/v2","metrics":"https://stats.acme.io"};'
            '</script></body></html>'
        )
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert "api.acme.io" in vals
        assert "stats.acme.io" in vals

    def test_subdomain_in_cdx_snapshot_url(self):
        # The page itself never mentions admin.acme.io in its body, but
        # CDX archived a snapshot of admin.acme.io. Even if the scrape
        # failed (html=None) we must surface the host.
        pages = [
            {"html": "<html><body>nothing</body></html>",
             "url": "https://admin.acme.io/login", "timestamp": "20240101000000"},
            {"html": None, "url": "https://staging.acme.io/", "timestamp": "20240301000000"},
        ]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert "admin.acme.io" in vals
        assert "staging.acme.io" in vals

    def test_short_label_subdomains_not_truncated(self):
        # Regression: api/cdn/dev/fr labels MUST NOT trigger hex-prefix trim
        # in structured-source paths. they did in earlier iterations.
        html = (
            '<html><body>'
            '<a href="https://api.acme.io/x">api</a>'
            '<a href="https://cdn.acme.io/x">cdn</a>'
            '<a href="https://dev.acme.io/x">dev</a>'
            '<a href="https://fr.acme.io/x">fr</a>'
            '</body></html>'
        )
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        for sub in ("api.acme.io", "cdn.acme.io", "dev.acme.io", "fr.acme.io"):
            assert sub in vals, f"{sub} was lost"

    # --- Negative cases -----------------------------------------------------

    def test_apex_itself_never_recorded(self):
        html = (
            '<html><body>'
            '<a href="https://acme.io/">apex</a>'
            '<a href="https://www.acme.io/">www</a>'
            '</body></html>'
        )
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert "acme.io" not in vals
        assert "www.acme.io" not in vals

    def test_foreign_domain_lookalike_rejected(self):
        # Scanning bar.com. www.foo.com must never enter the bucket.
        html = (
            '<html><body>'
            '<a href="https://www.foo.com/">foo</a>'
            '<a href="https://api.foo.com/">api on foo</a>'
            '</body></html>'
        )
        pages = [{"html": html, "url": "https://bar.com/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "bar.com", categories=["subdomains"])
        assert results["subdomains"] == []

    def test_asset_filename_containing_apex_rejected(self):
        # ecole2600.com is a foreign domain. A CSS filename that merely
        # contains the apex string ("widget-...-ecole2600.css") must not
        # turn into a fake subdomain.
        html = (
            '<html><body>'
            '<link rel="stylesheet" href="/assets/widget-hero-ecole2600.css">'
            '<img src="/img/photo-ecole2600-logo.png">'
            '</body></html>'
        )
        pages = [{"html": html, "url": "https://2600.eu/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "2600.eu", categories=["subdomains"])
        assert results["subdomains"] == []

    def test_case_variants_collapsed(self):
        # MIXED case in any source must collapse to the lowercased host.
        html = (
            '<html><body>'
            '<a href="https://API.Acme.IO/x">api</a>'
            '<a href="https://api.acme.io/y">api lower</a>'
            '</body></html>'
        )
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert "api.acme.io" in vals
        # Only one canonical entry, not two.
        assert sum(1 for v in vals if v == "api.acme.io") == 1

    def test_trailing_dot_normalised(self):
        # Fully-qualified host with trailing dot must collapse to the
        # canonical form rather than emit "api.acme.io." as a distinct row.
        pages = [
            {"html": None, "url": "https://api.acme.io./", "timestamp": "20240101000000"},
            {"html": None, "url": "https://api.acme.io/", "timestamp": "20240201000000"},
        ]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert "api.acme.io." not in vals
        # Either gets recorded as the canonical form OR rejected. both
        # acceptable; we only forbid the dot-suffixed row.
        for v in vals:
            assert not v.endswith(".")

    def test_idn_homograph_rejected(self):
        # Raw unicode hostnames (Cyrillic 'а' that looks like Latin 'a')
        # must not slip through as a subdomain of the Latin apex.
        homo_host = "ap\u0456.acme.io"  # Cyrillic dotless i U+0456
        html = (
            f'<html><body><a href="https://{homo_host}/">x</a></body></html>'
        )
        pages = [{"html": html, "url": "https://acme.io/", "timestamp": "20240101000000"}]
        results = extract_all(pages, "acme.io", categories=["subdomains"])
        vals = self._values(results)
        assert homo_host not in vals
        # It's fine if the fullmatch ASCII guard rejects it entirely.
        for v in vals:
            assert all(c.isascii() for c in v)


# ---------------------------------------------------------------------------
# Crypto address extractor (BTC / ETH / LTC / DOGE / TRX / XMR / SOL / XRP)
# ---------------------------------------------------------------------------


class TestCryptoAddresses:
    """Validates the checksum-aware crypto extractor (no naive regex matches)."""

    def _addrs(self, html: str) -> list[dict]:
        from services.extractor.crypto_extract import extract_crypto_addresses
        return extract_crypto_addresses(html)

    # ------- positive cases (real, well-known addresses) -------

    def test_btc_p2pkh_genesis(self):
        # Satoshi's genesis block coinbase output (P2PKH)
        html = "<p>Donate BTC: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa thanks!</p>"
        out = self._addrs(html)
        assert any(a["address"] == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
                   and a["type"] == "btc"
                   and a["validation_method"] == "base58check"
                   for a in out)

    def test_btc_p2sh(self):
        html = "<p>Multisig: 3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy</p>"
        out = self._addrs(html)
        assert any(a["address"] == "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy" for a in out)

    def test_btc_bech32_v0(self):
        # BIP-173 P2WPKH test vector (uppercase form)
        html = "<p>SegWit BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4</p>"
        out = self._addrs(html)
        assert any(a["address"].lower() == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
                   and a["validation_method"] == "bech32"
                   for a in out)

    def test_btc_taproot_bech32m(self):
        # BIP-350 taproot test vector
        addr = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
        html = f"<p>Taproot tip: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr and a["type"] == "btc" for a in out)

    def test_eth_mixed_case_eip55(self):
        # Real EIP-55 test vector
        addr = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        html = f"<p>ETH: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr
                   and a["type"] == "eth"
                   and a["validation_method"] == "eip55"
                   for a in out)

    def test_eth_all_lowercase(self):
        addr = "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359"
        html = f"<p>ETH: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr and a["type"] == "eth" for a in out)

    def test_ltc_legacy(self):
        # Real Litecoin P2PKH
        addr = "LhK2kQwiaAvhjWY799cZvMyYwnQAcxkarr"
        html = f"<p>LTC: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr and a["type"] == "ltc" for a in out)

    def test_doge_legacy(self):
        # Real Dogecoin address
        addr = "DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L"
        html = f"<p>DOGE: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr and a["type"] == "doge" for a in out)

    def test_trx_address(self):
        # Tron foundation address
        addr = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        html = f"<p>TRX: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr and a["type"] == "trx" for a in out)

    def test_xrp_address(self):
        addr = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
        html = f"<p>XRP: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr and a["type"] == "xrp" for a in out)

    def test_sol_address_with_keyword(self):
        addr = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        html = f"<p>Solana wallet: {addr}</p>"
        out = self._addrs(html)
        assert any(a["address"] == addr and a["type"] == "sol" for a in out)

    # ------- false-positive cases (must NOT match) -------

    def test_md5_hash_rejected(self):
        # 32-char hex MD5 starting with '1'. naive regex would match BTC
        html = "<p>etag: 1c2aa3ac8a14cd42b8885b2341979faa</p>"
        out = self._addrs(html)
        assert out == []

    def test_uuid_rejected(self):
        html = "<p>id=550e8400-e29b-41d4-a716-446655440000</p>"
        out = self._addrs(html)
        assert out == []

    def test_github_commit_sha_rejected(self):
        html = "<p>commit f7d4c8e1a3b9f2e5d6c8a0b1e3d5f7c9a1b3d5f7</p>"
        out = self._addrs(html)
        assert out == []

    def test_random_base58_rejected(self):
        # Looks like a base58 string but has no valid Base58Check checksum
        html = "<p>nonce 3qAh8pGH9wXiZXBe4gbi2cPqdWTzE8yew x</p>"
        out = self._addrs(html)
        assert out == []

    def test_eth_zero_address_rejected(self):
        html = "<p>0x0000000000000000000000000000000000000000</p>"
        out = self._addrs(html)
        assert out == []

    def test_eth_dead_address_rejected(self):
        html = "<p>0xdeaddeaddeaddeaddeaddeaddeaddeaddeadbeef</p>"
        out = self._addrs(html)
        assert out == []

    def test_eth_bad_checksum_rejected(self):
        # Mixed case but one bit flipped from a valid EIP-55 address
        bad = "0x5AaEB6053F3E94C9b9A09f33669435E7Ef1BeAed"
        html = f"<p>{bad}</p>"
        out = self._addrs(html)
        assert out == []

    def test_addr_inside_script_rejected(self):
        html = (
            "<html><body><script>"
            "var addr='1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa';"
            "</script></body></html>"
        )
        out = self._addrs(html)
        assert out == []

    def test_addr_inside_style_rejected(self):
        html = (
            "<html><body><style>"
            ".foo { content: '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa'; }"
            "</style></body></html>"
        )
        out = self._addrs(html)
        assert out == []

    def test_addr_inside_data_attr_rejected(self):
        html = (
            '<html><body><div data-id="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa">x</div>'
            "</body></html>"
        )
        out = self._addrs(html)
        assert out == []

    def test_sol_without_keyword_rejected(self):
        addr = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        html = f"<p>random base58 token: {addr}</p>"
        out = self._addrs(html)
        # No 'solana'/'SOL'/'SPL' keyword nearby -> rejected
        assert all(a["type"] != "sol" for a in out)



# ---------------------------------------------------------------------------
# French business / training identifiers
# ---------------------------------------------------------------------------


class TestFrenchBusinessIds:
    """SIREN / SIRET (Luhn-validated), TVA, RCS, Qualiopi extractor."""

    def _ids(self, html: str) -> list[dict]:
        from services.extractor.french_business_ids_extract import extract_french_business_ids
        return extract_french_business_ids(html)

    # --- positives ---

    def test_siren_valid(self):
        # 552 100 554 = Renault SA (real INSEE entry, satisfies Luhn)
        out = self._ids("<p>SIREN : 552 100 554</p>")
        assert any(i["type"] == "siren" and i["value"] == "552100554" for i in out)

    def test_siret_valid(self):
        # SIRET = SIREN + 5-digit etablissement, Luhn over 14 digits.
        # 73282932000074 is the canonical Wikipedia example of a valid SIRET.
        out = self._ids("<p>SIRET 73282932000074</p>")
        assert any(i["type"] == "siret" and i["value"] == "73282932000074" for i in out)

    def test_qualiopi_with_subindex(self):
        out = self._ids("<a href='/Qualiopi_24FOR00922.2-_Certificat.pdf'>cert</a>")
        assert any(
            i["type"] == "qualiopi" and i["value"] == "24FOR00922.2" for i in out
        )

    def test_tva_fr(self):
        out = self._ids("<p>TVA intra. : FR40552100554</p>")
        assert any(i["type"] == "tva_fr" and i["value"] == "FR40552100554" for i in out)

    def test_rcs_with_city(self):
        out = self._ids("<p>RCS Paris B 552 100 554 capital social ...</p>")
        assert any(
            i["type"] == "rcs" and i["siren"] == "552100554" for i in out
        )

    def test_siret_does_not_double_emit_as_siren(self):
        # The SIRET above contains the SIREN as its prefix; we must not
        # emit both for the same digit run.
        out = self._ids("<p>SIRET 73282932000074</p>")
        sirens = [i for i in out if i["type"] == "siren"]
        # SIREN may still appear separately if the standalone form is in
        # the HTML, but the SIRET span itself must not yield a SIREN.
        assert all(i["value"] != "732829320" for i in sirens)

    # --- negatives ---

    def test_random_9_digit_run_rejected(self):
        # Phone number 0176403226 is 10 digits (not 9), but a stray 9-digit
        # JS timestamp like 123456789 fails Luhn -> rejected.
        out = self._ids("<p>id 123456789 something</p>")
        assert all(i["type"] != "siren" for i in out)

    def test_phone_number_not_siren(self):
        # 0176403226 = 10 digits, never matches SIREN_RE.
        out = self._ids("<p>Téléphone : 01 76 40 32 26</p>")
        assert all(i["type"] not in ("siren", "siret") for i in out)

    def test_qualiopi_rejects_unrelated_pattern(self):
        # Random "FOR" inside text without YYFORNNNNN shape is ignored.
        out = self._ids("<p>FORmation 2024 inscriptions ouvertes</p>")
        assert all(i["type"] != "qualiopi" for i in out)

    def test_invalid_luhn_siren_rejected(self):
        # 123456788 (off-by-one of any valid Luhn) should fail.
        out = self._ids("<p>SIREN 123 456 788</p>")
        assert all(i["value"] != "123456788" for i in out)

    def test_bare_luhn_passer_without_fr_context_rejected(self):
        # 335703880 satisfies Luhn but appears on wordpress.org pages with
        # zero French context. Without separators or FR keywords, drop it.
        out = self._ids("<p>tracking id 335703880 was assigned</p>")
        assert all(i["value"] != "335703880" for i in out)

    def test_bare_luhn_passer_with_fr_context_kept(self):
        # Same digits but with FR keyword nearby in the same document.
        out = self._ids("<p>SIREN 335703880 - société française</p>")
        assert any(i["type"] == "siren" and i["value"] == "335703880" for i in out)

    def test_separated_luhn_passer_without_keyword_kept(self):
        # French presentation form (with spaces) carries enough signal alone.
        out = self._ids("<p>335 703 880 immatriculé en 2002</p>")
        assert any(i["type"] == "siren" and i["value"] == "335703880" for i in out)

    def test_bare_siret_without_fr_context_rejected(self):
        # 14-digit Luhn-passer with no FR signal is coincidence on US sites.
        # Use a real but contextless valid SIRET (Wikipedia's example).
        out = self._ids("<p>order_id=73282932000074 status=ok</p>")
        assert all(i["type"] != "siret" for i in out)

    def test_siren_inconsistent_separator_rejected(self):
        """Floats like '867.852832' (single dot at position 3, no second
        separator) must NOT match. stripe.com archived blog emitted 668
        such ghost SIRENs before the backreference fix."""
        out = self._ids("<p>SIREN ratio = 867.852832, value = 108.356668</p>")
        assert all(i["type"] != "siren" for i in out)

    def test_siret_inconsistent_separator_rejected(self):
        # 14-digit float-like with one dot in middle.
        out = self._ids("<p>SIRET amount=867.85283200000074</p>")
        assert all(i["type"] != "siret" for i in out)

    def test_siren_dot_separated_three_groups_kept(self):
        """Real French formatting with two dots (552.100.554) stays valid."""
        out = self._ids("<p>SIREN : 552.100.554</p>")
        assert any(i["type"] == "siren" and i["value"] == "552100554" for i in out)

    def test_siren_doc_wide_keyword_far_from_digits_rejected(self):
        """Multilingual sites (stripe.com /fr-fr/* docs) had a 'siret'
        keyword in instructional text and unrelated 9-digit IDs further
        down the same page. Document-wide presence is no longer enough;
        the keyword must sit within the 60-char window before the digits."""
        far_html = (
            "<p>Pour s'inscrire vous devrez fournir votre numéro de SIRET.</p>"
            + "<div>" + "x" * 500 + "</div>"
            + "<p>Transaction id 444138432 archived for ref</p>"
        )
        out = self._ids(far_html)
        # 444138432 is Luhn-valid, but the only FR keyword is 500+ chars
        # away so the bare run no longer qualifies.
        assert all(i["value"] != "444138432" for i in out if i["type"] == "siren")

    def test_siren_keyword_within_window_kept(self):
        out = self._ids("<p>SIREN 444138432 declared 2009</p>")
        assert any(i["value"] == "444138432" for i in out if i["type"] == "siren")
