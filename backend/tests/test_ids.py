"""Tests for url_id generation."""
import re

from services.ids import generate_url_id


def test_url_id_is_24_url_safe_chars():
    uid = generate_url_id()
    assert len(uid) == 24
    assert re.fullmatch(r"[A-Za-z0-9_-]+", uid), uid


def test_url_id_is_unique_over_2000_calls():
    ids = {generate_url_id() for _ in range(2000)}
    assert len(ids) == 2000
