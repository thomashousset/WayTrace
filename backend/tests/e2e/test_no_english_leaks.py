"""Read every view in French and look for English that leaked through.

Every i18n check written before this one reasoned about the source: the call
sites that show a toast, the strings the backend sends, the keys t() is asked
for. None could see a block of English sitting in the markup with no
data-i18n on it, because nothing ever asks t() about it. That is how the
whole bug-report dialog and the scope subtitle survived a sweep that was
declared complete.

Two attempts at detecting it by parsing index.html produced more false
positives than findings: nested elements under a translated parent, multi-line
tags, JS-replaced blocks. The rendered page is the only ground truth, so the
check runs in the browser and reads what a French visitor actually sees.
"""
import re

import pytest

# Deliberately English: product names, formats, third parties, identifiers.
_ALLOWED = {
    "WayTrace", "Wayback Machine", "Internet Archive", "archive.org", "OSINT",
    "JSON", "CSV", "Markdown", "HTML", "API", "GitHub", "Shodan", "Stripe",
    "Google", "AWS", "Slack", "OpenAI", "Docker", "Caddy", "URL", "IP",
    "Source on GitHub", "English", "Français",
}

# Function words that only appear in an English sentence, never in a French one.
_ENGLISH_ONLY = re.compile(
    r"\b(the|your|you|we|please|what|went|wrong|attach|cancel|send|sign in|"
    r"sign up|keep|before|launching|happened|expect|retrieved|analysed|"
    r"snapshots analysed|findings|categories|still present|disappeared)\b", re.I)

# No #/scope route here on purpose: opening it fires the CDX preflight, which
# is a real request to archive.org. The suite must never reach them.
_ROUTES = ["", "#/history", "#/themes", "#/legal", "#/pasunepage"]


def _visible_text_nodes(page):
    return page.evaluate("""() => {
      const out = [];
      const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = walk.nextNode())) {
        const t = (n.textContent || '').trim();
        if (t.length < 6 || t.length > 160) continue;
        const p = n.parentElement;
        if (!p || !p.offsetParent) continue;
        if (['SCRIPT', 'STYLE', 'CODE', 'PRE'].includes(p.tagName)) continue;
        out.push(t);
      }
      return [...new Set(out)];
    }""")


def _leaks(texts):
    bad = []
    for t in texts:
        if t in _ALLOWED:
            continue
        if any(a in t for a in ("archive.org", "Wayback", "WayTrace")) and len(t) < 40:
            continue
        # A French sentence carries French markers; an English one does not.
        if re.search(r"[éèêàçûôîùœ]|\b(le|la|les|un|une|des|du|de|et|pour|avec|"
                     r"sur|par|vous|votre|dans|est|sont|ce|cette|qui|que)\b", t, re.I):
            continue
        if _ENGLISH_ONLY.search(t):
            bad.append(t)
    return bad


@pytest.mark.parametrize("route", _ROUTES)
def test_no_english_sentence_is_visible_in_french(live_server, page, route):
    page.goto(live_server + "/" + route, wait_until="networkidle")
    page.wait_for_timeout(900)
    page.evaluate("() => setLang('fr')")
    page.wait_for_timeout(700)
    leaks = _leaks(_visible_text_nodes(page))
    assert leaks == [], f"{route or 'home'} shows English: {leaks}"


def test_the_bug_dialog_and_the_tab_title_follow_the_language(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    page.evaluate("() => setLang('fr')")
    page.wait_for_timeout(700)
    assert "reconnaissance" in page.title(), page.title()
    page.evaluate("() => openBugReport()")
    page.wait_for_timeout(500)
    dialog = page.locator("#wt-bug-overlay").inner_text()
    assert _leaks([l for l in dialog.split("\n") if l.strip()]) == [], dialog
    # And the hidden file input still has a name for a screen reader.
    assert page.get_attribute("#wt-bug-shot", "aria-label")
