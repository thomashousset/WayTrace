"""Build a standalone HTML snapshot of a scan.

The frontend bundle is monolithic (frontend/index.html). We inject the
scan data as a `<script>window.__WAYTRACE_PRELOAD__ = {...};</script>`
right before `</head>`. The frontend JS detects this preload and skips
the API round-trip, rendering directly from the inlined data.

The resulting HTML is self-contained: open it anywhere, no server needed.
"""
from __future__ import annotations

import json
from pathlib import Path

_FRONTEND_PATH = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "index.html"
)


def _safe_json(obj) -> str:
    """JSON encoding safe for inclusion in a <script> tag.

    Escapes ``</`` sequences so a domain like ``</script><script>alert(1)//``
    cannot break out of the script context.
    """
    return json.dumps(obj, default=str, ensure_ascii=False).replace("</", "<\\/")


def build_standalone_html(job: dict, *, frontend_path: Path | None = None) -> str:
    """Return a complete HTML page with the scan data inlined."""
    path = frontend_path or _FRONTEND_PATH
    html = path.read_text(encoding="utf-8")
    inline = f"<script>window.__WAYTRACE_PRELOAD__ = {_safe_json(job)};</script>"
    if "</head>" in html:
        html = html.replace("</head>", f"{inline}</head>", 1)
    else:
        html = inline + html
    return html
