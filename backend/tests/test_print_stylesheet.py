"""The print rules must name elements the app still renders.

Printing a report, or saving it as a PDF to hand over, is how an OSINT
finding leaves the tool. The print block used to target .pub-cat and
.pub-actions, the classes of the scan view that came before the current one,
so every dark-theme token survived onto the paper and the domain, the counts
and the extracted values all came out white on white.

Nothing catches that by reading the screen, so this reads the selectors.
"""
import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _print_blocks(css: str) -> list[str]:
    out, i = [], 0
    while (i := css.find("@media print", i)) != -1:
        j = css.index("{", i)
        depth, k = 1, j + 1
        while depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        out.append(css[j + 1:k - 1])
        i = k
    return out


def test_there_is_a_print_block_for_the_report():
    css = (_FRONTEND / "styles.css").read_text(encoding="utf-8")
    blocks = _print_blocks(css)
    assert len(blocks) >= 2
    joined = "\n".join(blocks)
    assert "--text-bright" in joined, "print must repaint the tokens, not one element at a time"


def test_every_class_the_print_rules_hide_still_exists():
    css = (_FRONTEND / "styles.css").read_text(encoding="utf-8")
    markup = (_FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (_FRONTEND / "app.js").read_text(encoding="utf-8")
    live = markup + js
    dead = []
    for block in _print_blocks(css):
        for cls in set(re.findall(r"\.([a-z][a-z0-9-]{3,})", block)):
            # A class is emitted as class="x", class="x y" or class="x${...}".
            if not re.search(r"[\"'\s]%s[\"'\s$`]" % re.escape(cls), live):
                dead.append(cls)
    assert dead == [], f"print rules target classes nothing renders: {sorted(set(dead))}"
