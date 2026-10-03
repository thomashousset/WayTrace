"""Build a standalone HTML snapshot of a scan.

The frontend is three files: index.html plus /styles.css and /app.js, both
referenced by absolute path. A downloaded copy has no server under it, so
those two references resolve to nothing and the page renders as unstyled
markup with no scan on it. We therefore inline both files into the document,
then inject the scan data as a
`<script>window.__WAYTRACE_PRELOAD__ = {...};</script>` right before
`</head>`. The frontend JS detects this preload and skips the API round-trip,
rendering directly from the inlined data.

The result really is self-contained: open it from disk, offline, no server.
The only thing that still wants the network is the webfont, which falls back
to the system sans in the same stack.
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

_FRONTEND_PATH = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "index.html"
)

_CSS_LINK = '<link rel="stylesheet" href="/styles.css">'
_JS_TAG = '<script src="/app.js"></script>'


def _safe_json(obj) -> str:
    """JSON encoding safe for inclusion in a <script> tag.

    Escapes ``</`` sequences so a domain like ``</script><script>alert(1)//``
    cannot break out of the script context.
    """
    return json.dumps(obj, default=str, ensure_ascii=False).replace("</", "<\\/")


def _neutralise(text: str, tag: str) -> str:
    """Stop an asset from closing the block it is being inlined into.

    ``</script`` inside app.js, or ``</style`` inside styles.css, ends the
    element wherever it appears, string literal or not. Backslash-escaping the
    slash keeps both files valid where the sequence could legitimately occur
    (a JS string or regex, a CSS string) and inert where it could not.
    """
    return text.replace(f"</{tag}", f"<\\/{tag}")


def _read_sibling(path: Path, name: str) -> str | None:
    try:
        return (path.parent / name).read_text(encoding="utf-8")
    except OSError as exc:
        # A packaging variant that ships index.html without its assets should
        # still get an export, just a degraded one, rather than a 500.
        logger.warning("HTML export could not inline {}: {}", name, exc)
        return None


def build_standalone_html(job: dict, *, frontend_path: Path | None = None) -> str:
    """Return a complete, self-contained HTML page with the scan data inlined."""
    path = frontend_path or _FRONTEND_PATH
    html = path.read_text(encoding="utf-8")

    css = _read_sibling(path, "styles.css")
    if css is not None and _CSS_LINK in html:
        html = html.replace(
            _CSS_LINK, f"<style>\n{_neutralise(css, 'style')}\n</style>", 1
        )
    js = _read_sibling(path, "app.js")
    if js is not None and _JS_TAG in html:
        html = html.replace(
            _JS_TAG, f"<script>\n{_neutralise(js, 'script')}\n</script>", 1
        )

    inline = f"<script>window.__WAYTRACE_PRELOAD__ = {_safe_json(job)};</script>"
    if "</head>" in html:
        html = html.replace("</head>", f"{inline}</head>", 1)
    else:
        html = inline + html
    return html
