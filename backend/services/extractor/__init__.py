"""OSINT extraction engine ; split into submodules for maintainability.

Public API (backward-compatible with the old single-file extractor):
    ALL_CATEGORIES, extract_page_safe, finalize_accum, compute_highlights, extract_all
"""
from .finalize import (  # noqa: F401
    ALL_CATEGORIES, extract_page_safe, finalize_accum, extract_all,
    new_accum, mine_subdomains, process_page,
)
from .highlights import compute_highlights  # noqa: F401

# Re-export patterns and helpers for tests and external consumers
from .patterns import (  # noqa: F401
    EMAIL_RE, PHONE_RE, TRACKER_PATTERNS, SOCIAL_PATTERNS,
    S3_RE, GCS_RE, AZURE_RE, DO_SPACES_RE,
    AWS_KEY_RE, GOOGLE_API_RE, STRIPE_RE, TWILIO_RE, SENDGRID_RE,
    SLACK_WEBHOOK_RE, GITHUB_TOKEN_RE,
    SCRIPT_TECH_PATTERNS,
    JWT_RE,
)
from .helpers import (  # noqa: F401
    is_email_excluded as _is_email_excluded,
    strip_wayback_artifacts as _strip_wayback_artifacts,
)
from .jwt_extract import extract_jwts  # noqa: F401
from .dirlist_extract import detect_directory_listing  # noqa: F401
from .hidden_fields_extract import extract_hidden_fields  # noqa: F401
from .internal_ips_extract import extract_internal_ips  # noqa: F401
from .adsense_extract import extract_adsense_ids  # noqa: F401
from .verification_extract import extract_verification_tags  # noqa: F401
from .iframe_extract import extract_iframe_sources  # noqa: F401
from .js_urls_extract import extract_js_urls  # noqa: F401
from .connstring_extract import extract_connection_strings  # noqa: F401
from .crypto_extract import extract_crypto_addresses  # noqa: F401
from .favicon_extract import extract_favicons  # noqa: F401
from .outgoing_links_extract import extract_outgoing_links  # noqa: F401
from .hosting_extract import detect_hosting  # noqa: F401

__all__ = [
    "ALL_CATEGORIES",
    "extract_page_safe",
    "finalize_accum",
    "compute_highlights",
    "extract_all",
    # Patterns
    "EMAIL_RE", "PHONE_RE", "TRACKER_PATTERNS", "SOCIAL_PATTERNS",
    "S3_RE", "GCS_RE", "AZURE_RE", "DO_SPACES_RE",
    "AWS_KEY_RE", "GOOGLE_API_RE", "STRIPE_RE", "TWILIO_RE",
    "SENDGRID_RE", "SLACK_WEBHOOK_RE", "GITHUB_TOKEN_RE",
    "SCRIPT_TECH_PATTERNS",
    # Helpers (prefixed for backward compat)
    "_is_email_excluded", "_strip_wayback_artifacts",
    # New extractors
    "extract_jwts", "detect_directory_listing", "JWT_RE",
    "extract_hidden_fields", "extract_internal_ips", "extract_adsense_ids",
    "extract_verification_tags", "extract_iframe_sources", "extract_js_urls",
    "extract_connection_strings",
    "extract_crypto_addresses", "extract_favicons",
    "extract_outgoing_links", "detect_hosting",
]
