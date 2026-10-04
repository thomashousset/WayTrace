"""Every dependency needs a ceiling, and the ceiling has to be in the right place.

selectolax 1.0 removed the parser all 23 extractor modules import. The
requirement read `selectolax>=0.3.0`, so a fresh install resolved to it the day
it was published: a clone following the quick start got 68 collection errors and
no tests, and the next image rebuild would have shipped a container that could
not parse a page. Production was fine; deploying would have broken it.

The second half matters as much as the first. Half of these are 0.x, and semver
puts the breaking change in the MINOR there. A `<1.0` on a 0.x package reads as
caution and stops nothing, because the step that moves under you is 0.142 to
0.143. So this checks the bound exists AND that it is the minor for a 0.x
requirement, which is the shape that would otherwise pass review by looking
careful.
"""
import re
from pathlib import Path

import pytest

REQ = Path(__file__).resolve().parents[1] / "requirements.txt"
_LINE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(?:\[[^\]]+\])?(?P<specs>.*)$")


def _requirements() -> list[tuple[str, str]]:
    out = []
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _LINE.match(line)
        assert m, f"cannot read this requirement line: {raw!r}"
        out.append((m.group("name"), m.group("specs")))
    return out


def test_the_file_has_requirements_to_check():
    """If the format changes, this must not quietly pass by finding nothing."""
    assert len(_requirements()) >= 8


@pytest.mark.parametrize("name,specs", _requirements(), ids=lambda v: str(v)[:28])
def test_every_dependency_has_a_ceiling(name, specs):
    assert "<" in specs, (
        f"{name} has no upper bound, so a fresh install takes whatever is "
        f"published next. That is how selectolax 1.0 arrived."
    )


# A floor in 0.x whose ceiling is still allowed at the major, with the reason.
# The rule below is right for a package whose 0.x line is still being released
# into; it is wrong for one that has left 0.x behind, where no further 0.minor
# can ever appear to break anything.
_MAJOR_CAP_IS_CORRECT = {
    # selectolax shipped 1.0 and ended its 0.x line at 0.4.13, so <1.0 blocks
    # the only release that can break this project and nothing below it will
    # ever move again.
    "selectolax",
}


@pytest.mark.parametrize("name,specs", _requirements(), ids=lambda v: str(v)[:28])
def test_a_zero_x_dependency_is_capped_at_the_minor(name, specs):
    """A 0.x package breaks at the minor, so <1.0 on one is a ceiling placed
    above every version that could actually break it."""
    if name in _MAJOR_CAP_IS_CORRECT:
        pytest.skip(f"{name}: its 0.x line is closed, the major cap is the real one")
    low = re.search(r">=\s*([0-9][^,\s]*)", specs)
    high = re.search(r"<\s*([0-9][^,\s]*)", specs)
    assert low and high, f"{name}: expected both a floor and a ceiling, got {specs!r}"
    if not low.group(1).startswith("0."):
        return                      # 1.x+: capping at the major is correct
    assert high.group(1).startswith("0."), (
        f"{name} is a 0.x package, where semver puts the breaking change in the "
        f"minor, but it is capped at {high.group(1)}. That ceiling sits above "
        f"every release that could break it."
    )
