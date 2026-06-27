# backend/tests/test_hidden_fields_extract.py
"""Tests for hidden form field extraction."""
from __future__ import annotations

import pytest

from services.extractor.hidden_fields_extract import extract_hidden_fields


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------

def test_basic_hidden_input():
    html = '<input type="hidden" name="csrf_token" value="abc123xyz">'
    results = extract_hidden_fields(html)
    assert len(results) == 1
    assert results[0]["name"] == "csrf_token"
    assert results[0]["value"] == "abc123xyz"


def test_multiple_hidden_fields_only_hidden_extracted():
    html = """
    <form>
        <input type="hidden" name="session_id" value="sess_abc123">
        <input type="text"   name="username"   value="alice">
        <input type="hidden" name="return_url" value="/dashboard/home">
    </form>
    """
    results = extract_hidden_fields(html)
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert names == {"session_id", "return_url"}
    assert "username" not in names


def test_form_action_captured():
    html = """
    <form action="/submit/login">
        <input type="hidden" name="nonce" value="xyz987abc">
    </form>
    """
    results = extract_hidden_fields(html)
    assert len(results) == 1
    assert results[0]["form_action"] == "/submit/login"


def test_no_form_action_when_form_lacks_action():
    html = """
    <form>
        <input type="hidden" name="nonce" value="xyz987abc">
    </form>
    """
    results = extract_hidden_fields(html)
    assert len(results) == 1
    assert results[0]["form_action"] is None


def test_value_truncated_at_200_chars():
    long_value = "x" * 300
    html = f'<input type="hidden" name="big_token" value="{long_value}">'
    results = extract_hidden_fields(html)
    assert len(results) == 1
    assert len(results[0]["value"]) == 200
    assert results[0]["value"] == "x" * 200


def test_type_attribute_case_insensitive():
    html = '<input TYPE="HIDDEN" name="api_key" value="key_abc123def">'
    results = extract_hidden_fields(html)
    assert len(results) == 1
    assert results[0]["name"] == "api_key"


# ---------------------------------------------------------------------------
# False positive rejections
# ---------------------------------------------------------------------------

def test_skip_empty_value():
    html = '<input type="hidden" name="some_field" value="">'
    results = extract_hidden_fields(html)
    assert results == []


def test_skip_short_value():
    html = '<input type="hidden" name="flag" value="ab">'
    results = extract_hidden_fields(html)
    assert results == []


def test_skip_viewstate():
    html = '<input type="hidden" name="__VIEWSTATE" value="longviewstatevalue123">'
    results = extract_hidden_fields(html)
    assert results == []


def test_skip_eventvalidation():
    html = '<input type="hidden" name="__EVENTVALIDATION" value="longvalidationvalue123">'
    results = extract_hidden_fields(html)
    assert results == []


def test_skip_utf8_field():
    html = '<input type="hidden" name="utf8" value="&#x2713;">'
    results = extract_hidden_fields(html)
    assert results == []


def test_skip_input_with_no_name():
    html = '<input type="hidden" value="some_secret_value_here">'
    results = extract_hidden_fields(html)
    assert results == []
