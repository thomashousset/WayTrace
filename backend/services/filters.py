from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from loguru import logger

if TYPE_CHECKING:
    from models import ScanConfig

# Paths with high OSINT value ; diverse content likely
HIGH_PRIORITY_KEYWORDS = {
    "contact", "about", "team", "staff", "people", "privacy", "terms",
    "careers", "legal", "imprint", "impressum", "login", "admin", "blog",
    "jobs", "press", "partners", "investors", "security",
}

# Depth preset multipliers. 'max' is the completeness-first mode:
# skip the content-digest dedup and lift the cap to whatever the domain
# actually has (bounded at 10000 to protect memory). Intended for
# incident response / deep-dive investigations where missing a
# snapshot has real cost.
DEPTH_PRESETS = {
    "quick":    {"cap_mult": 0.15, "min_cap": 200},
    "standard": {"cap_mult": 1.0, "min_cap": 1},
    "full":     {"cap_mult": 2.0, "max_cap": 30000},
    "max":      {"cap_mult": 50.0, "min_cap": 5000, "max_cap": 10000},
}


# Auto-depth thresholds. operate on the raw CDX record count returned by
# ``cdx_size_probe`` (pages × 3000 approximate). The post-filter
# (status=200, mimetype=text/html) snapshot count is typically 10-30% of
# the raw, so a 100k-record probe maps to ~10-30k actual HTML snapshots.
# Goal: keep wall-clock under ~3 min for the median scan, with bigger
# domains explicitly truncating rather than spinning forever.
_AUTO_DEPTH_TABLE: list[tuple[int, str, int]] = [
    # (records_upper_bound, depth_preset, download_cap)
    (5_000,     "max",      500),    # tiny
    (20_000,    "full",     800),    # small
    (100_000,   "standard", 500),    # medium
    (1_000_000, "quick",    300),    # big
    # Anything larger falls into the catch-all below.
]
_AUTO_DEPTH_FALLBACK: tuple[str, int] = ("quick", 200)


def auto_depth(estimated_records: int, *, force_thorough: bool = False) -> tuple[str, int]:
    """Pick a (depth, cap) pair for a given CDX-record estimate.

    *estimated_records* is ``cdx_size_probe()['estimated_records']``.

    *force_thorough* is the "rescan thorough" escape hatch surfaced by the
    truncation banner in the UI: lifts the depth one tier (max records get
    'full' instead of 'quick', etc.) so the user can opt into a heavier
    crawl after seeing partial coverage.
    """
    if estimated_records <= 0:
        # Probe failed or returned 0. pick a safe default.
        return ("standard", 500)

    for upper, depth, cap in _AUTO_DEPTH_TABLE:
        if estimated_records < upper:
            picked = (depth, cap)
            break
    else:
        picked = _AUTO_DEPTH_FALLBACK

    if force_thorough:
        # Lift one tier toward 'max'.
        depth_order = ["quick", "standard", "full", "max"]
        idx = depth_order.index(picked[0])
        bumped = depth_order[min(idx + 1, len(depth_order) - 1)]
        return (bumped, picked[1])

    return picked


def _compute_cap(unique_paths: int, html_count: int = 0) -> int:
    """Adaptive cap based on unique paths and available HTML snapshots.

    Tuned so a standard scan stays under ~500 pages for typical sites and
    under ~1500 for very large ones. Two or three snapshots per path is
    already enough to catch most temporal changes. the dedup pass on
    (path, digest) removes the rest.
    """
    if unique_paths <= 30:
        # Small site: cap at 100, no need to fetch everything
        base = min(html_count, 100)
    elif unique_paths <= 200:
        # Medium: ~3 snapshots per path, max 500
        base = min(unique_paths * 3, 500)
    elif unique_paths <= 1000:
        # Large: ~1.5 per path, max 1000
        base = min(int(unique_paths * 1.5), 1000)
    else:
        # Very large: flat 1500
        base = 1500

    # Never go below a reasonable minimum
    return max(base, min(html_count, 50))


def _normalize_path(url: str) -> str:
    """Extract and normalize the URL path."""
    try:
        parsed = urlparse(url)
        path = (parsed.path or "/").rstrip("/").lower() or "/"
        return path
    except ValueError:
        return "/"


def _score_path(path: str) -> int:
    """Score a path by OSINT value. Higher = more interesting."""
    path_lower = path.lower()
    for kw in HIGH_PRIORITY_KEYWORDS:
        if kw in path_lower:
            return 3  # high
    if path == "/":
        return 2  # medium (homepage)
    return 1  # low


