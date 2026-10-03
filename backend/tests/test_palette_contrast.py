"""Both palettes must stay readable, not just look right to whoever picked them.

The light theme shipped an accent that failed WCAG AA as a text colour on every
one of its own surfaces, and six rules painted #fff on the accent fill instead
of using --accent-text, which put the navbar Scan button at 2.87 in dark mode.
Nothing caught either, because contrast is invisible until someone measures it.

These read the real stylesheet, so a future palette edit is checked by the same
numbers rather than by eye.
"""
import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[2] / "frontend" / "styles.css"
pytestmark = pytest.mark.skipif(not CSS.exists(), reason="frontend not present")

AA_NORMAL = 4.5   # body-size text
AA_LARGE = 3.0    # >=24px, or >=18.66px bold


def _lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    chan = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    chan = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chan]
    return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _vars(scope: str) -> dict[str, str]:
    """Hex custom properties declared in :root or in [data-theme="light"]."""
    src = CSS.read_text(encoding="utf-8")
    start = src.index(scope)
    block = src[start:src.index("\n    }", start)]
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", block))


DARK = _vars(":root {")
LIGHT = _vars('[data-theme="light"] {')

# Text colours that land on a plain surface somewhere in the app, and the
# surfaces they can land on.
SURFACES = ("--bg", "--surface", "--surface2")
ON_SURFACE = ("--text", "--text-dim", "--text-faint", "--text-bright", "--accent", "--green")


@pytest.mark.parametrize("palette,name", [(DARK, "dark"), (LIGHT, "light")])
def test_every_text_colour_passes_AA_on_every_surface(palette, name):
    failures = []
    for fg in ON_SURFACE:
        for bg in SURFACES:
            if fg not in palette or bg not in palette:
                continue
            r = contrast(palette[fg], palette[bg])
            if r < AA_NORMAL:
                failures.append(f"{name}: {fg} ({palette[fg]}) on {bg} ({palette[bg]}) = {r:.2f}")
    assert not failures, "below AA:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("palette,name", [(DARK, "dark"), (LIGHT, "light")])
def test_accent_text_is_readable_on_the_accent_fill(palette, name):
    """--accent-text exists precisely so the label on an accent button follows
    the palette. In dark mode the fill is a light orange and the label must be
    near-black; six rules hardcoded #fff and sat at 2.87."""
    r = contrast(palette["--accent-text"], palette["--accent"])
    assert r >= AA_NORMAL, f"{name}: --accent-text on --accent = {r:.2f}"


def test_no_rule_hardcodes_white_on_the_accent_fill():
    src = CSS.read_text(encoding="utf-8")
    bad = re.findall(r"background:\s*var\(--accent\);\s*color:\s*#fff", src, re.IGNORECASE)
    assert not bad, (
        f"{len(bad)} rule(s) paint white on the accent fill instead of var(--accent-text); "
        "that is unreadable in whichever theme has a light accent"
    )
