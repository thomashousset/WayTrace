"""Directory listing detection in archived HTML pages (passive)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

DIRLIST_PATTERNS = [
    (re.compile(r"<h1>\s*Index of\s+/", re.IGNORECASE), "apache"),
    (re.compile(r"<title>\s*Index of\s+/", re.IGNORECASE), "apache"),
    (re.compile(r"Directory listing for\s+/", re.IGNORECASE), "python"),
    (re.compile(r'<a[^>]*>\s*Parent Directory\s*</a>', re.IGNORECASE), "generic"),
]

SECONDARY_PATTERNS = [
    re.compile(r"<th[^>]*>\s*Name\s*</th>\s*<th[^>]*>\s*Last modified", re.IGNORECASE),
    re.compile(r"<th[^>]*>\s*Name</a>\s*</th>", re.IGNORECASE),
]


def detect_directory_listing(html: str, page_url: str, timestamp: str) -> dict | None:
    path = urlparse(page_url).path or "/"
    server_type = None
    for pattern, stype in DIRLIST_PATTERNS:
        if pattern.search(html):
            server_type = stype
            break
    if server_type:
        return {"path": path, "server_type": server_type, "timestamp": timestamp, "url": page_url}
    for pattern in SECONDARY_PATTERNS:
        if pattern.search(html):
            return {"path": path, "server_type": "unknown", "timestamp": timestamp, "url": page_url}
    return None
