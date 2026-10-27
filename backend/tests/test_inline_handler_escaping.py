"""Values inside inline handlers must be real JS literals, not HTML-escaped text.

The HTML parser decodes &#39; back to ' before an onclick runs, so
onclick="f('${esc(x)}')" let any value close the string: an archived URL
path on the scope page (reachable by anyone who can get archive.org to
capture a crafted URL) and an account email in the admin tabs (the signup
check accepts a quote) both ran their own code. jsArg() emits a
JSON-encoded literal instead, and esc() now escapes quotes for the
attributes it also lands in.
"""
import re
from pathlib import Path

APP = (Path(__file__).resolve().parents[2] / "frontend" / "app.js").read_text()


def test_no_quoted_escape_inside_an_inline_handler():
    bad = [
        (n, line.strip()) for n, line in enumerate(APP.splitlines(), 1)
        if re.search(r'\bon[a-z]+="', line) and re.search(r"'\$\{esc(?:Attr)?\(", line)
    ]
    assert bad == [], bad


def test_esc_escapes_both_quotes():
    m = re.search(r"^const esc = .*$", APP, re.M)
    assert m and "&quot;" in m.group(0) and "&#39;" in m.group(0)


def test_jsarg_is_a_json_literal_escaped_for_the_attribute():
    m = re.search(r"^const jsArg = .*$", APP, re.M)
    assert m and "JSON.stringify" in m.group(0) and "escAttr" in m.group(0)
