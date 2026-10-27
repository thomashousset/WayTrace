"""Extractor regexes must stay linear on the inputs archived pages actually hold.

Every pattern runs over attacker-controlled text: whatever a site served, years
ago, to whoever archived it. A regex that backtracks exponentially lets one
page pin a worker for minutes. PHONE_RE split long digit runs every possible
way (about x4.7 per 4 extra digits: 2 s at 44 digits, minutes past 50), and
EMAIL_RE rescanned a long run of local-part characters from each start
position (3 s on a 60 KB minified SVG path).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor.patterns import EMAIL_RE, PHONE_RE

BUDGET = 0.25  # seconds; the fixed patterns finish these in a few ms


def _elapsed(pattern, text):
    start = time.perf_counter()
    list(pattern.finditer(text))
    return time.perf_counter() - start


def test_phone_long_digit_run_before_a_period():
    # Branch 3 shape: one separated group, then a digit run the lookahead rejects.
    assert _elapsed(PHONE_RE, "12 " + "1" * 60 + ".") < BUDGET


def test_phone_long_run_after_parens_area_code():
    assert _elapsed(PHONE_RE, "(12) " + "1" * 60 + ".") < BUDGET


def test_phone_many_long_runs_in_one_page():
    text = " ".join("12 " + "3" * 50 + "." for _ in range(50))
    assert _elapsed(PHONE_RE, text) < BUDGET


def test_email_minified_svg_path():
    # Real shape: a path's d attribute, digits and dots for kilobytes, no '@'.
    path = "M" + ".".join(str(i % 97) for i in range(20000))
    assert _elapsed(EMAIL_RE, path) < BUDGET


def test_email_long_local_part_run_without_at():
    assert _elapsed(EMAIL_RE, "a." * 30000) < BUDGET


# What the bounds must not cost: ordinary matches are unchanged.

def test_phone_matches_still_found():
    for text, want in [
        ("Tel: 01 42 68 53 00", "01 42 68 53 00"),
        ("call +33 1 42 68 53 00 now", "+33 1 42 68 53 00"),
        ("(800) 555-1234", "(800) 555-1234"),
        ("+1-800-555-1234", "+1-800-555-1234"),
        ("Fax 04.91.55.12.34 (bureau)", "04.91.55.12.34"),
    ]:
        assert [m.group(0) for m in PHONE_RE.finditer(text)] == [want], text


def test_email_matches_still_found():
    for text, want in [
        ("mailto:contact@acme.fr", "contact@acme.fr"),
        ("write to jean.dupont+news@mail.acme.co.uk.", "jean.dupont+news@mail.acme.co.uk"),
        ("--marie_x@example.org--", "marie_x@example.org"),
    ]:
        assert [m.group(0) for m in EMAIL_RE.finditer(text)] == [want], text


def test_an_over_long_local_part_yields_nothing_rather_than_its_tail():
    """The 64-character cap must not turn an address nobody could send to into
    a shorter, plausible one made of its last words."""
    from services.extractor.extract import _whole_emails
    long_local = ".".join(["jean.dupont"] * 7) + "@example.com"
    assert len(long_local.split("@")[0]) > 64
    assert [m.group() for m in _whole_emails(f" {long_local} ")] == []
    assert [m.group() for m in _whole_emails("--marie_x@example.org")] == ["marie_x@example.org"]
