"""Extractor for advertising publisher / ad-slot IDs.

Recognized patterns (each kept with its exact prefix so the value can be
pivoted on Shodan/Censys and displayed precisely):

  - ``adsense_publisher``  Google AdSense publisher   ``ca-pub-<10-16 digits>``
  - ``admob``              Google AdMob app publisher  ``ca-app-pub-<10-16 digits>``
  - ``ad_slot``            AdSense ad slot             ``data-ad-slot="<digits>"``

Google Ads conversion tags (``AW-...``) and the Meta/Facebook Pixel (``fbq``)
are captured as trackers in ``patterns.TRACKER_PATTERNS`` and surface under the
Analytics & trackers category, so they are not duplicated here.
"""
from __future__ import annotations

from .patterns import ADMOB_RE, ADSENSE_PUB_RE, ADSENSE_SLOT_RE


def extract_adsense_ids(html: str) -> list[dict]:
    """Return a deduplicated list of advertising IDs found in *html*.

    Each entry is a dict with ``type`` (network label) and ``id`` (the raw
    prefixed identifier, e.g. ``ca-pub-1234567890123456``).
    """
    # Dedupe per (type, id): a publisher and a slot can share a numeric suffix,
    # so a single seen-set would silently drop one of them.
    seen: set[tuple[str, str]] = set()
    results: list[dict] = []

    def _add(kind: str, value: str) -> None:
        key = (kind, value)
        if value and key not in seen:
            seen.add(key)
            results.append({"type": kind, "id": value})

    # AdMob's "ca-app-pub-" is distinct from AdSense's "ca-pub-" (no overlap).
    for match in ADMOB_RE.finditer(html):
        _add("admob", match.group(0))
    for match in ADSENSE_PUB_RE.finditer(html):
        _add("adsense_publisher", match.group(0))
    for match in ADSENSE_SLOT_RE.finditer(html):
        _add("ad_slot", match.group(1))

    return results
