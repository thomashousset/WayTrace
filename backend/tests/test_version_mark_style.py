"""The version on the wordmark: a title-screen splash, still in the site's sans.

Since v2.4 the version is the way into what changed: tilted, breathing
slowly, and a hover or a tap opens the notes of that version. What still holds
from the earlier rule is the face: a fixed-width number next to a logo is the
terminal cliche the mark was moved away from. The colour is the theme's accent
token, never a literal, so the splash follows whichever palette the reader
picked instead of clashing with it.

None of that is visible to a test that renders the page, because the rule is
what it is at rest. So this reads the declaration.
"""
import re
from pathlib import Path

_CSS = (Path(__file__).resolve().parents[2] / "frontend" / "styles.css").read_text(
    encoding="utf-8"
)


def _rule(selector: str) -> str:
    """Return the body of the first rule whose selector list matches exactly."""
    i = _CSS.index(selector)
    j = _CSS.index("{", i)
    return _CSS[j + 1 : _CSS.index("}", j)]


def test_version_mark_uses_the_site_sans() -> None:
    body = _rule(".home-ver-btn {")
    assert "font: inherit" in body, "the mark must be set in the site's sans"
    assert "var(--mono)" not in body, (
        "monospace is the terminal cliche the mark was moved away from"
    )


def test_version_mark_follows_the_theme_accent() -> None:
    body = _rule(".home-ver-btn {")
    colour = re.search(r"(?<!-)\bcolor:\s*([^;]+);", body)
    assert colour, "the mark must state its own colour"
    assert colour.group(1).strip() == "var(--accent)", (
        "the splash takes the theme's accent token, so it follows the palette"
    )


def test_the_splash_stands_still_for_reduced_motion() -> None:
    assert re.search(
        r"prefers-reduced-motion: reduce\)\s*\{\s*\.home-ver-btn\s*\{\s*animation:\s*none",
        _CSS,
    ), "the breathing splash must stop for anyone who asked for less motion"


def test_the_version_string_is_never_parsed_as_html() -> None:
    app = (Path(__file__).resolve().parents[2] / "frontend" / "app.js").read_text(
        encoding="utf-8"
    )
    fn = app[app.index("function showVersionMark") :][:400]
    assert "textContent" in fn and "innerHTML" not in fn, (
        "the version string comes from the API and must never be parsed as HTML"
    )
