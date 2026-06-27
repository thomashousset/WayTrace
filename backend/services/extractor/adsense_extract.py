"""Extractor for Google Adsense publisher and ad-slot IDs."""
from __future__ import annotations

from .patterns import ADSENSE_PUB_RE, ADSENSE_SLOT_RE


def extract_adsense_ids(html: str) -> list[dict]:
    """Return a deduplicated list of Adsense IDs found in *html*.

    Each entry is a dict with keys:
      - ``type``: ``"adsense_publisher"`` or ``"ad_slot"``
      - ``id``:   the raw numeric/prefixed ID string
    """
    # Publisher IDs (ca-pub-…) and slot IDs (data-ad-slot=…) share the
    # same numeric format but live in different keyspaces. A previous
    # single `seen` set silently dropped a slot whose numeric suffix
    # happened to collide with a publisher ID. Dedupe per type.
    seen_pub: set[str] = set()
    seen_slot: set[str] = set()
    results: list[dict] = []

    for match in ADSENSE_PUB_RE.finditer(html):
        pub_id = match.group(1)
        if pub_id not in seen_pub:
            seen_pub.add(pub_id)
            results.append({"type": "adsense_publisher", "id": pub_id})

    for match in ADSENSE_SLOT_RE.finditer(html):
        slot_id = match.group(1)
        if slot_id not in seen_slot:
            seen_slot.add(slot_id)
            results.append({"type": "ad_slot", "id": slot_id})

    return results
