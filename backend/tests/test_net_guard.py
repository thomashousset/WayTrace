"""SSRF guard: private-IP classification + archive-host redirect allowlist.

The scraper/favicon fetchers must follow archive.org's same-host nearest-capture
302s, so redirects can't just be disabled. guarded_wayback_get follows them
manually but ONLY to archive.org hosts, and GuardedResolver refuses hostnames
that resolve to private addresses. These tests pin both behaviours with a fake
session (no network).
"""
import pytest

from services.net_guard import (
    BlockedAddressError,
    guarded_wayback_get,
    is_archive_host,
    is_public_ip,
)


def test_is_public_ip_classifies_reserved_ranges():
    assert is_public_ip("8.8.8.8")
    assert is_public_ip("207.241.224.2")   # archive.org space
    for bad in ["169.254.169.254", "127.0.0.1", "10.1.2.3", "192.168.0.1",
                "172.16.0.1", "::1", "fd00::1", "0.0.0.0", "not-an-ip"]:
        assert not is_public_ip(bad), bad


def test_is_archive_host_allowlist():
    assert is_archive_host("https://web.archive.org/web/1id_/http://x/")
    assert is_archive_host("http://archive.org/")
    assert is_archive_host("https://analytics.archive.org/")
    for bad in ["http://169.254.169.254/latest/meta-data/",
                "http://web.archive.org.evil.com/",
                "http://archive.org.attacker.net/",
                "http://127.0.0.1/", "file:///etc/passwd"]:
        assert not is_archive_host(bad), bad


# --- fake aiohttp session that scripts a sequence of responses -------------

class _FakeResp:
    def __init__(self, status, headers=None, body=b"ok"):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.url = None
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def read(self):
        return self._body


class _FakeSession:
    def __init__(self, script):
        # script: dict url -> _FakeResp (or list for sequential)
        self.script = script
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(url)
        r = self.script[url]
        r.url = url
        return r


@pytest.mark.asyncio
async def test_follows_same_host_archive_redirect():
    start = "https://web.archive.org/web/20200101id_/http://x.com/"
    nearest = "https://web.archive.org/web/20191231id_/http://x.com/"
    sess = _FakeSession({
        start: _FakeResp(302, {"Location": nearest}),
        nearest: _FakeResp(200, {}, b"<html>page</html>"),
    })
    async with guarded_wayback_get(sess, start) as resp:
        assert resp.status == 200
        assert await resp.read() == b"<html>page</html>"
    assert sess.calls == [start, nearest]   # followed the one same-host hop


@pytest.mark.asyncio
async def test_refuses_cross_host_redirect_to_metadata():
    start = "https://web.archive.org/web/20200101id_/http://evil.com/"
    sess = _FakeSession({
        start: _FakeResp(302, {"Location": "http://169.254.169.254/latest/meta-data/"}),
    })
    with pytest.raises(BlockedAddressError):
        async with guarded_wayback_get(sess, start) as resp:
            pass
    assert sess.calls == [start]   # never issued the request to the metadata IP


@pytest.mark.asyncio
async def test_refuses_relative_redirect_that_escapes_to_other_host():
    # A protocol-relative Location that resolves off-archive must be refused.
    start = "https://web.archive.org/web/1id_/http://evil.com/"
    sess = _FakeSession({start: _FakeResp(302, {"Location": "//127.0.0.1/x"})})
    with pytest.raises(BlockedAddressError):
        async with guarded_wayback_get(sess, start) as resp:
            pass


@pytest.mark.asyncio
async def test_non_redirect_response_passes_through():
    start = "https://web.archive.org/web/1id_/http://x.com/"
    sess = _FakeSession({start: _FakeResp(200, {}, b"body")})
    async with guarded_wayback_get(sess, start) as resp:
        assert resp.status == 200
    assert sess.calls == [start]
