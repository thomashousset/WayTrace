"""Extract the client IP from a Starlette/FastAPI request, spoof-resistant.

X-Real-IP is set by the reverse proxy in front (which overwrites any
client-supplied value with the real remote host), so it cannot be forged. It
is therefore the trust anchor. CF-Connecting-IP and X-Forwarded-For are only meaningful when
a known proxy (Cloudflare) sits in front; they are client-forgeable otherwise,
so they are gated behind settings.trust_cloudflare to keep a direct client from
faking its IP to bypass the per-IP scan caps.
"""
from __future__ import annotations

import ipaddress

from config import settings


def rate_key(value: str) -> str:
    """The identity a per-IP limit should count against.

    An IPv6 client is handed a whole /64 by its provider and can pick any
    address inside it, so counting full addresses gave one person billions of
    fresh buckets. The /64 is what one subscriber controls; it is the IPv6
    equivalent of one IPv4 address. Anything that is not an IPv6 address (an
    IPv4, an email, a user id) comes back unchanged.
    """
    try:
        addr = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return value
    if addr.version == 6 and addr.ipv4_mapped is None:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    if addr.version == 6:
        return str(addr.ipv4_mapped)
    return value


def _header(headers, name: str) -> str | None:
    value = headers.get(name) if hasattr(headers, "get") else None
    return value.split(",")[0].strip() if value else None


def get_client_ip(request) -> str:
    headers = getattr(request, "headers", {}) or {}
    if settings.trust_cloudflare:
        # Cloudflare fronts the app: the real client is in CF-Connecting-IP.
        for name in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
            ip = _header(headers, name)
            if ip:
                return ip
    else:
        # Trust only the value our reverse proxy set; ignore forgeable ones.
        ip = _header(headers, "X-Real-IP")
        if ip:
            return ip
    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return client.host
    return "0.0.0.0"
