"""A refused scan must explain itself in the reader's language.

Every refusal the API returns carries both a machine code and an English
sentence. The frontend used the code for one case and printed the English
sentence for all the others, so hitting a rate limit, a full queue or the
archive.org pause answered in English inside a French interface. These are
the three refusals a real visitor actually meets on a busy day.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_BACKEND = Path(__file__).resolve().parents[1]
_APP_JS = Path(__file__).resolve().parents[2] / "frontend" / "app.js"


def _error_codes() -> set[str]:
    out = set()
    for py in _BACKEND.rglob("routers/*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        out |= set(re.findall(r'"error":\s*"([a-z_]+)"', text))
    return out


def test_the_frontend_resolves_a_code_before_falling_back():
    js = _APP_JS.read_text(encoding="utf-8")
    assert "function _apiErrorText(" in js
    body = js[js.index("function _apiErrorText("):]
    body = body[:body.index("\n}\n")]
    # A code it does not know must still show what the server said, not a key.
    assert "detail.message" in body


def test_the_refusals_a_visitor_meets_are_translated():
    js = _APP_JS.read_text(encoding="utf-8")
    missing = [c for c in ("per_user_limit", "per_ip_limit", "service_full",
                           "archive_paused")
               if ("'api.%s'" % c) not in js]
    assert missing == [], f"these answer in English: {missing}"


def test_no_refusal_code_was_added_without_a_sentence():
    js = _APP_JS.read_text(encoding="utf-8")
    # auth_required is handled by its own 401 branch with its own copy.
    known = {"auth_required"}
    missing = [c for c in sorted(_error_codes())
               if c not in known and ("'api.%s'" % c) not in js]
    assert missing == [], f"a refusal with no sentence of its own: {missing}"
