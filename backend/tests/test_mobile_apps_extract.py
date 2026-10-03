"""Tests for the mobile_apps extractor (site-to-app linkage)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{"html": html, "url": "https://example.com/", "timestamp": "20220601120000"}]
    return extract_all(pages, "example.com")["mobile_apps"]


def _find(items: list[dict], store: str, app_id: str) -> dict | None:
    return next((it for it in items if it["store"] == store and it["app_id"] == app_id), None)


# ---------------- Positive ----------------

def test_apple_itunes_app_meta():
    html = '<meta name="apple-itunes-app" content="app-id=284882215">'
    e = _find(_run(html), "ios", "284882215")
    assert e is not None
    assert e["pivot_url"] == "https://apps.apple.com/app/id284882215"


def test_al_ios_app_store_id_meta():
    html = '<meta property="al:ios:app_store_id" content="389801252">'
    assert _find(_run(html), "ios", "389801252") is not None


def test_al_android_package_meta():
    html = '<meta property="al:android:package" content="com.instagram.android">'
    e = _find(_run(html), "android", "com.instagram.android")
    assert e is not None
    assert "com.instagram.android" in e["pivot_url"]


def test_play_store_link():
    html = '<a href="https://play.google.com/store/apps/details?id=com.spotify.music&hl=en">Get it</a>'
    assert _find(_run(html), "android", "com.spotify.music") is not None


def test_apple_apps_link():
    html = '<a href="https://apps.apple.com/us/app/spotify/id324684580">iOS</a>'
    assert _find(_run(html), "ios", "324684580") is not None


def test_twitter_app_card_googleplay():
    html = '<meta name="twitter:app:id:googleplay" content="com.example.app">'
    assert _find(_run(html), "android", "com.example.app") is not None


def test_itunes_legacy_link():
    html = '<a href="https://itunes.apple.com/app/id123456789">app</a>'
    assert _find(_run(html), "ios", "123456789") is not None


# ---------------- Negative ----------------

def test_generic_apple_link_without_id_ignored():
    html = '<a href="https://www.apple.com/iphone/">iPhone</a>'
    assert _run(html) == []


def test_play_store_homepage_ignored():
    html = '<a href="https://play.google.com/store">Play Store</a>'
    assert _run(html) == []


def test_android_package_single_token_rejected():
    html = '<meta property="al:android:package" content="notapackage">'
    assert _run(html) == []


def test_random_number_not_ios_app():
    html = '<p>Order #284882215 shipped. Reference id 389801252.</p>'
    assert _run(html) == []


def test_apple_music_content_link_not_app():
    # A song/album link (no /id<digits> app path) must not register.
    html = '<a href="https://music.apple.com/us/album/thriller/269572838">album</a>'
    # music.apple.com album ids are not app ids; our regex targets apps/itunes only.
    assert _run(html) == []


def test_play_id_query_without_package_shape_ignored():
    html = '<a href="https://play.google.com/store/apps/details?id=singleword">x</a>'
    assert _run(html) == []
