"""E2E for UI polish / a11y: homepage focus rings, feed error vs empty state,
and the scan-completion moment. Opt-in: WT_E2E=1."""


def test_search_pill_shows_a_focus_halo(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    page.locator(".home-search-input").focus()   # :focus-within applies programmatically
    shadow = page.evaluate(
        "() => getComputedStyle(document.querySelector('.home-search')).boxShadow")
    assert shadow and shadow != "none", "search pill has no visible focus halo"


def test_scan_button_has_a_keyboard_focus_ring(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    page.locator(".home-search-input").focus()
    page.keyboard.press("Tab")   # keyboard move -> the Scan button, triggers :focus-visible
    ring = page.evaluate(
        """() => {
            const el = document.querySelector('.home-search-btn');
            if (document.activeElement !== el) return 'not-focused';
            const s = getComputedStyle(el);
            return s.outlineStyle + '|' + s.boxShadow;
        }""")
    assert ring != "not-focused"
    assert "solid" in ring or (("none" not in ring.split("|")[1]) and ring.split("|")[1]), \
        f"Scan button has no visible keyboard focus ring: {ring}"


def test_feed_shows_error_state_on_failure_not_empty(live_server, page):
    # Force /api/feed to fail -> the ERROR state must show (with Retry), not the
    # "no scans yet" empty state (which would misrepresent a network problem).
    page.route("**/api/feed*", lambda route: route.fulfill(status=500, body="nope"))
    page.goto(live_server + "/", wait_until="networkidle")
    page.wait_for_selector(".feed-error", timeout=5000)
    assert page.locator(".feed-error").count() == 1
    assert page.locator(".feed-error button", has_text="").count() >= 1  # Retry button
    assert not page.locator("#home-feed-empty").is_visible()


def test_feed_shows_empty_state_on_genuine_empty(live_server, page):
    page.route("**/api/feed*", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"items": [], "count": 0}'))
    page.goto(live_server + "/", wait_until="networkidle")
    page.wait_for_selector("#home-feed-empty", state="visible", timeout=5000)
    assert page.locator("#home-feed-empty").is_visible()
    assert page.locator(".feed-error").count() == 0


def test_feed_retry_refetches(live_server, page):
    calls = {"n": 0}
    def handler(route):
        calls["n"] += 1
        if calls["n"] == 1:
            route.fulfill(status=500, body="nope")
        else:
            route.fulfill(status=200, content_type="application/json", body='{"items": [], "count": 0}')
    page.route("**/api/feed*", handler)
    page.goto(live_server + "/", wait_until="networkidle")
    page.wait_for_selector(".feed-error", timeout=5000)
    page.locator(".feed-error button").first.click()
    page.wait_for_selector("#home-feed-empty", state="visible", timeout=5000)
    assert calls["n"] >= 2


def test_completion_moment_toast(live_server, page):
    # Drive renderPublicScan through running -> completed and assert a neutral
    # completion toast appears with a finding count.
    page.goto(live_server + "/", wait_until="networkidle")
    page.evaluate("""() => {
        if (window.setLang) window.setLang('en');
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.getElementById('view-scan-public').classList.add('active');
        window.publicScanUrlId = 'done1';
        // first: running (sets the 'running' prev-status)
        window.renderPublicScan({status: 'running', domain: 'x.com', step: 'Scraping page 2/2', progress: 40});
    }""")
    page.evaluate("""() => {
        window.renderPublicScan({status: 'completed', url_id: 'done1', domain: 'x.com',
            results: {emails: [{value:'a@x.com', first_seen:'2020-01', last_seen:'2020-01', occurrences:1}],
                      subdomains: [{value:'w.x.com', first_seen:'2020-01', last_seen:'2020-01', occurrences:1}]},
            meta: {}});
    }""")
    toast = page.locator("#toast")
    assert "complete" in toast.inner_text().lower()
    assert "2" in toast.inner_text()   # 2 findings
