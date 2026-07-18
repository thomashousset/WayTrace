"""French business / training identifiers. SIREN, SIRET, TVA, RCS, Qualiopi.

High OSINT value for FR domains: lets you pivot from a website to the
INSEE / Pappers / Infogreffe legal record of the entity behind it.

SIREN / SIRET use the Luhn checksum (per INSEE specification. the
overall sum of doubled even-position digits + odd-position digits must
be divisible by 10). TVA intracommunautaire numbers are derived from the
SIREN but include two control characters (alphanumeric. Spain/Greece-
style legacy quirk for FR).

Qualiopi IDs (training-org certifications mandated since 2022) follow
the form "<YY>FOR<NNNNN>(.subindex)?" and are common on French school /
training sites. extracted as plain regex matches, no cryptographic
checksum.

The module deliberately avoids hitting any external API: WayTrace is a
strictly archive-only OSINT tool.
"""
from __future__ import annotations

import re

from .patterns import (
    QUALIOPI_RE,
    RCS_RE,
    RNCP_BARE_RE,
    RNCP_URL_RE,
    SIREN_RE,
    SIRET_RE,
    TVA_FR_RE,
)


# A bare 9-digit run satisfies Luhn roughly one time in ten by accident,
# which would emit ghost SIRENs on any English-language site that ships
# numeric identifiers. Require either French-style group separators
# (`123 456 789` or `123.456.789`) in the match itself, or a French
# legal-context keyword somewhere in the same document.
_FR_CONTEXT_RE = re.compile(
    r"\b(?:siren|siret|kbis|insee|pappers|infogreffe|qualiopi"
    r"|tva\s+intra(?:communautaire)?|rcs\s+[a-zà-ÿ]"
    r"|num[ée]ro\s+(?:de\s+)?si(?:ren|ret|rene)t?"
    r"|raison\s+sociale|si[èe]ge\s+social|immatricul(?:e|é|ée|ation)"
    r"|micro[\s\-]?entrepreneur|auto[\s\-]?entrepreneur)\b",
    re.IGNORECASE,
)


