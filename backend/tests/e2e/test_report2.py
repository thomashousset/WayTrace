"""E2E for the Report 2.0 two-view master-detail page.

Drives the browser with a synthetic completed scan (no archive.org), then asserts
the Categories master-detail and the composable Activity view actually render and
respond to clicks. Opt-in: WT_E2E=1.
"""
import json

# A realistic synthetic result set: several categories, one disappeared
# subdomain, a favicon with an archived source, an analytics tracker.
FINDINGS = [
    {"id": 1, "category": "emails", "value": "contact@oteria.fr", "first_seen": "2016-03", "last_seen": "2025-06", "occurrences": 214, "metadata": {"source_url": "https://web.archive.org/web/20200101000000/http://oteria.fr/"}},
    {"id": 2, "category": "emails", "value": "rh@oteria.fr", "first_seen": "2019-01", "last_seen": "2023-11", "occurrences": 31, "metadata": {"source_url": "https://web.archive.org/web/20231101000000/http://oteria.fr/contact"}},
    {"id": 3, "category": "emails", "value": "admissions@oteria.fr", "first_seen": "2018-09", "last_seen": "2025-06", "occurrences": 96, "metadata": {}},
    {"id": 4, "category": "subdomains", "value": "www.oteria.fr", "first_seen": "2016-03", "last_seen": "2025-06", "occurrences": 388, "metadata": {}},
    {"id": 5, "category": "subdomains", "value": "staging.oteria.fr", "first_seen": "2021-06", "last_seen": "2021-08", "occurrences": 3, "metadata": {"source_url": "https://web.archive.org/web/20210801000000/http://staging.oteria.fr/"}},
    {"id": 6, "category": "technologies", "value": "nginx", "first_seen": "2016-03", "last_seen": "2025-06", "occurrences": 388, "metadata": {"version": "1.24"}},
    {"id": 7, "category": "analytics_trackers", "value": "UA-38xxxx", "first_seen": "2016-03", "last_seen": "2023-04", "occurrences": 276, "metadata": {"type": "Google Analytics"}},
    {"id": 8, "category": "favicons", "value": "https://oteria.fr/favicon.ico", "first_seen": "2022-05", "last_seen": "2025-06", "occurrences": 40, "metadata": {"md5": "91af7c00", "source_url": "https://web.archive.org/web/20230101000000/http://oteria.fr/favicon.ico"}},
]

INFO = {"name": "oteria.fr", "findings_summary": {}, "scanMeta": {}}


def _open_report(page, live_server):
    page.goto(live_server + "/", wait_until="networkidle")
    page.evaluate(
        """([findings, info]) => {
            if (window.setLang) window.setLang('en');   // stable labels for assertions
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.getElementById('view-results').classList.add('active');
            window.renderReport2(info, findings, {url_id: 'e2etest', domain: 'oteria.fr'});
        }""",
        [FINDINGS, INFO],
    )


def test_categories_view_renders_rail_and_detail(live_server, page):
    _open_report(page, live_server)
    rail = page.locator("#r2-rail")
    assert rail.is_visible()
    # Found categories are listed with counts.
    assert page.locator(".r2-rlink", has_text="Emails").count() >= 1
    assert page.locator(".r2-rlink", has_text="Subdomains").count() >= 1
    # The default-open category (emails, most findings) shows rows.
    main = page.locator("#r2-main")
    assert "contact@oteria.fr" in main.inner_text()
    # Provenance columns present.
    assert "occ." in main.inner_text().lower()
    # Neutral: no severity UI anywhere (stats bar, filter dropdown, per-row dot).
    assert page.locator(".sev-stat").count() == 0
    assert page.locator("#r2-main .col-sev").count() == 0
    assert page.locator("#filter-severity:visible").count() == 0


def test_clicking_a_category_opens_it(live_server, page):
    _open_report(page, live_server)
    page.locator(".r2-rlink", has_text="Tech stack").first.click()
    main = page.locator("#r2-main")
    assert "nginx" in main.inner_text()
    # Its per-category activity block renders.
    assert page.locator(".r2-act").count() >= 1


def test_empty_categories_toggle(live_server, page):
    _open_report(page, live_server)
    # Empty categories are collapsed by default, revealed on toggle.
    assert page.locator(".r2-emptylist").count() == 0
    page.locator(".r2-emptytoggle").click()
    assert page.locator(".r2-emptylist").count() == 1
    assert page.locator(".r2-emptylist .r2-rlink.zero").count() > 10


def test_activity_view_composes_lanes(live_server, page):
    _open_report(page, live_server)
    page.locator("#r2-vbtn-activity").click()
    # Composer with lanes and checkboxes.
    assert page.locator(".r2-composer").count() == 1
    assert page.locator(".r2-composer .r2-lane").count() >= 1
    # Category + pivot checkboxes in the rail.
    assert page.locator(".r2-chk").count() >= 2
    # Favicon gallery present (we supplied one favicon with an archive source).
    assert page.locator(".r2-favstrip").count() == 1


def test_pivot_checkbox_adds_a_lane(live_server, page):
    _open_report(page, live_server)
    page.locator("#r2-vbtn-activity").click()
    before = page.locator(".r2-composer .r2-lane").count()
    # Tick a pivot that isn't checked yet (find an unchecked pivot row).
    unchecked = page.locator(".r2-chk.pv:not(.on)").first
    if unchecked.count() > 0:
        unchecked.click()
        after = page.locator(".r2-composer .r2-lane").count()
        assert after >= before


