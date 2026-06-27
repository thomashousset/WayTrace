"""Tests for directory listing detection in archived HTML."""
import pytest

from services.extractor.dirlist_extract import detect_directory_listing


def test_detects_apache_index_of():
    html = '<html><body><h1>Index of /uploads</h1><pre>Name Last modified</pre></body></html>'
    result = detect_directory_listing(html, "http://example.com/uploads/", "20200101120000")
    assert result is not None
    assert result["path"] == "/uploads/"
    assert result["server_type"] == "apache"


def test_detects_nginx_listing():
    html = '<html><head><title>Index of /assets/</title></head><body><h1>Index of /assets/</h1></body></html>'
    result = detect_directory_listing(html, "http://example.com/assets/", "20200101120000")
    assert result is not None
    assert result["path"] == "/assets/"


def test_detects_parent_directory():
    html = '<html><body><a href="../">Parent Directory</a><br>file1.txt</body></html>'
    result = detect_directory_listing(html, "http://example.com/data/", "20200101120000")
    assert result is not None


def test_detects_directory_listing_for():
    html = '<html><body><h2>Directory listing for /var/www/</h2></body></html>'
    result = detect_directory_listing(html, "http://example.com/var/www/", "20200101120000")
    assert result is not None


def test_no_false_positive_on_normal_page():
    html = '<html><body><h1>Welcome to our site</h1><p>Content here</p></body></html>'
    result = detect_directory_listing(html, "http://example.com/", "20200101120000")
    assert result is None


def test_no_false_positive_on_blog():
    html = '<html><body><h1>Blog Index</h1><p>Recent posts about indexing</p></body></html>'
    result = detect_directory_listing(html, "http://example.com/blog", "20200101120000")
    assert result is None


def test_detects_last_modified_table():
    html = '<html><body><table><tr><th>Name</th><th>Last modified</th><th>Size</th></tr></table></body></html>'
    result = detect_directory_listing(html, "http://example.com/files/", "20200101120000")
    assert result is not None
