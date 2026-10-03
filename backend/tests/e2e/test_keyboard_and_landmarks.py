"""What a keyboard, and a screen reader, can actually do here.

The shortcut panel promises "Esc closes any open drawer or overlay". It does
that for the shortcut overlay itself and for nothing else: the bug dialog and
the sign-in modal both stay open. Every one of the three declares
role="dialog" aria-modal="true", which promises the rest of the page is
inert, and Tab walks straight out of all of them into content the overlay is
covering. None gives focus back to whatever opened it.

And the report, the thing this product exists to produce, carried no heading
at all: the domain was a div, so there was no structure to navigate by.
"""
import pytest

_VISIBLE = """(s) => {
  const e = [...document.querySelectorAll(s)]
    .find(x => !x.hidden && getComputedStyle(x).display !== 'none');
  return !!e;
}"""

_MODALS = [
    ("bug report", "openBugReport()", "#wt-bug-overlay"),
    ("shortcuts", "showKbHelp()", "#kb-overlay"),
]


@pytest.mark.parametrize("name,opener,sel", _MODALS)
def test_escape_closes_every_overlay(live_server, page, name, opener, sel):
    page.goto(live_server + "/", wait_until="networkidle")
    page.wait_for_timeout(700)
    page.evaluate("() => { %s }" % opener)
    page.wait_for_timeout(400)
    assert page.evaluate(_VISIBLE, sel), f"{name} did not open"
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    assert not page.evaluate(_VISIBLE, sel), f"Escape left {name} open"


@pytest.mark.parametrize("name,opener,sel", _MODALS)
def test_tab_stays_inside_an_open_dialog(live_server, page, name, opener, sel):
    page.goto(live_server + "/", wait_until="networkidle")
    page.wait_for_timeout(700)
    page.evaluate("() => { %s }" % opener)
    page.wait_for_timeout(400)
    escaped = 0
    for _ in range(14):
        page.keyboard.press("Tab")
        page.wait_for_timeout(30)
        inside = page.evaluate("""(s) => {
          const m = [...document.querySelectorAll(s)]
            .find(x => !x.hidden && getComputedStyle(x).display !== 'none');
          return m ? m.contains(document.activeElement) : false;
        }""", sel)
        if not inside:
            escaped += 1
    assert escaped == 0, f"Tab left {name} {escaped} times out of 14"


def test_focus_comes_back_to_what_opened_the_dialog(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    page.wait_for_timeout(700)
    page.evaluate("() => document.querySelector('.wt-bugfab').focus()")
    before = page.evaluate("() => document.activeElement.className")
    page.evaluate("() => openBugReport()")
    page.wait_for_timeout(400)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.activeElement.className") == before


def test_the_report_has_a_heading_and_the_page_has_a_main(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    page.wait_for_timeout(700)
    # Landmarks are on the shell, so any view will do.
    assert page.locator("main").count() == 1
    # A skip link is the one control that makes a landmark usable.
    assert page.locator("a.skip-link").count() == 1
    # And the report names itself with a heading rather than a styled div.
    page.evaluate("""() => {
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      document.getElementById('view-results').classList.add('active');
      document.getElementById('res-domain').textContent = 'example.com';
    }""")
    page.wait_for_timeout(200)
    assert page.locator("#view-results h1").count() == 1
