"""When a data-i18n key IS an English sentence, it has to be the sentence shown.

Most keys here are symbolic (`legal.p10`, `adm.screenshot`). The rest use the
English copy itself as the key, which is convenient until somebody rewrites the
copy and leaves the attribute alone. Then the key names a sentence that is no
longer on screen anywhere, and the next person to rewrite that line will update
the attribute too, silently orphaning the French translation: the lookup misses
and the French reader is shown the old English sentence instead.

Reported from production by a user who noticed the bug dialog's subtitle and
read the source to explain it. The symptom they described did not reproduce,
French rendered correctly in every path tested, but the drift was real and one
edit away from becoming the bug they expected. An audit of all 28
sentence-shaped keys found exactly that one.
"""
import html
import re
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[2] / "frontend" / "index.html"
pytestmark = pytest.mark.skipif(not INDEX.exists(), reason="frontend not present")

_TAG = re.compile(r'data-i18n(?:-html)?="([^"]+)"[^>]*>([^<]{3,300})<', re.S)


def _norm(s: str) -> str:
    return " ".join(html.unescape(s).split()).strip()


def _sentence_keyed() -> list[tuple[str, str]]:
    """(key, visible text) for every element keyed on English prose."""
    out = []
    for key, text in _TAG.findall(INDEX.read_text(encoding="utf-8")):
        first = key.split()[0]
        if "." in first and not key.endswith("."):
            continue                      # symbolic id, not prose
        if not re.search(r"[a-z]\s[a-z]", key):
            continue                      # single word or a label, not a sentence
        out.append((_norm(key), _norm(text)))
    return out


def test_there_are_sentence_keyed_elements_to_check():
    """If the markup convention changes, this file must not quietly pass by
    finding nothing to look at."""
    assert len(_sentence_keyed()) >= 10


def test_every_sentence_key_is_the_sentence_on_screen():
    drifted = [(k, t) for k, t in _sentence_keyed() if k != t]
    assert not drifted, (
        "a data-i18n key no longer matches the English text it stands for, so "
        "the next edit to that line orphans its translation:\n  "
        + "\n  ".join(f"key:  {k}\n  text: {t}" for k, t in drifted)
    )
