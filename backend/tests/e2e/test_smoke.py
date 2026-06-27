"""Offline e2e smoke tests: home, language toggle, legal page. No scan."""
import pytest


def test_home_loads_and_has_search(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    assert "WayTrace" in page.title() or page.locator("h1.home-title").count() > 0
    assert page.locator(".home-search-input, #domain-input").first.is_visible()
    # The hero subtitle is present (sells the tool).
    assert page.locator(".home-sub").first.inner_text().strip() != ""


def test_language_toggle_switches_copy(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    page.evaluate("() => window.setLang && window.setLang('en')")
    en = page.locator(".home-sub").first.inner_text()
    page.evaluate("() => window.setLang && window.setLang('fr')")
    fr = page.locator(".home-sub").first.inner_text()
    assert en != fr                       # copy actually changed
    assert "Wayback" in en and "Wayback" in fr
    # Flag switcher reflects the active language.
    assert page.locator('#lang-switch .lang-opt[data-lang="fr"].active').count() == 1


def test_legal_page_renders(live_server, page):
    page.goto(live_server + "/#/legal", wait_until="networkidle")
    body = page.locator("#view-legal")
    assert body.is_visible()
    assert "MIT" in body.inner_text()      # licence section present
    # Nine numbered sections.
    assert body.locator("h2").count() >= 8


def test_unknown_route_shows_404(live_server, page):
    page.goto(live_server + "/#/definitely-not-a-route", wait_until="networkidle")
    assert page.locator("#view-notfound").is_visible()
