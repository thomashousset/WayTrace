"""The extraction category list exists twice: once in the backend, once in the
frontend rail. They drifted, and the report header carried a third copy as the
literal 43 while both lists had grown to 48, so the page told the reader it had
searched five categories it had in fact searched.

These tests read both files and compare them, so the next category can only be
added in one place before something fails.
"""
import re
from pathlib import Path

import pytest

from services.extractor.extract import CATEGORY_EXTRACTORS
from services.extractor.finalize import ALL_CATEGORIES

APP_JS = Path(__file__).resolve().parents[2] / "frontend" / "app.js"


def _frontend_categories() -> set[str]:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("const CAT_DESCRIPTIONS")
    end = src.index("\n};", start)
    return set(re.findall(r"^  ([a-z_]+):", src[start:end], re.MULTILINE))


def test_every_registered_extractor_is_a_declared_category():
    """One direction only.

    CATEGORY_EXTRACTORS maps a category to the module that owns it, but several
    modules fill more than one category: jsonld_structured_extract alone writes
    persons, phones, organizations and addresses. So a category can legitimately
    be in ALL_CATEGORIES without its own entry here. The reverse cannot happen:
    an extractor registered under a key finalize does not know about writes into
    an accumulator slot that is never read back out.
    """
    orphans = set(CATEGORY_EXTRACTORS) - set(ALL_CATEGORIES)
    assert not orphans, f"extractor registered for a category finalize drops: {sorted(orphans)}"


@pytest.mark.skipif(not APP_JS.exists(), reason="frontend not present in this build")
def test_frontend_rail_describes_every_backend_category():
    missing = set(ALL_CATEGORIES) - _frontend_categories()
    extra = _frontend_categories() - set(ALL_CATEGORIES)
    assert not missing, f"extracted but never shown in the report rail: {sorted(missing)}"
    assert not extra, f"shown in the report rail but never extracted: {sorted(extra)}"


@pytest.mark.skipif(not APP_JS.exists(), reason="frontend not present in this build")
def test_no_hardcoded_category_total_in_the_frontend():
    """The report header said 9/43 while there were 48. Read the list instead."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "'/43'" not in src and "/43'" not in src, (
        "the category total is hardcoded again; derive it from REPORT2_SCOPE.length"
    )


@pytest.mark.skipif(not APP_JS.exists(), reason="frontend not present in this build")
def test_every_category_has_an_explicit_label():
    """Without an entry in CAT_LABELS the label is invented from the key and then
    passed through t(), which cannot match a string that exists nowhere in the
    dictionary. 'addresses' surfaced as the English 'Addresses' in the middle of
    a French settings page, and with the English spelling at that.
    """
    src = APP_JS.read_text(encoding="utf-8")

    def keys_of(name: str) -> set[str]:
        start = src.index("const " + name)
        return set(re.findall(r"^  ([a-z_]+):", src[start:src.index("\n};", start)], re.MULTILINE))

    missing = keys_of("CAT_DESCRIPTIONS") - keys_of("CAT_LABELS")
    assert not missing, (
        "no CAT_LABELS entry, so the label is guessed from the key and never "
        f"translated: {sorted(missing)}"
    )
