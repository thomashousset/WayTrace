"""The navigation bar holds four controls, and no page links.

Appearance, mode and language used to share one paintbrush button that opened
a menu, which meant changing the language required guessing that a paintbrush
also held languages. They are three different things and they now have three
buttons, each with its own icon.

History left the bar for the account menu, where the rest of the pages already
were. It stays in the bar in exactly one case: the build with no accounts has
no account menu, so removing it there would leave the page unreachable. That
case is decided from the DOM, not from a flag, because the account markup is
stripped out of that build entirely.
"""
import re
from pathlib import Path

import pytest

_FRONT = Path(__file__).resolve().parents[2] / "frontend"
_HTML = (_FRONT / "index.html").read_text(encoding="utf-8")
_JS = (_FRONT / "app.js").read_text(encoding="utf-8")

_ACTIONS = _HTML[_HTML.index('<div class="nav-actions">'):_HTML.index("</nav>")]

# The account markup is stripped from the build without accounts, so the two
# builds put the pages in different places. Both arrangements are correct and
# both are checked, picked apart by what the markup actually contains rather
# than by a flag that does not exist at this layer.
_HAS_ACCOUNT_MENU = 'id="account-menu"' in _HTML


def test_the_bar_holds_mode_theme_language_and_source() -> None:
    for ident in ("mode-btn", "theme-btn", "lang-btn", "source-btn"):
        assert f'id="{ident}"' in _ACTIONS, f"{ident} must be in the navigation bar"


def test_each_control_carries_its_own_icon() -> None:
    """Four identical-looking buttons would be worse than the one menu."""
    for ident in ("mode-btn", "theme-btn", "lang-btn", "source-btn"):
        i = _ACTIONS.index(f'id="{ident}"')
        end = _ACTIONS.find("</button>", i)
        if end == -1 or (0 < _ACTIONS.find("</a>", i) < end):
            end = _ACTIONS.find("</a>", i)
        assert "<svg" in _ACTIONS[i:end], f"{ident} must carry an icon"


def test_the_shared_preferences_menu_is_gone() -> None:
    assert 'id="pref-wrap"' not in _HTML
    assert 'id="pref-menu"' not in _HTML
    for dead in ("togglePrefMenu", "renderPrefMenu", "hidePrefMenu"):
        assert dead not in _JS, f"{dead} must not survive the menu it drove"


@pytest.mark.skipif(not _HAS_ACCOUNT_MENU,
                    reason="build without accounts has no menu to hold them")
def test_the_pages_live_in_the_account_menu() -> None:
    menu = _HTML[_HTML.index('id="account-menu"'):_HTML.index("</nav>")]
    assert "#/history" in menu, "History belongs in the account menu"
    assert "#/config" in menu, "Settings belongs in the account menu"


def test_history_is_reachable_in_both_builds() -> None:
    """The bar keeps the button; who hides it is what differs.

    With accounts, the menu holds the pages and renderAccountControl hides the
    bar's copy so the two do not both shout. Without accounts there is no menu
    and no renderAccountControl, so the button is simply the way in, and
    nothing must be left that could hide it.
    """
    assert 'id="history-btn"' in _ACTIONS, "the bar must ship the button"
    if _HAS_ACCOUNT_MENU:
        fn = _JS[_JS.index("function renderAccountControl"):]
        fn = fn[:fn.index("\nfunction ", 1)]
        assert "history-btn" in fn, (
            "renderAccountControl must hide the bar's History once the menu holds it"
        )
    else:
        assert "renderAccountControl" not in _JS, (
            "no account control in this build, so nothing should reference it"
        )
        assert 'id="history-btn"' in _ACTIONS and "hidden" not in _ACTIONS[
            _ACTIONS.index('id="history-btn"'):_ACTIONS.index('id="history-btn"') + 120], (
            "History is the only way to the page here, it cannot ship hidden"
        )


def test_nothing_dereferences_the_history_button_blindly() -> None:
    """It is hidden in one build and absent from the other."""
    for m in re.finditer(r"\$\('history-btn'\)(\.\w+)", _JS):
        line = _JS[_JS.rfind("\n", 0, m.start()) + 1:_JS.find("\n", m.end())]
        assert "?." in line or "if (" in line, (
            f"unguarded access to history-btn: {line.strip()}"
        )


def test_the_mode_label_survives_being_painted_before_the_dictionary() -> None:
    """applyThemeLabel runs at module scope, above where LANG is declared.

    The version this replaced only did work when a menu happened to be open,
    so it never reached t() during boot. This one always paints the icon, and
    a bare t() there throws on LANG's temporal dead zone, which takes the rest
    of app.js with it: no theme, no scan button, a blank page.
    """
    fn = _JS[_JS.index("function applyThemeLabel"):]
    fn = fn[:fn.index("\nfunction ", 1)]
    assert "typeof LANG" not in fn, (
        "typeof does not save you from a let in its temporal dead zone, it "
        "throws there like any other read"
    )
    assert "try {" in fn, "the t() call has to be caught, not guarded"
    assert fn.index("try {") < fn.index("t('Switch to"), (
        "the catch must wrap the t() call, not follow it"
    )
