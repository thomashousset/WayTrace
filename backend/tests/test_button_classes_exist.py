"""No button may rely on a class the stylesheet never defines.

Both dialogs shipped their Cancel as `class="btn-ghost"`, and .btn-ghost was
written nowhere in styles.css. A class that does not exist applies nothing,
so those buttons fell all the way back to the browser's native control:
mid-grey fill, a 2px white outset border, square corners, no padding, and a
font a point larger than everything around them, sitting next to a properly
drawn accent button. Nothing fails when this happens. The page renders, the
button works, and it simply looks like a 1997 form.

The pair it belongs to, `btn btn-ghost`, would have been fine: the modifier
adds nothing on its own and the base does the work. So the check is not that
every class exists, which would flag state classes the JS toggles, but that
each button has at least one class the stylesheet actually draws.
"""
import re
from pathlib import Path

_FRONT = Path(__file__).resolve().parents[2] / "frontend"
_HTML = (_FRONT / "index.html").read_text(encoding="utf-8")
_CSS = (_FRONT / "styles.css").read_text(encoding="utf-8")

_DEFINED = set(re.findall(r"\.([A-Za-z][\w-]*)", _CSS))


def _buttons() -> list[tuple[str, list[str]]]:
    out = []
    for m in re.finditer(r"<(button|a)\b([^>]*)>", _HTML):
        attrs = m.group(2)
        cls = re.search(r'class="([^"]*)"', attrs)
        if not cls:
            continue
        names = [c for c in cls.group(1).split() if c]
        if any(c.startswith("btn") or c.endswith("-btn") for c in names):
            out.append((m.group(0)[:90], names))
    return out


def test_every_button_has_at_least_one_class_the_stylesheet_draws() -> None:
    orphans = [
        (tag, names) for tag, names in _buttons()
        if not any(c in _DEFINED for c in names)
    ]
    assert not orphans, "buttons styled by nothing:\n" + "\n".join(
        f"  {names} in {tag}" for tag, names in orphans)


def test_the_ghost_modifier_is_defined_now_that_it_is_used() -> None:
    assert "btn-ghost" in _DEFINED, (
        ".btn-ghost is referenced in the markup and must exist in styles.css"
    )


def test_the_ghost_modifier_does_not_compete_with_the_accent_button() -> None:
    """It is the quiet half of a pair. A second filled button is not that."""
    i = _CSS.index(".btn-ghost {")
    body = _CSS[i:_CSS.index("}", i)]
    assert "transparent" in body, "a ghost button carries no fill at rest"
    assert "var(--accent)" not in body, "the accent belongs to the primary action"


def test_the_ghost_rules_come_after_the_base_button_rules() -> None:
    """Same specificity, so source order is the whole mechanism."""
    assert _CSS.index(".btn:hover") < _CSS.index(".btn-ghost:hover"), (
        ".btn:hover would otherwise win and repaint the ghost on hover"
    )