def _apply_date_filter(snapshots: list[dict], config: ScanConfig | None) -> list[dict]:
    """Filter snapshots by date_from/date_to from config."""
    if config is None:
        return snapshots
    filtered = snapshots
    if config.date_from:
        # date_from is "YYYY-MM", timestamp is "YYYYMMDDhhmmss"
        from_ts = config.date_from.replace("-", "") + "01000000"
        filtered = [s for s in filtered if s["timestamp"] >= from_ts]
    if config.date_to:
        # date_to is "YYYY-MM", include the full month
        to_ts = config.date_to.replace("-", "") + "31235959"
        filtered = [s for s in filtered if s["timestamp"] <= to_ts]
    return filtered


def _apply_depth_to_cap(cap: int, config: ScanConfig | None) -> int:
    """Apply depth preset multiplier to cap."""
    if config is None:
        return cap
    preset = DEPTH_PRESETS.get(config.depth, DEPTH_PRESETS["standard"])
    adjusted = int(cap * preset["cap_mult"])
    if "min_cap" in preset:
        adjusted = max(adjusted, preset["min_cap"])
    if "max_cap" in preset:
        adjusted = min(adjusted, preset["max_cap"])
    return adjusted


def _evenly_spaced(items: list[dict], n: int) -> list[dict]:
    """Return *n* items spaced evenly across the original list order.

    Assumes *items* is sorted by timestamp. This gives temporal coverage
    rather than "first N" or "last N" bias. essential for capturing
    changes in analytics trackers, tech stack, favicons over time.
    """
    k = len(items)
    if n >= k:
        return list(items)
    if n <= 0:
        return []
    # Pick indices that cover start, end, and interior evenly.
    step = (k - 1) / (n - 1) if n > 1 else 0
    picked: list[dict] = []
    seen: set[int] = set()
    for i in range(n):
        idx = round(i * step)
        if idx in seen:
            # Under rounding degeneracy, advance to next free slot
            idx = next((j for j in range(idx, k) if j not in seen), idx)
        seen.add(idx)
        picked.append(items[idx])
    return picked


def _allocate_budget_by_score(selected: list[dict], cap: int) -> list[dict]:
    """Allocate *cap* snapshots across path groups, weighted by path score.

    The cap is distributed so higher-OSINT-value paths (/admin, /login,
    /contact …) get more temporal coverage than long-tail paths, even
    when the long tail dominates by snapshot count. Within each path,
    picks are evenly spaced across the timeline.

    Uses the Hamilton (largest remainder) method for rounding so the
    total budget is hit exactly without drift.
    """
    if cap >= len(selected):
        return selected

    # Group by path, preserving the (already timestamp-sorted) order.
    groups: dict[str, list[dict]] = {}
    for snap in selected:
        path = _normalize_path(snap["url"])
        groups.setdefault(path, []).append(snap)

    paths = list(groups)

    # When the cap is tighter than the number of unique paths we can't
    # give each path at least one representative. Score-sort the paths
    # and take the top `cap` paths, one snapshot each (the earliest
    # timestamp is arbitrary. keep the first in time).
    if cap < len(paths):
        ranked = sorted(
            paths,
            key=lambda p: (-_score_path(p), -len(groups[p]), p),
        )
        picked: list[dict] = [groups[p][0] for p in ranked[:cap]]
        picked.sort(key=lambda s: s["timestamp"])
        return picked

    # Otherwise run the weighted allocation. Raw weight = path_score ×
    # len(group) so a long-tail path still gets proportional coverage
    # while /admin gets its score multiplier boost.
    weights: dict[str, float] = {
        path: _score_path(path) * len(items) for path, items in groups.items()
    }
    total_weight = sum(weights.values()) or 1.0

    floats: dict[str, float] = {}
    floors: dict[str, int] = {}
    for path, w in weights.items():
        raw = cap * w / total_weight
        # Floor of at least 1. every path in the weighted branch is
        # guaranteed at least one representative (we've checked the
        # cap ≥ len(paths) invariant above).
        f = max(1, int(raw))
        floats[path] = raw
        floors[path] = f

    assigned = sum(floors.values())
    remainder = cap - assigned
    if remainder > 0:
        fracs = sorted(
            ((floats[p] - floors[p], p) for p in paths),
            reverse=True,
        )
        for _, p in fracs[:remainder]:
            floors[p] += 1
    elif remainder < 0:
        surplus = -remainder
        by_low = sorted(paths, key=lambda p: weights[p])
        for p in by_low:
            while surplus > 0 and floors[p] > 1:
                floors[p] -= 1
                surplus -= 1
            if surplus == 0:
                break

    # A path can have fewer snapshots than its allocated floor. clamp and
    # redistribute the spare budget to paths that still have headroom,
    # ranked by fractional share. Repeat until stable or no donor has slack.
    def _clamp_and_redistribute() -> None:
        while True:
            spare = 0
            for p in paths:
                avail = len(groups[p])
                if floors[p] > avail:
                    spare += floors[p] - avail
                    floors[p] = avail
            if spare == 0:
                break
            donors = sorted(
                (
                    (floats[p] - floors[p], p)
                    for p in paths
                    if floors[p] < len(groups[p])
                ),
                reverse=True,
            )
            if not donors:
                break
            bumped = 0
            for _, p in donors:
                if bumped >= spare:
                    break
                floors[p] += 1
                bumped += 1
            if bumped == 0:
                break

    _clamp_and_redistribute()

    picked: list[dict] = []
    for path, items in groups.items():
        picked.extend(_evenly_spaced(items, floors[path]))
    picked.sort(key=lambda s: s["timestamp"])
    return picked


