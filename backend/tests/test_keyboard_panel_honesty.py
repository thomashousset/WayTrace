"""The keyboard panel must document only shortcuts that exist.

The report's severity tiers were removed when it moved to ordering by
consequence, and the four tier shortcuts plus the "clear value filter" one
went with them. The panel kept listing all five, and they were carefully
translated into French in a later pass without anyone pressing them.
"""
import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

_ROW = re.compile(
    r'<span class="kb-key">([^<]+)</span><span class="kb-desc"', re.S
)


def _documented_keys() -> list[str]:
    html = (_FRONTEND / "index.html").read_text(encoding="utf-8")
    panel = html.split('class="kb-title"', 1)[1].split("kb-footer", 1)[0]
    return _ROW.findall(panel)


def _handled_keys() -> set[str]:
    js = (_FRONTEND / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r"key === '([^']+)'", js))
    keys |= set(re.findall(r"e\.key === '([^']+)'", js))
    return keys


def test_panel_documents_at_least_the_core_shortcuts():
    assert set(_documented_keys()) >= {"?", "e", "h", "n", "Esc"}


def test_every_documented_shortcut_is_implemented():
    handled = _handled_keys()
    handled.add("Esc")  # written Escape in the handler
    dead = [k for k in _documented_keys() if k not in handled]
    assert dead == [], f"the panel promises keys nothing implements: {dead}"
