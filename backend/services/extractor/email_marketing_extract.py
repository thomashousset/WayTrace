"""Marketing-automation / CRM embed detector, with the tenant account id.

Same idea as support_chat: ``technologies`` flags that e.g. HubSpot's script is
present, this category pulls the portal / account id out of the snippet. The id
is the pivot: one HubSpot portal id, Mailchimp audience id or Marketo munchkin
id reused across sites ties them to a single marketing account.
"""
from __future__ import annotations

import re

from .helpers import update_entity


_VENDOR_PATTERNS: dict[str, list[re.Pattern]] = {
    "mailchimp": [
        re.compile(r"\.list-manage\.com", re.IGNORECASE),
        re.compile(r"chimpstatic\.com", re.IGNORECASE),
        re.compile(r"mc\.us\d+\.list-manage\.com", re.IGNORECASE),
    ],
    "hubspot": [
        re.compile(r"js\.hs-scripts\.com/\d+\.js", re.IGNORECASE),
        re.compile(r"js\.hsforms\.net", re.IGNORECASE),
        re.compile(r"js\.hubspot\.com", re.IGNORECASE),
    ],
    "marketo": [
        re.compile(r"munchkin\.(?:js|init)", re.IGNORECASE),
        re.compile(r"munchkin\.marketo\.net", re.IGNORECASE),
        re.compile(r"\d+\.mktoresp\.com", re.IGNORECASE),
    ],
    "klaviyo": [
        re.compile(r"static\.klaviyo\.com", re.IGNORECASE),
        re.compile(r"klaviyo\.com/onsite", re.IGNORECASE),
    ],
    "pardot": [
        re.compile(r"pi\.pardot\.com", re.IGNORECASE),
        re.compile(r"\bpiAId\s*="),
    ],
    "convertkit": [
        re.compile(r"\.ck\.page", re.IGNORECASE),
        re.compile(r"f\.convertkit\.com", re.IGNORECASE),
    ],
    "activecampaign": [
        re.compile(r"\.activehosted\.com", re.IGNORECASE),
    ],
    "brevo": [
        re.compile(r"sibforms\.com", re.IGNORECASE),
        re.compile(r"sibautomation\.com", re.IGNORECASE),
    ],
}


_ID_PATTERNS: dict[str, re.Pattern] = {
    # Mailchimp audience: the u=<hex> of the list-manage subscribe URL.
    "mailchimp": re.compile(r"list-manage\.com/[^\"'?]*\?[^\"']*\bu=([0-9a-f]{20,})", re.IGNORECASE),
    # HubSpot portal id (numeric) from the tracking-script filename.
    "hubspot": re.compile(r"js\.hs-scripts\.com/(\d{4,})\.js", re.IGNORECASE),
    # Marketo munchkin id, e.g. 123-ABC-456.
    "marketo": re.compile(r"munchkin\.init\(\s*['\"]([0-9]{3}-[A-Za-z]{3}-[0-9]{3})['\"]", re.IGNORECASE),
    # Klaviyo company id (6 alnum) in the onsite URL or company_id param.
    "klaviyo": re.compile(
        r"klaviyo\.com/onsite/js/([A-Za-z0-9]{6})/"
        r"|company_id=([A-Za-z0-9]{6})",
        re.IGNORECASE,
    ),
    # Pardot account id.
    "pardot": re.compile(r"piAId\s*=\s*['\"]?(\d{3,})"),
    # ConvertKit form id or account subdomain.
    "convertkit": re.compile(
        r"f\.convertkit\.com/(\w+)"
        r"|\b([a-z0-9]+)\.ck\.page",
        re.IGNORECASE,
    ),
    # ActiveCampaign account subdomain.
    "activecampaign": re.compile(r"\b([a-z0-9][a-z0-9-]*)\.activehosted\.com", re.IGNORECASE),
    # Brevo (Sendinblue) form serve id.
    "brevo": re.compile(r"sibforms\.com/serve/([A-Za-z0-9_-]{6,})", re.IGNORECASE),
}

# Subdomains that are the vendor's own infra, not a customer account.
_NON_TENANT = {"www", "static", "cdn", "assets", "js", "api", "app", "mc"}

_LABELS = {
    "mailchimp": "Mailchimp",
    "hubspot": "HubSpot",
    "marketo": "Marketo",
    "klaviyo": "Klaviyo",
    "pardot": "Pardot",
    "convertkit": "ConvertKit",
    "activecampaign": "ActiveCampaign",
    "brevo": "Brevo",
}

_PORTALS = {
    "mailchimp": "https://mailchimp.com/",
    "hubspot": "https://www.hubspot.com/",
    "marketo": "https://www.marketo.com/",
    "klaviyo": "https://www.klaviyo.com/",
    "pardot": "https://www.salesforce.com/products/marketing-cloud/",
    "convertkit": "https://kit.com/",
    "activecampaign": "https://www.activecampaign.com/",
    "brevo": "https://www.brevo.com/",
}


def _pivot_for(platform: str, account_id: str) -> str:
    if platform == "hubspot" and account_id:
        return f"https://app.hubspot.com/contacts/{account_id}/"
    if platform == "activecampaign" and account_id:
        return f"https://{account_id}.activehosted.com/"
    return _PORTALS.get(platform, "")


def _capture_id(platform: str, raw_text: str) -> str:
    rx = _ID_PATTERNS.get(platform)
    if rx is None:
        return ""
    m = rx.search(raw_text)
    if not m:
        return ""
    acct = next((g for g in m.groups() if g), "")
    if platform in ("activecampaign", "convertkit") and acct.lower() in _NON_TENANT:
        return ""
    return acct


def extract_email_marketing(raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['email_marketing']`` with detected martech accounts."""
    for platform, patterns in _VENDOR_PATTERNS.items():
        if not any(rx.search(raw_text) for rx in patterns):
            continue
        account_id = _capture_id(platform, raw_text)
        key = f"{platform}:{account_id}" if account_id else platform
        update_entity(
            accum["email_marketing"],
            key,
            month,
            {
                "platform": platform,
                "label": _LABELS.get(platform, platform),
                "account_id": account_id,
                "pivot_url": _pivot_for(platform, account_id),
            },
        )
