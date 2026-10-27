"""Opening another scan resets the time scrubber.

SCRUB.at survived from one scan to the next: scrub scan A to 2014, open
scan B covering 2019-2024, and B opened with "Jan 2014", every row dimmed
and every rail count at 0. Opt-in: WT_E2E=1.
"""


def _job(uid, domain, first, last):
    return {"url_id": uid, "domain": domain, "status": "completed",
            "created_at": "2026-10-01T00:00:00", "expires_at": "2026-11-01T00:00:00",
            "results": {"emails": [{"value": f"x@{domain}", "first_seen": first,
                                    "last_seen": last, "occurrences": 1}]}}


def test_a_new_scan_opens_on_all_time(live_server, page):
    page.goto(live_server + "/", wait_until="networkidle")
    page.evaluate("job => renderV2InLegacyView(job)", _job("a" * 24, "a.example", "2012-01", "2016-01"))
    page.evaluate("setScrub(String(2014 * 12))")
    assert page.evaluate("SCRUB.at") == 2014 * 12
    page.evaluate("job => renderV2InLegacyView(job)", _job("b" * 24, "b.example", "2019-01", "2024-01"))
    assert page.evaluate("SCRUB.at") is None
    assert page.locator("#r2-main .r2-row.scrub-out").count() == 0
