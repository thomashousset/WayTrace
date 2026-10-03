"""The README's category table must name the categories that exist.

A README is the one file nobody runs, so a category invented there survives
every test suite and is read as fact by anyone evaluating the tool. The first
draft of this table listed "CMS" and "frameworks", neither of which is a
category, and quietly dropped seven that are.

The table is the contract: all 48, spelled as the code spells them, and
nothing else.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _table_names(readme: Path) -> set[str]:
    """Read the table rows only.

    Not the whole section: the sentence above the table names first_seen and
    last_seen in backticks too, and those are fields, not categories.
    """
    rows = [
        line for line in readme.read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and "`" in line
    ]
    return set(re.findall(r"`([a-z][a-z0-9_]+)`", "\n".join(rows)))


def _all_categories() -> set[str]:
    import sys
    sys.path.insert(0, str(_ROOT / "backend"))
    from services.extractor.finalize import ALL_CATEGORIES
    return set(ALL_CATEGORIES)


def test_the_english_table_covers_every_category_and_invents_none() -> None:
    real = _all_categories()
    listed = _table_names(_ROOT / "README.md")
    assert not (listed - real), f"named in the README but not a category: {listed - real}"
    assert not (real - listed), f"real categories missing from the README: {real - listed}"


def test_the_french_table_says_the_same() -> None:
    fr = _ROOT / "README.fr.md"
    if not fr.exists():
        return
    assert _table_names(fr) == _all_categories(), (
        "the two READMEs must list the same categories"
    )


def test_the_announced_count_matches() -> None:
    n = len(_all_categories())
    for name in ("README.md", "README.fr.md"):
        f = _ROOT / name
        if f.exists():
            assert f"{n} categories" in f.read_text(encoding="utf-8") or \
                   f"{n} catégories" in f.read_text(encoding="utf-8"), (
                f"{name} must state {n} categories"
            )