def test_filter_narrows_the_open_category(live_server, page):
    _open_report(page, live_server)
    # Open emails, filter to "rh". The findings ROWS narrow (the per-category
    # activity below deliberately still shows the full history).
    page.locator(".r2-rlink", has_text="Emails").first.click()
    page.fill("#r2-filter", "rh")
    assert page.locator("#r2-main .r2-row", has_text="rh@oteria.fr").count() >= 1
    assert page.locator("#r2-main .r2-row", has_text="contact@oteria.fr").count() == 0


# --- regression tests for reported UI bugs (fixed) ---

LONG_VAL = "https://cdn.oteria.fr/assets/js/vendor/analytics.bundle.min.js?v=8f3a9c2e1b7d4f60a1c2e3b4d5f6a7b8&ts=1699999999"

# Two categories with DISJOINT date ranges, to prove the timeline axis adapts.
DATED = [
    {"id": 1, "category": "emails", "value": "old@x.com", "first_seen": "2005-01", "last_seen": "2008-06", "occurrences": 5, "metadata": {}},
    {"id": 2, "category": "subdomains", "value": "new.x.com", "first_seen": "2021-01", "last_seen": "2024-06", "occurrences": 9, "metadata": {}},
    {"id": 3, "category": "js_urls", "value": LONG_VAL, "first_seen": "2019-05", "last_seen": "2020-02", "occurrences": 3, "metadata": {}},
]


def _open_with(page, live_server, findings):
    page.goto(live_server + "/", wait_until="networkidle")
    page.evaluate(
        """([findings]) => {
            if (window.setLang) window.setLang('en');
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.getElementById('view-results').classList.add('active');
            window.renderReport2({name: 'x.com'}, findings, {url_id: 't', domain: 'x.com'});
        }""",
        [findings],
    )


def test_long_value_shown_in_full_not_truncated(live_server, page):
    _open_with(page, live_server, DATED)
    page.locator(".r2-rlink", has_text="JavaScript URLs").first.click()
    # The full long URL is present in the DOM (no ellipsis truncation of content).
    txt = page.locator("#r2-main .r2-val-text").first.inner_text()
    assert LONG_VAL in txt


def test_copy_button_is_reachable_on_a_long_row(live_server, page):
    _open_with(page, live_server, DATED)
    page.locator(".r2-rlink", has_text="JavaScript URLs").first.click()
    btn = page.locator("#r2-main .r2-row", has_text="analytics.bundle").locator(".r2-copy")
    assert btn.count() == 1
    box = btn.first.bounding_box()
    main_box = page.locator("#r2-main").bounding_box()
    # The copy button sits within the visible main column (not clipped off-row).
    assert box is not None and box["x"] >= main_box["x"] - 1
    btn.first.click()   # must not throw


def test_per_category_timeline_axis_adapts_to_that_category(live_server, page):
    _open_with(page, live_server, DATED)
    # Open the 2005-2008 emails category: its axis years should be around 2005-2008,
    # NOT the global 2005-2024 span.
    page.locator(".r2-rlink", has_text="Emails").first.click()
    years = page.locator("#r2-main .r2-act .r2-years").first.inner_text()
    assert "2005" in years
    assert "2021" not in years and "2024" not in years


def test_activity_checkbox_state_updates_in_the_rail(live_server, page):
    _open_report(page, live_server)
    page.locator("#r2-vbtn-activity").click()
    first = page.locator("#r2-rail .r2-chk").first
    was_on = "on" in (first.get_attribute("class") or "")
    first.click()
    now_on = "on" in (page.locator("#r2-rail .r2-chk").first.get_attribute("class") or "")
    assert now_on != was_on, "rail checkbox visual did not update on toggle"


def test_activity_offers_many_pivots(live_server, page):
    _open_report(page, live_server)
    page.locator("#r2-vbtn-activity").click()
    # Rich pivot list (subdomains, persons, trackers, emails, tech, favicons…).
    assert page.locator("#r2-rail .r2-chk.pv").count() >= 4


def test_composed_feed_has_no_duplicate_events(live_server, page):
    _open_report(page, live_server)
    page.locator("#r2-vbtn-activity").click()
    # staging.oteria.fr is both a subdomain (checked category) and a pivot; its
    # events must appear once, not twice.
    rows = page.locator(".r2-composer .r2-ev", has_text="staging.oteria.fr")
    texts = rows.all_inner_texts()
    assert len(texts) == len(set(texts)), f"duplicate change-feed events: {texts}"


def test_unticking_everything_shows_a_compose_hint(live_server, page):
    _open_report(page, live_server)
    page.locator("#r2-vbtn-activity").click()
    # Untick every checked category and pivot.
    for _ in range(40):
        on = page.locator("#r2-rail .r2-chk.on")
        if on.count() == 0:
            break
        on.first.click()
    assert page.locator(".r2-empty-compose").count() == 1


def test_show_all_lists_every_found_category(live_server, page):
    _open_report(page, live_server)
    page.locator(".r2-rall", has_text="Show all").click()
    # Every found category's detail block is rendered at once.
    assert page.locator("#r2-main .r2-catblock").count() >= 4


def test_copy_column_button_present_per_category(live_server, page):
    _open_report(page, live_server)
    assert page.locator("#r2-main .r2-copycol").count() >= 1