# Minimum snapshots kept per archived year when the budget is tight. Keeps
# the timeline honest: a sparse early year still shows up instead of being
# crowded out by a recent year that dominates by volume.
_YEAR_FLOOR = 3


def _allocate_budget_by_year(
    selected: list[dict], cap: int, floor: int = _YEAR_FLOOR
) -> list[dict]:
    """Distribute *cap* snapshots across calendar years, proportional to each
    year's volume but with a per-year floor, then pick within each year by
    path score (``_allocate_budget_by_score``).

    Why two levels: proportional-by-year gives temporal coverage without a
    recency bias (a year with 8000 captures doesn't eat the whole budget),
    while the floor guarantees rare/old years stay represented. Inside a year
    the existing score allocation still favours high-OSINT paths.
    """
    if cap >= len(selected):
        return selected

    years: dict[str, list[dict]] = {}
    for snap in selected:
        years.setdefault(snap["timestamp"][:4], []).append(snap)
    if len(years) <= 1:
        return _allocate_budget_by_score(selected, cap)

    ykeys = sorted(years)
    counts = {y: len(years[y]) for y in ykeys}
    alloc = {y: 0 for y in ykeys}

    # Pass 1: reserve the floor per year (oldest first, so early history is
    # never the part that gets dropped when the cap can't cover every floor).
    budget = cap
    for y in ykeys:
        give = min(counts[y], floor, budget)
        alloc[y] = give
        budget -= give
        if budget <= 0:
            break

    # Pass 2: hand out the rest proportional to each year's remaining headroom
    # (Hamilton / largest-remainder so the total lands exactly on the budget).
    if budget > 0:
        headroom = {y: counts[y] - alloc[y] for y in ykeys}
        total_hr = sum(headroom.values())
        if total_hr > 0:
            ideal = {y: budget * headroom[y] / total_hr for y in ykeys}
            base = {y: min(headroom[y], int(ideal[y])) for y in ykeys}
            for y in ykeys:
                alloc[y] += base[y]
            budget -= sum(base.values())
            if budget > 0:
                fr = sorted(
                    ((ideal[y] - int(ideal[y]), y) for y in ykeys if alloc[y] < counts[y]),
                    reverse=True,
                )
                for _, y in fr:
                    if budget <= 0:
                        break
                    alloc[y] += 1
                    budget -= 1

    picked: list[dict] = []
    for y in ykeys:
        if alloc[y] > 0:
            picked.extend(_allocate_budget_by_score(years[y], alloc[y]))
    picked.sort(key=lambda s: s["timestamp"])
    return picked