def _luhn_ok(digits: str) -> bool:
    """Return True iff *digits* satisfies the standard Luhn checksum."""
    total = 0
    # Per INSEE: walk the digits right-to-left, doubling every second one.
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _norm(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def _looks_like_placeholder(digits: str) -> bool:
    """Reject Luhn-passing strings that are obvious placeholders.

    Some test/demo SIRETs (e.g. ``99999999999969``) satisfy the Luhn
    checksum by construction yet correspond to no real entity. A digit
    sequence is treated as a placeholder when:

    * the run consists of ≤2 distinct digits (``111111111``, ``121212121``),
    * ≥80% of its characters are the same digit (``99999999999969``), OR
    * it is a periodic repeat of a 2- or 3-digit motif
      (``243243243`` / ``121212121`` / ``212121212``).

    The periodic-repeat check matters because anthropic.com's archived
    pages emitted ``212162127`` and ``243243243`` after the FR-context
    gate let them through (the page contained French translation text);
    distinct-digit count alone passes them. Real INSEE numbers don't
    share this short-period structure.
    """
    if not digits:
        return True
    distinct = set(digits)
    if len(distinct) <= 2:
        return True
    most_common = max(digits.count(d) for d in distinct)
    if most_common / len(digits) >= 0.8:
        return True
    # Periodic repeat detector: any motif of length 1-4 that fully tiles
    # the run is suspicious for a 9- or 14-digit identifier.
    n = len(digits)
    for period in range(1, 5):
        if n % period == 0 and digits[:period] * (n // period) == digits:
            return True
    return False


def _has_fr_separator(raw: str) -> bool:
    """True if *raw* contains a French-style 3-digit group separator."""
    return " " in raw or "." in raw


# Window (chars BEFORE the digit run) within which a French legal-context
# keyword must sit for a bare Luhn-passer to qualify as a SIREN/SIRET.
# Document-wide presence isn't enough on multilingual sites: stripe.com's
# /fr-fr/* docs have ``siret`` mentions on the same page as random 9-digit
# transaction IDs, which then masquerade as SIRENs by coincidence (14 of
# 15 remaining FPs after the doc-level gate had no FR keyword nearby).
_FR_CONTEXT_NEAR_WINDOW = 60


def _has_fr_context_near(html: str, pos: int) -> bool:
    """True iff a French legal-context keyword appears within
    ``_FR_CONTEXT_NEAR_WINDOW`` characters BEFORE position *pos* in *html*."""
    start = max(0, pos - _FR_CONTEXT_NEAR_WINDOW)
    return bool(_FR_CONTEXT_RE.search(html[start:pos]))


def extract_french_business_ids(html: str) -> list[dict]:
    """Return a deduplicated list of French business / training IDs.

    Each entry is ``{"type": str, "value": str, "raw": str, "validated": bool}``.
    ``type`` is one of: ``siren``, ``siret``, ``tva_fr``, ``rcs``, ``qualiopi``.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []

    # Cheap doc-wide pre-check: skip the expensive near-window scan when
    # the page contains zero French legal terms at all.
    fr_context_doc = bool(_FR_CONTEXT_RE.search(html))

    # SIRET first so a 14-digit run is not also captured as a 9-digit SIREN.
    siret_spans: list[tuple[int, int]] = []
    for m in SIRET_RE.finditer(html):
        digits = _norm(m.group(0))
        if len(digits) != 14:
            continue
        if not _luhn_ok(digits):
            continue
        if _looks_like_placeholder(digits):
            continue
        # Bare Luhn-passers must have either French formatting OR a
        # French keyword within the immediate window. A doc-wide keyword
        # presence is not enough: multilingual sites (stripe.com /fr-fr/)
        # have "siret" lines on the same page as transaction IDs.
        if not _has_fr_separator(m.group(0)):
            if not (fr_context_doc and _has_fr_context_near(html, m.start())):
                continue
        elif not fr_context_doc:
            # French formatting alone isn't enough: a 3/3/3-grouped run on a
            # page with zero French legal vocabulary is a reference / order
            # number, not a SIRET. Require at least a doc-wide FR term.
            continue
        key = ("siret", digits)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": "siret", "value": digits, "raw": m.group(0), "validated": True})
        siret_spans.append((m.start(), m.end()))

    for m in SIREN_RE.finditer(html):
        # Skip when this 9-digit run is part of a SIRET we already captured.
        if any(start <= m.start() < end for start, end in siret_spans):
            continue
        digits = _norm(m.group(0))
        if len(digits) != 9 or not _luhn_ok(digits):
            continue
        if _looks_like_placeholder(digits):
            continue
        # Same gate as SIRET: French formatting OR a French keyword in
        # the near-window. The doc-wide check alone over-emits on
        # translated documentation pages.
        if not _has_fr_separator(m.group(0)):
            if not (fr_context_doc and _has_fr_context_near(html, m.start())):
                continue
        elif not fr_context_doc:
            # See SIRET above: separator formatting on a non-French page is a
            # grouped reference number, not a SIREN.
            continue
        key = ("siren", digits)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": "siren", "value": digits, "raw": m.group(0), "validated": True})

    for m in TVA_FR_RE.finditer(html):
        raw = m.group(0)
        normalized = raw.replace(" ", "").replace(".", "").upper()
        key = ("tva_fr", normalized)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": "tva_fr", "value": normalized, "raw": raw, "validated": False})

    for m in RCS_RE.finditer(html):
        city = m.group(1).strip()
        siren_raw = m.group(2)
        siren_digits = _norm(siren_raw)
        validated = len(siren_digits) == 9 and _luhn_ok(siren_digits)
        value = f"RCS {city} {siren_digits}"
        key = ("rcs", value.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "type": "rcs", "value": value, "raw": m.group(0),
            "validated": validated, "city": city, "siren": siren_digits,
        })

    for m in QUALIOPI_RE.finditer(html):
        year, num, sub = m.group(1), m.group(2), m.group(3)
        value = f"{year}FOR{num}" + (f".{sub}" if sub else "")
        key = ("qualiopi", value)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": "qualiopi", "value": value, "raw": m.group(0), "validated": False})

    # RNCP. bare token first, then official-registry URL (which often
    # accompanies the token, so the dedup tuple keeps a single entry).
    for m in RNCP_BARE_RE.finditer(html):
        rncp_id = m.group(1)
        key = ("rncp", rncp_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "type": "rncp", "value": f"RNCP{rncp_id}", "raw": m.group(0),
            "validated": False,
            "pivot_url": f"https://www.francecompetences.fr/recherche/rncp/{rncp_id}/",
        })
    for m in RNCP_URL_RE.finditer(html):
        rncp_id = m.group(1)
        key = ("rncp", rncp_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "type": "rncp", "value": f"RNCP{rncp_id}", "raw": m.group(0),
            "validated": True,  # registry URL is canonical
            "pivot_url": f"https://www.francecompetences.fr/recherche/rncp/{rncp_id}/",
        })

    return out
