"""A failed scan must say why in words, not in a token.

classify_failure writes three or four careful sentences per failure, and
_failure_meta stored only the code beside them. The frontend rendered that
code through t(), no entry matched, and t() falls back to the key, so the
whole explanation came out as the literal string `cdx_timeout`. In both
languages. The English text existed the entire time and no one ever saw it.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers.scan import _FAILURE_SIGNATURES

_APP_JS = Path(__file__).resolve().parents[2] / "frontend" / "app.js"

# Written by classify_failure outside the signature table.
_EXTRA_CODES = ("scan_timeout", "unexpected")


def _codes() -> list[str]:
    seen = []
    for code, _, _ in _FAILURE_SIGNATURES:
        if code not in seen:
            seen.append(code)
    return seen + list(_EXTRA_CODES)


def _map(name: str) -> str:
    js = _APP_JS.read_text(encoding="utf-8")
    i = js.index(name)
    depth, k = 0, js.index("{", i)
    start = k
    while True:
        if js[k] == "{":
            depth += 1
        elif js[k] == "}":
            depth -= 1
            if depth == 0:
                return js[start:k]
        k += 1


def test_every_failure_code_reads_as_a_sentence_in_both_languages():
    fr = _map("const I18N = ")
    en = _map("const I18N_EN = ")
    missing = []
    for code in _codes():
        key = "err." + code
        for label, blob in (("fr", fr), ("en", en)):
            if ("'%s':" % key) not in blob:
                missing.append(f"{key} ({label})")
    assert missing == [], f"a failed scan would show a raw token: {missing}"


def test_the_frontend_looks_the_code_up_rather_than_printing_it():
    js = _APP_JS.read_text(encoding="utf-8")
    assert "_failureText(" in js
    # And an unknown code, added later on the backend, must not reach the page.
    body = js[js.index("function _failureText("):]
    body = body[:body.index("\n}\n")]
    assert "err.unexpected" in body


def test_the_messages_are_not_the_codes():
    fr = _map("const I18N = ")
    for code in _codes():
        m = re.search(r"""'err\.%s':\s*(?:'([^']*)'|"([^"]*)")""" % re.escape(code), fr)
        assert m, code
        text = m.group(1) or m.group(2)
        assert len(text) > 25, f"err.{code} is not a sentence"
