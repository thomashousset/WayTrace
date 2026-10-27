"""A running scan's polling ends when the person leaves it.

Clearing the timer could not stop a request already in flight: leaving a
running scan for the legal page let that answer schedule the next poll,
and once the scan finished the page switched itself back to the results
under #/legal. Going from scan A to scan B could render A under B.
The scan API is faked inside the page (delayed fetch); nothing reaches
the server's scan routes or archive.org. Opt-in: WT_E2E=1.
"""
UID_A, UID_B = "A" * 24, "B" * 24


def _wait_polls(page, which, n, timeout_ms=5000):
    # wait_for_function evaluates a string, which the app's CSP refuses.
    for _ in range(timeout_ms // 50):
        if page.evaluate(f"window.__polls.{which}") >= n:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"never reached {n} polls of {which}")

FAKE_API = """
window.__polls = {A: 0, B: 0};
window.__done = false;
const realFetch = window.fetch.bind(window);
window.fetch = (url, opts) => {
  const m = String(url).match(/\\/api\\/s\\/(A{24}|B{24})$/);
  if (!m) return realFetch(url, opts);
  const which = m[1][0];
  window.__polls[which]++;
  const domain = which === 'A' ? 'aaa.example' : 'bbb.example';
  const status = window.__done ? 'completed' : 'running';
  const body = JSON.stringify({url_id: m[1], domain, status, phase: 'scraping', progress: 40,
    results: {emails: [{value: 'x@' + domain, first_seen: '2015-01', last_seen: '2016-01', occurrences: 1}]},
    created_at: '2026-10-01T00:00:00', expires_at: '2026-11-01T00:00:00'});
  const delay = which === 'A' ? 700 : 50;
  return new Promise(res => setTimeout(() => res(new Response(body, {status: 200,
    headers: {'Content-Type': 'application/json'}})), delay));
};
"""


def test_leaving_a_running_scan_stops_its_polling(live_server, page):
    page.add_init_script(FAKE_API)
    page.goto(f"{live_server}/#/s/{UID_A}")
    _wait_polls(page, "A", 2)
    page.wait_for_timeout(200)                       # the next request is in flight
    page.evaluate("location.hash = '#/legal'")
    n = page.evaluate("window.__polls.A")
    page.evaluate("window.__done = true")            # the scan finishes meanwhile
    page.wait_for_timeout(3000)
    assert page.evaluate("window.__polls.A") == n, "polling went on after leaving the scan"
    assert page.evaluate("document.querySelector('.view.active')?.id") == "view-legal"


def test_switching_scans_never_shows_the_previous_one(live_server, page):
    # Both running: they share the progress view, so A's stale answer (it
    # lands ~700 ms after the switch) overwrote B's header with A's domain
    # until the next poll, and A's chain went on polling B a second time.
    page.add_init_script(FAKE_API)
    page.goto(f"{live_server}/#/s/{UID_A}")
    _wait_polls(page, "A", 1)                       # A's first request is in flight
    page.evaluate(f"location.hash = '#/s/{UID_B}'")
    seen = set()
    for _ in range(14):                             # sample ~2 s
        seen.add(page.evaluate("document.getElementById('public-scan-domain')?.innerText || ''"))
        page.wait_for_timeout(150)
    assert "aaa.example" not in seen, seen
    assert "bbb.example" in seen
    assert page.evaluate("window.__polls.A") == 1
    assert page.evaluate("window.__polls.B") <= 3, "two polling chains on B"
