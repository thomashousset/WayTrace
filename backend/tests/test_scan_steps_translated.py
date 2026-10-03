"""The progress screen is the one people watch, and it spoke English.

The frontend translates the two phases it recognises by shape (the N/M scrape
and the extraction), and falls back to printing job.step verbatim for
everything else. Every step the backend writes is English prose, so the CDX
query, the snapshot selection and the start of the scrape, which is the whole
first stretch of a scan on a large domain, read in English inside a French
interface.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_BACKEND = Path(__file__).resolve().parents[1]
_APP_JS = Path(__file__).resolve().parents[2] / "frontend" / "app.js"

# Written when the job ends, and rendered by the completed/failed branches
# rather than the progress line.
_NOT_PROGRESS = {"Scan complete", "Scan failed", "Cancelled", "Scraping", "Boom"}

# Two shapes the frontend recognises and re-writes in its own words rather
# than echoing: anything containing "extract" (the extraction phase has its
# own sentence and a determinate bar) and anything carrying an N/M count (it
# is re-templated as "Scraped {done} / {total} archived pages").
_REWRITTEN = ("extract", "{completed}/{total}")


def _backend_steps() -> set[str]:
    out = set()
    for py in _BACKEND.rglob("*.py"):
        if ".venv" in py.parts or "tests" in py.parts:
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        out |= set(re.findall(r'step="([^"{]+)"', text))
        out |= set(re.findall(r'step=f"([^"]+)"', text))
    return {s for s in out if s not in _NOT_PROGRESS
            and not any(m in s.lower() for m in _REWRITTEN)}


def test_the_frontend_translates_the_step_instead_of_printing_it():
    js = _APP_JS.read_text(encoding="utf-8")
    assert "function _stepText(" in js
    assert "job.step || t('Preparing scan…')" not in js, \
        "the raw backend string is still being printed"


def test_every_progress_step_has_a_french_sentence():
    js = _APP_JS.read_text(encoding="utf-8")
    missing = []
    for step in sorted(_backend_steps()):
        # A step carrying a count is matched by shape and re-templated.
        key = re.sub(r"\{[a-z_()\[\] ]+\}", "{n}", step)
        # A templated step is re-punctuated when it is rebuilt, so match on the
        # sentence rather than on its trailing dots.
        needle = key.rstrip(".… ") if "{n}" in key else key
        if ("'%s" % needle) not in js and ('"%s' % needle) not in js:
            missing.append(step)
    assert missing == [], f"a French user reads these in English: {missing}"
