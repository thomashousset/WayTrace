"""The version on the wordmark is set in the site's sans, not in monospace.

A small green number in a fixed-width face, parked next to a logo, is the
house style of every generated landing page on the internet. The rest of
WayTrace is one sans family throughout, and the mark reads as part of the
wordmark only if it is set in that same family. The "v" carries the meaning
of "version" on its own, so the colour no longer has to: the mark sits in the
faint text tone and never competes with the accent the logo is painted in.

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
    body = _rule(".home-title .home-ver {")
    assert "var(--font)" in body, "the mark must be set in the site's sans"
    assert "var(--mono)" not in body, (
        "monospace is the terminal cliche the mark was moved away from"
    )


def test_version_mark_is_not_painted_green_or_accent() -> None:
    body = _rule(".home-title .home-ver {")
    colour = re.search(r"(?<!-)\bcolor:\s*([^;]+);", body)
    assert colour, "the mark must state its own colour, it inherits accent otherwise"
    assert colour.group(1).strip() == "var(--text-faint)", (
        "the v already says 'version', so the colour must stay out of the way"
    )


def test_the_v_prefix_is_drawn_by_the_stylesheet() -> None:
    """The JS writes only the number, so the prefix has to come from CSS.

    showVersionMark assigns textContent, which is what keeps a value from the
    API out of the HTML parser. The prefix is presentation, and the element is
    aria-hidden, so generated content is the right place for it.
    """
    body = _rule(".home-title .home-ver::before {")
    assert re.search(r"content:\s*['\"]v['\"]", body), "the prefix must be a v"

    app = (Path(__file__).resolve().parents[2] / "frontend" / "app.js").read_text(
        encoding="utf-8"
    )
    fn = app[app.index("function showVersionMark") :][:400]
    assert "textContent" in fn and "innerHTML" not in fn, (
        "the version string comes from the API and must never be parsed as HTML"
    )
