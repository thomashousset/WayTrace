"""Extract the client IP from a Starlette/FastAPI request.

Priority order:
1. CF-Connecting-IP   - set by Cloudflare when proxying (the real client IP)
2. X-Real-IP          - set by Caddy reverse proxy
3. X-Forwarded-For    - generic fallback; we take the leftmost (origin) value
4. request.client.host - direct connection (local dev, no proxy)
"""
from __future__ import annotations


def get_client_ip(request) -> str:
    headers = getattr(request, "headers", {}) or {}
    for header in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
        value = headers.get(header) if hasattr(headers, "get") else None
        if value:
            return value.split(",")[0].strip()
    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return client.host
    return "0.0.0.0"
