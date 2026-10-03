"""The v1.5 safety/queue defaults must not silently drift back up."""
from config import Settings


def test_antiblock_and_queue_defaults():
    s = Settings()
    # Rate ceiling sits below the ~105/min refusal point measured during tuning.
    assert s.archive_rate_max == 80
    assert s.archive_rate_per_minute <= s.archive_rate_max
    # One scan at a time, deep fair waiting queue, small per-IP abuse net.
    assert s.max_active_total == 1
    assert s.max_queue_total == 100
    assert s.max_active_per_ip == 2
    # Escalating hard-block cooldown: cheap first, capped.
    assert s.archive_hard_cooldown_base == 120
    assert s.archive_hard_cooldown_max == 1800
    assert s.archive_hard_streak_reset == 900


# --- .env.example is the file a self-hoster edits ----------------------------
# pydantic-settings ignores an env var it does not know, so a key that outlived
# the setting it named sits there doing nothing, silently. The production
# example had accumulated nine of them before anyone compared the two lists.

def test_no_key_in_the_env_example_is_ignored_by_the_settings():
    import re
    from pathlib import Path
    example = Path(__file__).resolve().parents[2] / ".env.example"
    if not example.exists():
        return
    known = {k.upper() for k in Settings.model_fields}
    keys = re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=",
                      example.read_text(encoding="utf-8"), re.M)
    dead = sorted({k for k in keys if k not in known})
    assert dead == [], f"these do nothing when set: {dead}"


def test_the_env_example_covers_the_settings_a_self_hoster_needs():
    import re
    from pathlib import Path
    example = Path(__file__).resolve().parents[2] / ".env.example"
    if not example.exists():
        return
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=",
                          example.read_text(encoding="utf-8"), re.M))
    # The knobs that decide how hard this instance leans on archive.org.
    for k in ("MAX_CONCURRENT_SCRAPES", "ARCHIVE_GLOBAL_CONCURRENCY",
              "SCRAPE_DELAY_MIN", "SCRAPE_DELAY_MAX"):
        assert k in keys, f"{k} is missing from the file people edit"
