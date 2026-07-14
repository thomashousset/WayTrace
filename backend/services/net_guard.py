"""SSRF guard for the archive.org fetchers.

WayTrace only ever talks to archive.org, but the Wayback replay endpoint returns
same-host 302s (nearest-capture normalization) that the scraper and favicon
fetchers must follow, so we cannot simply disable redirects on content fetches.
Two layers close the SSRF surface without breaking legitimate redirects:

1. `guarded_wayback_get` follows redirects MANUALLY and only to archive.org
   hosts, so a crafted archived 302 (`Location: http://169.254.169.254/...`,
   including a literal-IP target aiohttp would otherwise connect to directly)
   stops the chain instead of reaching an internal service.
2. `GuardedResolver` on the session connector refuses any hostname that resolves
   to a private / loopback / link-local / reserved address, covering
   DNS-rebinding of a hostname (the literal-IP path is handled by layer 1).
"""
from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlparse

import aiohttp

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_ARCHIVE_HOSTS = ("web.archive.org", "archive.org")


class BlockedAddressError(aiohttp.ClientConnectionError):
    """Raised when a host/redirect target is not a permitted public archive address."""


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def is_archive_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _ARCHIVE_HOSTS or host.endswith(".archive.org")


class GuardedResolver(aiohttp.abc.AbstractResolver):
    """Wraps aiohttp's default resolver and drops non-public results, so a
    hostname that (re)resolves to an internal address is refused at connect
    time. Note: aiohttp short-circuits literal IPs past the resolver, so
    literal-IP targets are handled by the redirect allowlist, not here."""

    def __init__(self) -> None:
        self._inner = aiohttp.ThreadedResolver()

    async def resolve(self, host, port=0, family=0):
        infos = await self._inner.resolve(host, port, family)
        safe = [info for info in infos if is_public_ip(info["host"])]
        if not safe:
            raise BlockedAddressError(
                f"refused connection to non-public address for host {host!r}"
            )
        return safe

    async def close(self) -> None:
        await self._inner.close()


def guarded_connector(**kwargs) -> aiohttp.TCPConnector:
    """A TCPConnector that refuses non-public addresses. Extra kwargs
    (limit, limit_per_host, keepalive_timeout, ...) pass through."""
    return aiohttp.TCPConnector(resolver=GuardedResolver(), **kwargs)


@asynccontextmanager
async def guarded_wayback_get(session: aiohttp.ClientSession, url: str, *,
                              max_redirects: int = 5, **kwargs):
    """`async with guarded_wayback_get(session, url) as resp:` — like
    `session.get`, but redirects are followed manually and only to archive.org
    hosts. A redirect to any other host raises BlockedAddressError."""
    kwargs["allow_redirects"] = False
    current = url
    for _ in range(max_redirects + 1):
        async with session.get(current, **kwargs) as resp:
            location = resp.headers.get("Location")
            if resp.status in _REDIRECT_STATUSES and location:
                nxt = urljoin(current, location)
                if not is_archive_host(nxt):
                    raise BlockedAddressError(f"refused cross-host redirect to {nxt!r}")
                current = nxt
                continue
            yield resp
            return
    raise BlockedAddressError(f"too many redirects following {url!r}")
