"""Tests for the social_profiles extractor."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all


def _run(html: str) -> list[dict]:
    pages = [{
        "html": html,
        "url": "https://example.com/",
        "timestamp": "20220601120000",
    }]
    return extract_all(pages, "example.com")["social_profiles"]


def _find(items: list[dict], platform: str):
    return [it for it in items if it["platform"] == platform]


# ---------------------------------------------------------------------------
# Positive - realistic profile links across platforms
# ---------------------------------------------------------------------------


def test_detects_twitter_handle():
    html = '<a href="https://twitter.com/jack">Twitter</a>'
    items = _run(html)
    tw = _find(items, "twitter")
    assert tw and tw[0]["handle"] == "jack"
    assert tw[0]["url"] == "https://twitter.com/jack"


def test_detects_x_handle():
    html = '<a href="https://x.com/elonmusk">X</a>'
    items = _run(html)
    x = _find(items, "x")
    assert x and x[0]["handle"] == "elonmusk"
    assert x[0]["url"] == "https://x.com/elonmusk"


def test_detects_linkedin_personal_profile():
    html = '<a href="https://www.linkedin.com/in/john-doe-123">LinkedIn</a>'
    items = _run(html)
    li = _find(items, "linkedin")
    assert any(it["handle"] == "john-doe-123" for it in li)
    entry = next(it for it in li if it["handle"] == "john-doe-123")
    assert entry["url"] == "https://linkedin.com/in/john-doe-123"


def test_detects_linkedin_company_profile():
    html = '<a href="https://linkedin.com/company/google">Company</a>'
    items = _run(html)
    li = _find(items, "linkedin")
    entry = next(it for it in li if it["handle"] == "google")
    assert entry["url"] == "https://linkedin.com/company/google"


def test_detects_github_profile():
    html = '<a href="https://github.com/torvalds">GitHub</a>'
    items = _run(html)
    gh = _find(items, "github")
    assert gh and gh[0]["handle"] == "torvalds"
    assert gh[0]["url"] == "https://github.com/torvalds"


def test_detects_instagram_profile():
    html = '<a href="https://www.instagram.com/natgeo/">Instagram</a>'
    items = _run(html)
    ig = _find(items, "instagram")
    assert ig and ig[0]["handle"] == "natgeo"
    assert ig[0]["url"] == "https://instagram.com/natgeo"


def test_detects_telegram_handle():
    html = '<a href="https://t.me/durov">Telegram</a>'
    items = _run(html)
    tg = _find(items, "telegram")
    assert tg and tg[0]["handle"] == "durov"
    assert tg[0]["url"] == "https://t.me/durov"


def test_detects_youtube_channel():
    html = '<a href="https://www.youtube.com/@MrBeast">YouTube</a>'
    items = _run(html)
    yt = _find(items, "youtube")
    assert yt and yt[0]["handle"] == "MrBeast"


def test_detects_tiktok_handle():
    html = '<a href="https://www.tiktok.com/@charlidamelio">TikTok</a>'
    items = _run(html)
    tt = _find(items, "tiktok")
    assert tt and tt[0]["handle"] == "charlidamelio"
    assert tt[0]["url"] == "https://tiktok.com/@charlidamelio"


def test_detects_snapchat_handle():
    html = '<a href="https://snapchat.com/add/teamsnapchat">Snapchat</a>'
    items = _run(html)
    sc = _find(items, "snapchat")
    assert sc and sc[0]["handle"] == "teamsnapchat"
    assert sc[0]["url"] == "https://snapchat.com/add/teamsnapchat"


def test_detects_discord_invite():
    html = '<a href="https://discord.gg/abc123">Discord</a>'
    items = _run(html)
    dc = _find(items, "discord")
    assert dc and dc[0]["handle"] == "abc123"
    assert dc[0]["url"] == "https://discord.gg/abc123"


# ---------------------------------------------------------------------------
# False positives - share/intent URLs, homepages, reserved/non-profile paths
# ---------------------------------------------------------------------------


def test_ignores_facebook_sharer():
    html = '<a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>'
    assert _find(_run(html), "facebook") == []


def test_ignores_facebook_dialog():
    html = '<a href="https://www.facebook.com/dialog/share?app_id=1">Share</a>'
    assert _find(_run(html), "facebook") == []


def test_ignores_twitter_share_endpoint():
    html = '<a href="https://twitter.com/share?url=https://example.com">Tweet</a>'
    assert _find(_run(html), "twitter") == []


def test_ignores_twitter_intent_endpoint():
    html = '<a href="https://twitter.com/intent/tweet?text=hi">Tweet</a>'
    assert _find(_run(html), "twitter") == []


def test_ignores_reserved_login_handle():
    # "login" is a reserved handle, not a real profile.
    html = '<a href="https://twitter.com/login">Log in</a>'
    assert _find(_run(html), "twitter") == []


def test_ignores_platform_homepage():
    # Bare homepage with no handle segment must not register a profile.
    html = '<a href="https://github.com/">GitHub</a>'
    assert _find(_run(html), "github") == []


def test_ignores_instagram_explore_path():
    # "explore" is a reserved non-profile path.
    html = '<a href="https://instagram.com/explore/tags/foo">Explore</a>'
    assert _find(_run(html), "instagram") == []


def test_ignores_youtube_watch_url():
    # A video watch URL is not a channel/profile.
    html = '<a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">Video</a>'
    assert _find(_run(html), "youtube") == []


def test_ignores_tiktok_without_at_prefix():
    # TikTok profiles require the "@" prefix; plain paths are not profiles.
    html = '<a href="https://tiktok.com/foryou">For You</a>'
    assert _find(_run(html), "tiktok") == []


def test_ignores_linkedin_anonymous_member_id():
    # Opaque "ACoA..." tracking member IDs are not usable handles.
    html = '<a href="https://linkedin.com/in/ACoAABCDEFGHIJKLMNOPQRSTUVWXYZ">x</a>'
    assert _find(_run(html), "linkedin") == []


def test_ignores_x_com_substring_in_word():
    # "x.com" must be preceded by a non-letter; here it is glued to a word.
    html = "<p>visit max.com/foo for details</p>"
    assert _find(_run(html), "x") == []


def test_ignores_short_telegram_handle():
    # Telegram handles need >=3 chars after t.me/.
    html = '<a href="https://t.me/ab">tg</a>'
    assert _find(_run(html), "telegram") == []


# Platform UI routes that look like handles but never identify a user.
# Matched per-platform so a legit handle elsewhere isn't dropped.


def test_ignores_github_features_route():
    html = '<a href="https://github.com/features">Features</a>'
    assert _find(_run(html), "github") == []


def test_ignores_github_pricing_route():
    html = '<a href="https://github.com/pricing">Pricing</a>'
    assert _find(_run(html), "github") == []


def test_ignores_instagram_reel_path():
    # /reel/<id> is a post, not the author's profile.
    html = '<a href="https://www.instagram.com/reel/Cabc123def">Reel</a>'
    assert _find(_run(html), "instagram") == []


def test_ignores_facebook_pixel_tr_endpoint():
    # facebook.com/tr is the Meta Pixel beacon, present on every site that
    # runs the pixel. It is not a Facebook page.
    html = '<img src="https://www.facebook.com/tr?id=123456&ev=PageView">'
    assert _find(_run(html), "facebook") == []


def test_ignores_twitter_home_feed():
    html = '<a href="https://twitter.com/home">Home</a>'
    assert _find(_run(html), "twitter") == []


def test_still_detects_handle_colliding_with_other_platform_route():
    # "features" is a reserved GitHub route, but a Twitter account literally
    # named "features" is still a valid handle: reserved lists are per-platform.
    html = '<a href="https://twitter.com/features">acct</a>'
    tw = _find(_run(html), "twitter")
    assert tw and tw[0]["handle"] == "features"
