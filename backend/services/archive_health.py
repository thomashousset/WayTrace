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

from config import settings

_FAIL_WINDOW = 120        # seconds: failures are counted within this window
_FAIL_THRESHOLD = 5       # this many soft failures (timeout/429/5xx) trips it
_COOLDOWN = 180           # seconds the breaker stays open after a soft trip

# A "hard block" is a TCP-level connection REFUSAL - the signature of archive.org
# firewalling our IP, not mere throttling. It needs a very different response: a
# refused IP will not recover in 3 minutes, and every retry only confirms the
# block, so we trip fast and stay closed for a long cooldown.
_HARD_FAIL_THRESHOLD = 2  # this many refusals in the window = "we are blocked"
# Hard-block cooldown is now dynamic (escalating) - see settings.archive_hard_*
# and record_hard_block(): short on a first/isolated refusal, longer only when
# refusals recur close together.

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
_hard_fails: list[float] = []
_open_until: float = 0.0
_tripped_hard: bool = False
_hard_streak: int = 0            # consecutive hard-block trips (drives cooldown escalation)
_last_hard_trip_at: float = 0.0
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


def is_hard_block() -> bool:
    """True when the breaker is open because of a hard IP block (connection
    refused), as opposed to soft throttling. Callers use this to abort rather
    than wait: a refused IP will not recover within a scan's lifetime."""
    with _lock:
        return _tripped_hard and time.time() < _open_until


def record_failure() -> None:
    """Log a soft failure (timeout / 429 / 5xx). Trips after _FAIL_THRESHOLD."""
    global _open_until
    now = time.time()
    with _lock:
        _fails[:] = [t for t in _fails if t > now - _FAIL_WINDOW]
        _fails.append(now)
        if len(_fails) >= _FAIL_THRESHOLD:
            _open_until = now + _COOLDOWN
            _fails.clear()


def record_hard_block() -> None:
    """Log a connection REFUSAL (TCP reject) - the signature of an IP block.
    Trips after _HARD_FAIL_THRESHOLD refusals. The cooldown ESCALATES only when
    trips recur inside archive_hard_streak_reset: a first/isolated refusal
    (usually temporary rate-limiting that clears in seconds) pauses briefly;
    repeated back-to-back refusals (a real block) pause progressively longer,
    capped at archive_hard_cooldown_max."""
    global _open_until, _tripped_hard, _hard_streak, _last_hard_trip_at
    now = time.time()
    with _lock:
        _hard_fails[:] = [t for t in _hard_fails if t > now - _FAIL_WINDOW]
        _hard_fails.append(now)
        if len(_hard_fails) >= _HARD_FAIL_THRESHOLD:
            if _last_hard_trip_at and (now - _last_hard_trip_at) <= settings.archive_hard_streak_reset:
                _hard_streak += 1
            else:
                _hard_streak = 0
            cooldown = min(
                settings.archive_hard_cooldown_max,
                settings.archive_hard_cooldown_base * (2 ** _hard_streak),
            )
            _open_until = now + cooldown
            _tripped_hard = True
            _last_hard_trip_at = now
            _hard_fails.clear()
            _fails.clear()


def record_success() -> None:
    """A clean response: clear the SOFT failure streak and close a soft pause.

    It deliberately does NOT clear the hard-refusal window: an intermittent
    throttle (archive.org dropping a fraction of connections while others still
    succeed) must still accumulate toward the hard-block threshold instead of
    being reset by every good response in between - that intermittent case is
    exactly what slipped through before. The hard window ages out on its own.
    While a hard pause is in force it is left untouched so its cooldown is
    respected; once the cooldown has elapsed a success clears the hard flag."""
    global _open_until, _tripped_hard
    now = time.time()
    with _lock:
        _fails.clear()
        if now >= _open_until:
            _open_until = 0.0
            _tripped_hard = False


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
        hard = _tripped_hard
        recent_fail = len([t for t in _fails if t > now - _FAIL_WINDOW])
        fresh = [sec for (t, sec) in _latencies if t > now - _LAT_WINDOW]
        lat = _last_latency if (now - _last_latency_at) < _LAT_WINDOW else 0.0
    med = _median(fresh)
    if cooldown > 0:
        mins = max(1, round(cooldown / 60))
        msg = (
            (f"Archive.org is refusing connections from this server (it looks IP-blocked). "
             f"Scanning is paused for about {mins} min to let it recover.")
            if hard else
            (f"Scanning is paused for about {cooldown}s: archive.org is rate-limiting us. "
             "Please retry in a moment.")
        )
        return {
            "state": "paused", "cooldown_remaining": cooldown, "last_latency": round(lat, 1),
            "blocked": hard, "message": msg,
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
