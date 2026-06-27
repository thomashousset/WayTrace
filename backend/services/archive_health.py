"""Process-wide circuit breaker for archive.org.

If archive.org starts timing out or rate-limiting (429), we MUST stop hitting it
rather than retry into the wall - hammering a slow archive.org is what gets the
server IP throttled or banned. After a few failures in a short window the breaker
"opens": every archive.org call then fails fast (no network request) for a
cooldown, letting the IP recover. A single success closes it again.

Shared by the CDX client and the scraper so one signal calms the whole app.
"""
from __future__ import annotations

import threading
import time

_FAIL_WINDOW = 120        # seconds: failures are counted within this window
_FAIL_THRESHOLD = 3       # this many failures trips the breaker
_COOLDOWN = 300           # seconds the breaker stays open (no calls allowed)

# "Slow mode" is advisory UX only (the breaker is the real ban protection). It
# must reflect SUSTAINED slowness, not archive.org's normal per-request jitter:
# the same CDX query routinely answers in 3s / 15s / 50s while archive.org is
# perfectly fine overall. So we look at the MEDIAN of several recent calls, not
# the last single one - one spike no longer trips the banner.
_SLOW_LATENCY = 8.0       # seconds: median this slow over recent calls = "slow"
_SLOW_MIN_SAMPLES = 3     # need at least this many recent calls before judging
_LAT_WINDOW = 300         # seconds: latency samples older than this are ignored
_LAT_MAX_SAMPLES = 12     # keep at most this many recent samples
_SLOW_FAIL_THRESHOLD = 2  # this many recent failures also reads as "slow"

_lock = threading.Lock()
_fails: list[float] = []
_open_until: float = 0.0
_last_latency: float = 0.0
_last_latency_at: float = 0.0
_latencies: list[tuple[float, float]] = []  # (timestamp, seconds), recent only


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def is_open() -> bool:
    """True when archive.org is in cooldown - callers must NOT make a request."""
    with _lock:
        return time.time() < _open_until


def seconds_remaining() -> int:
    with _lock:
        return max(0, int(_open_until - time.time()))


def record_failure() -> None:
    """Log a timeout / 429 / 5xx. Trips the breaker once the threshold is hit."""
    global _open_until
    now = time.time()
    with _lock:
        _fails[:] = [t for t in _fails if t > now - _FAIL_WINDOW]
        _fails.append(now)
        if len(_fails) >= _FAIL_THRESHOLD:
            _open_until = now + _COOLDOWN
            _fails.clear()


def record_success() -> None:
    """A clean response: clear the failure streak and close the breaker."""
    global _open_until
    with _lock:
        _fails.clear()
        _open_until = 0.0


def record_latency(seconds: float) -> None:
    """Record how long an archive.org call took (drives 'slow mode')."""
    global _last_latency, _last_latency_at
    now = time.time()
    with _lock:
        _last_latency = seconds
        _last_latency_at = now
        _latencies.append((now, seconds))
        cutoff = now - _LAT_WINDOW
        kept = [x for x in _latencies if x[0] > cutoff]
        _latencies[:] = kept[-_LAT_MAX_SAMPLES:]


def status() -> dict:
    """Public-facing health: ok / slow / paused, with a user message."""
    with _lock:
        now = time.time()
        cooldown = max(0, int(_open_until - now))
        recent_fail = len([t for t in _fails if t > now - _FAIL_WINDOW])
        fresh = [sec for (t, sec) in _latencies if t > now - _LAT_WINDOW]
        lat = _last_latency if (now - _last_latency_at) < _LAT_WINDOW else 0.0
    med = _median(fresh)
    if cooldown > 0:
        return {
            "state": "paused", "cooldown_remaining": cooldown, "last_latency": round(lat, 1),
            "message": (f"Scanning is paused for about {cooldown}s: archive.org is rate-limiting us. "
                        "Please retry in a moment."),
        }
    # Sustained-slow only: a slow MEDIAN over >= _SLOW_MIN_SAMPLES recent calls,
    # or a run of failures. One jittery spike (a single 15-50s CDX call while
    # archive.org is otherwise fine) must not trip the banner.
    sustained_slow = len(fresh) >= _SLOW_MIN_SAMPLES and med >= _SLOW_LATENCY
    if recent_fail >= _SLOW_FAIL_THRESHOLD or sustained_slow:
        sev = "very slow" if med >= _SLOW_LATENCY * 2 else "slow"
        return {
            "state": "slow", "cooldown_remaining": 0, "last_latency": round(med or lat, 1),
            "message": f"Archive.org is {sev} right now; scans may take longer than usual.",
        }
    return {"state": "ok", "cooldown_remaining": 0, "last_latency": round(lat, 1), "message": ""}


def latency_series() -> list[dict]:
    """Recent archive.org latency samples for the monitoring sparkline:
    [{"age": seconds-ago, "s": latency}], oldest first, within the window."""
    now = time.time()
    with _lock:
        return [
            {"age": int(now - ts), "s": round(sec, 2)}
            for (ts, sec) in _latencies if ts > now - _LAT_WINDOW
        ]


def snapshot() -> dict:
    with _lock:
        now = time.time()
        return {
            "open": now < _open_until,
            "cooldown_remaining": max(0, int(_open_until - now)),
            "recent_failures": len([t for t in _fails if t > now - _FAIL_WINDOW]),
            "last_latency": round(_last_latency if (now - _last_latency_at) < 600 else 0.0, 1),
        }
