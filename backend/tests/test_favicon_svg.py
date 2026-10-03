"""The SVG favicon carries no background, so it has to carry its own contrast.

The mark is two strokes: one in the accent orange, one in the near-white the
interface uses for bright text. Behind the opaque rounded square that used to
sit under it, both read. Without the square, on a browser tab in light mode,
the near-white stroke lands on near-white and half the mark disappears: what
is left is an orange tick, which is not the logo.

A PNG cannot do anything about that. An SVG can, because it may carry a style
sheet, and prefers-color-scheme is evaluated by the browser that paints the
tab. So the background goes, the mark grows into the margin the square used
to occupy, and the light stroke flips dark when the tab is light.

The PNG and .ico variants deliberately keep their square: apple-touch-icon
and the maskable icons are required by their platforms to be opaque, and the
small PNGs are the fallback for browsers that do not paint SVG favicons.
"""
import re
from pathlib import Path

_ICONS = Path(__file__).resolve().parents[2] / "frontend" / "icons"
_SVG = (_ICONS / "icon.svg").read_text(encoding="utf-8")


def test_the_svg_favicon_has_no_opaque_background() -> None:
    assert "<rect" not in _SVG, "the background square is what we are removing"
    # The dark tone is still in the file, as the stroke a light tab gets. What
    # must not come back is that tone used as a fill, which is what painted the
    # square.
    assert 'fill="#1C1B18"' not in _SVG, "the background fill must be gone too"


def test_the_mark_uses_the_space_the_square_left() -> None:
    """A favicon is 16 px. Margin is the one thing it cannot afford."""
    vb = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"', _SVG)
    assert vb, "the svg must declare a viewBox"
    w, h = float(vb.group(3)), float(vb.group(4))
    assert w < 100 and h < 100, (
        "the viewBox must crop to the ink, not keep the square's padding"
    )


def test_the_light_stroke_flips_dark_on_a_light_tab() -> None:
    assert "prefers-color-scheme: light" in _SVG, (
        "without this the near-white stroke vanishes on a light browser tab"
    )
    # The rule has to drive the stroke that would otherwise disappear, and the
    # accent one must be left alone: it reads on both.
    style = _SVG[_SVG.index("<style"):_SVG.index("</style>")]
    assert "#E87A48" not in style, "the accent stroke needs no adaptation"
    assert re.search(r"prefers-color-scheme:\s*light[^}]*\{[^}]*stroke:", style), (
        "the light-scheme rule must set a stroke"
    )


def test_the_platform_icons_keep_their_background() -> None:
    """iOS and Android draw their own shape around these; transparent breaks them."""
    for name in ("apple-touch-icon.png", "icon-maskable-192.png",
                 "icon-maskable-512.png"):
        assert (_ICONS / name).exists(), f"{name} must still ship"