def filter_snapshots(snapshots: list[dict], config: ScanConfig | None = None) -> dict:
    html_only = [s for s in snapshots if s.get("mimetype") == "text/html"]

    # Keyword blacklist: drop URLs containing any excluded substring before
    # anything else, so the cap budget is spent only on wanted pages.
    if config and config.exclude_keywords:
        kws = config.exclude_keywords
        html_only = [
            s for s in html_only
            if not any(kw in (s.get("url") or "").lower() for kw in kws)
        ]

    # Apply date filtering before anything else
    html_only = _apply_date_filter(html_only, config)

    if not html_only:
        return {
            "selected": [],
            "total_snapshots_found": len(snapshots),
            "snapshots_selected": 0,
            "pages_deduped": 0,
            "date_first_seen": None,
            "date_last_seen": None,
        }

    html_only.sort(key=lambda s: s["timestamp"])

    first = html_only[0]
    last = html_only[-1]

    selected = list(html_only)
    dedup_saved = 0

    # In 'max' mode we skip digest dedup even if the user didn't disable
    # it explicitly. the preset's whole point is "don't drop anything".
    is_max_mode = config is not None and config.depth == "max"
    smart_dedup = (config is None) or (config.smart_dedup and not is_max_mode)
    if smart_dedup:
        seen_content: dict[tuple, None] = {}
        deduped: list[dict] = []
        for snap in selected:
            digest = snap.get("digest")
            if digest:
                path = _normalize_path(snap["url"])
                key = (path, digest)
                if key in seen_content:
                    continue
                seen_content[key] = None
            deduped.append(snap)
        dedup_saved = len(selected) - len(deduped)
        selected = deduped

    cap = None
    if config and config.cap is not None:
        cap = config.cap
    elif config and config.depth == "quick":
        cap = 500
    elif is_max_mode:
        # 'max' preset: keep everything up to the upper bound set by the
        # preset (DEPTH_PRESETS['max']['max_cap']).
        cap = DEPTH_PRESETS["max"]["max_cap"]

    if cap is not None and len(selected) > cap:
        # Year-proportional + floor (temporal coverage, no recency bias),
        # with the score allocation applied within each year.
        selected = _allocate_budget_by_year(selected, cap)

    if dedup_saved > 0:
        logger.info(
            "Content dedup removed {} duplicate snapshots ({} → {})",
            dedup_saved,
            dedup_saved + len(selected),
            len(selected),
        )

    date_first = f"{first['timestamp'][:4]}-{first['timestamp'][4:6]}"
    date_last = f"{last['timestamp'][:4]}-{last['timestamp'][4:6]}"

    unique_paths = len({_normalize_path(s["url"]) for s in html_only})
    logger.info(
        "Filtered {} HTML snapshots down to {} across {} unique paths (range: {} to {})",
        len(html_only),
        len(selected),
        unique_paths,
        date_first,
        date_last,
    )

    return {
        "selected": selected,
        "total_snapshots_found": len(snapshots),
        "snapshots_selected": len(selected),
        "pages_deduped": dedup_saved,
        "date_first_seen": date_first,
        "date_last_seen": date_last,
    }


async def select_snapshots_in_db(
    domain_id: int,
    db_path: str,
    depth: str = "standard",
    date_from: str | None = None,
    date_to: str | None = None,
    smart_dedup: bool = True,
) -> dict:
    """Mark HTML snapshots as selected in the database and create pending page rows."""
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE snapshots SET selected = 0 WHERE domain_id = ?", (domain_id,))

        query = (
            "SELECT id, url, timestamp, digest FROM snapshots "
            "WHERE domain_id = ? AND mimetype = 'text/html'"
        )
        params_list = [domain_id]

        if date_from:
            ts_from = date_from.replace("-", "") + "01000000"
            query += " AND timestamp >= ?"
            params_list.append(ts_from)
        if date_to:
            ts_to = date_to.replace("-", "") + "31235959"
            query += " AND timestamp <= ?"
            params_list.append(ts_to)

        query += " ORDER BY timestamp"
        cursor = await db.execute(query, tuple(params_list))
        rows = await cursor.fetchall()

        if not rows:
            return {"selected_count": 0, "deduped_count": 0}

        snapshots = [{"id": r[0], "url": r[1], "timestamp": r[2], "digest": r[3]} for r in rows]

        deduped_count = 0
        if smart_dedup:
            seen = set()
            filtered = []
            for s in snapshots:
                path = urlparse(s["url"]).path or "/"
                key = (path, s["digest"])
                if key in seen:
                    deduped_count += 1
                    continue
                seen.add(key)
                filtered.append(s)
            snapshots = filtered

        unique_paths = {urlparse(s["url"]).path or "/" for s in snapshots}
        cap = _compute_cap(len(unique_paths), len(snapshots))

        preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"])
        final_cap = max(int(cap * preset["cap_mult"]), preset.get("min_cap", 1))
        if "max_cap" in preset:
            final_cap = min(final_cap, preset["max_cap"])

        scored = []
        for s in snapshots:
            path = urlparse(s["url"]).path or "/"
            score = _score_path(path)
            scored.append((score, s))
        scored.sort(key=lambda x: -x[0])

        selected = [s for _, s in scored[:final_cap]]

        ids = [s["id"] for s in selected]
        if ids:
            placeholders = ",".join("?" * len(ids))
            await db.execute(
                f"UPDATE snapshots SET selected = 1 WHERE id IN ({placeholders})", ids
            )
            for snap_id in ids:
                await db.execute(
                    "INSERT OR IGNORE INTO pages (snapshot_id, status) VALUES (?, 'pending')",
                    (snap_id,),
                )

        await db.commit()

    return {"selected_count": len(selected), "deduped_count": deduped_count}
