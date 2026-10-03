"""The French dictionary is one big object literal, so a key declared twice is
not an error: the later one silently wins. Two entries added for My scans
shadowed existing translations ('All' turned 'Tout' into 'Tous', 'Failed'
turned 'Échec' into 'Échecs') and nothing complained.

This reads the literal and refuses any repeated key, identical value or not,
because a duplicate that agrees today is a silent override tomorrow.
"""
import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[2] / "frontend" / "app.js"
pytestmark = pytest.mark.skipif(not APP_JS.exists(), reason="frontend not present")

# Some lines declare several pairs, so match pairs rather than line starts.
_PAIR = re.compile(
    r"'((?:[^'\\]|\\.)*)'\s*:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"
)


def _fr_block() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("const I18N = {")     # not I18N_EN, declared just above
    return src[start:src.index("\n};", start)]


def test_no_key_is_declared_twice_with_two_different_translations():
    """A repeated key with the same value is noise. A repeated key with two
    values is a bug: whichever comes last wins, everywhere, silently."""
    seen = {}
    conflicts = {}
    for key, raw in _PAIR.findall(_fr_block()):
        value = raw[1:-1]
        if key in seen and seen[key] != value:
            conflicts[key] = (seen[key], value)
        seen.setdefault(key, value)
    assert not conflicts, (
        "same key, two translations, the later one silently wins: " + repr(conflicts)
    )


def test_the_dictionary_is_not_trivially_small():
    """Guards the regex above: if it stops matching, the test must fail loudly
    rather than pass on an empty set."""
    assert len(_PAIR.findall(_fr_block())) > 400


def test_no_translation_key_is_built_from_an_empty_label():
    """Tagging the admin panel machine-generated its keys from the label text,
    and a column headed "#" produced the key "adm.", which rendered literally
    as "adm." in the table header. A key with nothing after its prefix is a
    generation slip, not a label.
    """
    src = APP_JS.read_text(encoding="utf-8")
    bad = re.findall(r"t\(\s*'((?:adm|ms|kb|legal|mode|home|scope|nav|menu)\.)'\s*\)", src)
    assert not bad, f"keys with an empty name: {sorted(set(bad))}"


def test_every_key_asked_for_exists_in_the_dictionary():
    """t() falls back to the key itself, so a typo ships the raw key to screen
    instead of failing. The admin translation pass shipped one that way."""
    src = APP_JS.read_text(encoding="utf-8")
    declared = {k for k, _ in _PAIR.findall(_fr_block())}
    asked = set(re.findall(r"t\(\s*'((?:adm|ms|kb|legal|mode)\.[a-z0-9_.]+)'\s*\)", src))
    missing = sorted(asked - declared)
    assert not missing, (
        "asked for by the code but never declared, so the raw key reaches the "
        f"screen in French: {missing}"
    )


def _en_block() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("const I18N_EN")
    return src[start:src.index("\n};", start)]


def test_identifier_keys_have_an_english_text():
    """t() returns the key itself when it finds no translation, which works
    because most keys ARE their English text. A key written as an identifier
    has no English anywhere, so the fallback ships the key to screen: the whole
    admin panel read "adm.scans_per_day" and "adm.last_ip" for anyone using the
    English interface.
    """
    src = APP_JS.read_text(encoding="utf-8")
    asked = set(re.findall(r"t\(\s*'([a-z][a-z0-9]*\.[a-z0-9_.]+)'\s*\)", src))
    have = {k for k, _ in _PAIR.findall(_en_block())}
    missing = sorted(asked - have)
    assert not missing, (
        "identifier-style keys with no English text, so the raw key reaches the "
        f"screen in English: {missing}"
    )


def test_the_english_map_is_not_just_the_keys_again():
    """Guards against filling I18N_EN mechanically with key: key, which would
    silence the test above while changing nothing on screen."""
    pairs = _PAIR.findall(_en_block())
    assert pairs, "I18N_EN parsed empty; the regex above has drifted"
    echoes = [k for k, v in pairs if v[1:-1] == k]
    assert not echoes, f"English text identical to the key: {echoes[:10]}"


def test_no_translation_key_is_used_twice_on_different_content():
    """applyI18n paints one translation into every element carrying the key, so
    two elements sharing a key with different English text means one of them
    gets overwritten by the other's copy. I did exactly that adding a paragraph
    to the legal page, reusing legal.p6e because it looked free."""
    import re
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text(encoding="utf-8")
    par_cle = {}
    for m in re.finditer(r'data-i18n(?:-html)?="([^"]+)"[^>]*>(.{0,120}?)</', html, re.S):
        cle, texte = m.group(1), " ".join(m.group(2).split())
        par_cle.setdefault(cle, set()).add(texte)
    collisions = {k: sorted(v)[:2] for k, v in par_cle.items() if len(v) > 1}
    assert collisions == {}, f"same key, different copy: {collisions}"


def test_the_retention_intro_states_no_count():
    """The list under it is shorter in the self-hosted build, which has no
    accounts, so a number in the sentence above is wrong in one of the two.
    I wrote "three" while the hosted list had four items and the public one
    three."""
    import re
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text(encoding="utf-8")
    i = html.index('data-i18n-html="legal.p6"')
    intro = html[i:html.index("</p>", i)]
    nombres = re.findall(r"\b(one|two|three|four|five|un|deux|trois|quatre|cinq)\b", intro, re.I)
    assert nombres == [], f"the intro names a count the build can change: {nombres}"
