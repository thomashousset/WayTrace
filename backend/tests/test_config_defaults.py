"""The v1.5 safety/queue defaults must not silently drift back up."""
from config import Settings


def test_antiblock_and_queue_defaults():
    s = Settings()
    # Rate ceiling sits below the ~105/min refusal point measured on 2600.eu.
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
