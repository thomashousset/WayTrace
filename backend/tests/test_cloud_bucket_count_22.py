"""The cloud-bucket highlight counts buckets, not URLs.

Every object URL is its own entity on purpose (backup.sql and logo.png are
different findings), but the LEAK line counted those URLs as buckets: four
images in one bucket read "4 cloud bucket(s) exposed".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.extractor import extract_all
from services.extractor.helpers import bucket_key
from services.extractor.highlights import compute_highlights


@pytest.mark.parametrize("url, key", [
    ("mybucket.s3.amazonaws.com/a.png", "s3:mybucket"),
    ("https://mybucket.s3.eu-west-1.amazonaws.com/d.png", "s3:mybucket"),
    ("s3.amazonaws.com/mybucket/c.png", "s3:mybucket"),
    ("s3-eu-west-1.amazonaws.com/mybucket/x", "s3:mybucket"),
    ("mybucket.s3-website-us-east-1.amazonaws.com/", "s3:mybucket"),
    ("my.dotted.bucket.s3.amazonaws.com/k", "s3:my.dotted.bucket"),
    # A bucket whose own name holds ".s3" is not the bucket before it.
    ("acme.s3-backup.s3.amazonaws.com/a.png", "s3:acme.s3-backup"),
    ("acme.s3-backup.s3.eu-west-3.amazonaws.com/a.png", "s3:acme.s3-backup"),
    ("assets.acme.fr.s3-website-eu-west-1.amazonaws.com/", "s3:assets.acme.fr"),
    ("storage.googleapis.com/my-bucket", "gcs:my-bucket"),
    ("myaccount.blob.core.windows.net/container/blob", "azure:myaccount.blob.core.windows.net/container"),
    ("myspace.nyc3.digitaloceanspaces.com/file", "do:myspace"),
    ("nyc3.digitaloceanspaces.com/myspace/file", "do:myspace"),
])
def test_bucket_key(url, key):
    assert bucket_key(url) == key


def _scan(html):
    res = extract_all([{"html": html, "url": "https://acme.fr/", "timestamp": "20220101000000"}], "acme.fr")
    res["highlights"] = compute_highlights(res, "acme.fr")
    return res


def test_four_objects_of_one_bucket_are_one_bucket():
    html = "".join(f'<img src="{u}">' for u in (
        "https://mybucket.s3.amazonaws.com/a.png", "https://mybucket.s3.amazonaws.com/b.png",
        "https://s3.amazonaws.com/mybucket/c.png", "https://mybucket.s3.eu-west-1.amazonaws.com/d.png"))
    res = _scan(html)
    assert len(res["cloud_buckets"]) == 4                     # each object kept
    [h] = [h for h in res["highlights"] if h["category"] == "cloud_buckets"]
    assert h["title"] == "1 cloud bucket(s) exposed"
    assert h["detail"] == "mybucket"


def test_scans_saved_before_the_field_still_count_right():
    results = {"cloud_buckets": [{"value": "a.s3.amazonaws.com/x"}, {"value": "a.s3.amazonaws.com/y"},
                                 {"value": "storage.googleapis.com/b"}]}
    [h] = [h for h in compute_highlights(results, "acme.fr") if h["category"] == "cloud_buckets"]
    assert h["title"] == "2 cloud bucket(s) exposed"
