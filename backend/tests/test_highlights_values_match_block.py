"""Every highlight must name values from its own block.

The values= argument was added to sixteen call sites in one pass, and one of
them was pasted with the variable of the block above it: hidden_fields listed
`pubs`, which belongs to the Adsense block. Two consequences, and the second
one reached production.

  1. When Adsense had matched, the highlight named Adsense values for hidden
     form fields, which is a wrong claim about which findings it covers.
  2. When it had not, `pubs` was never bound, so reading it raised
     UnboundLocalError and the whole scan failed. Any domain with a hidden
     form field and no Adsense publisher ID crashed.

A scan of sgi.com on production is what found it.
"""
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "services" / "extractor" / "highlights.py"


def test_no_highlight_names_values_from_another_block():
    s = _SRC.read_text(encoding="utf-8")
    wrong = []
    for m in re.finditer(r"_add\((.*?)\n\s*\)", s, re.S):
        block = m.group(1)
        mv = re.search(r"values=\[[^\]]*for \w+ in (\w+)", block)
        if not mv:
            continue
        named = mv.group(1)
        others = set(re.findall(r"\b(?:len|in)\((\w+)\)", block))
        others |= set(re.findall(r"for \w+ in (\w+)\[", block))
        others.discard(named)
        if others and named not in others:
            wrong.append((s[:m.start()].count("\n") + 1, named, sorted(others)))
    assert wrong == [], (
        "a highlight lists values from a different block: " + repr(wrong))


def test_every_variable_read_by_values_is_bound_in_the_same_block():
    """The crash case specifically: a name that only exists under another
    block's `if` is unbound when that branch did not run."""
    s = _SRC.read_text(encoding="utf-8")
    unbound = []
    for m in re.finditer(r"values=\[[^\]]*for \w+ in (\w+)", s):
        name = m.group(1)
        before = s[:m.start()]
        # the nearest assignment of that name before this point
        assigns = list(re.finditer(r"^\s*%s = " % re.escape(name), before, re.M))
        if not assigns:
            unbound.append((before.count("\n") + 1, name))
            continue
        # everything between the assignment and the use must not close its block
        gap = before[assigns[-1].end():]
        if gap.count("\n") > 40:
            unbound.append((before.count("\n") + 1, name))
    assert unbound == [], f"values= reads a name that may be unbound: {unbound}"
