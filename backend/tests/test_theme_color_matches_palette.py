"""The browser chrome must be painted the colour of the page it frames.

<meta name="theme-color"> and the manifest's theme/background colours drive
the Android address bar, the iOS status bar and the PWA splash. They held
#1C1B18 after the palette moved to #151311, so on a phone the bar sat a
shade off the page below it. Nothing on a desktop screenshot shows that.
"""
import json
import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _token(name: str, block_start: str) -> str:
    css = (_FRONTEND / "styles.css").read_text(encoding="utf-8")
    i = css.index(block_start)
    m = re.search(r"%s:\s*(#[0-9A-Fa-f]{3,8})" % re.escape(name), css[i:i + 1200])
    return m.group(1).upper()


def test_the_static_meta_is_the_default_palettes_ground():
    dark_bg = _token("--bg", ":root {")
    html = (_FRONTEND / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{3,8})"', html)
    assert m, "no theme-color meta"
    assert m.group(1).upper() == dark_bg


def test_the_manifest_matches_the_same_ground():
    dark_bg = _token("--bg", ":root {")
    man = json.loads((_FRONTEND / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert man["theme_color"].upper() == dark_bg
    assert man["background_color"].upper() == dark_bg


def test_the_app_keeps_it_in_step_with_the_palette():
    # A static value cannot be right: the mode toggles and the themes page
    # swaps whole palettes, so it has to be read back from the page.
    js = (_FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "function syncThemeColor()" in js
    assert "getPropertyValue('--bg')" in js
    # And it must run on every palette application, not only on the toggle.
    body = js[js.index("function applyThemeVars()"):]
    assert "syncThemeColor();" in body[:body.index("\n}\n")]


def test_every_manifest_icon_exists():
    man = json.loads((_FRONTEND / "manifest.webmanifest").read_text(encoding="utf-8"))
    missing = [i["src"] for i in man["icons"]
               if not (_FRONTEND / i["src"].lstrip("/")).exists()]
    assert missing == []
