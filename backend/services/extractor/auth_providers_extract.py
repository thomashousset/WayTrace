"""Detect identity-provider tenants.

Auth0, Okta, AWS Cognito, Keycloak, WorkOS and Clerk. The tenant slug
is the interesting piece: it identifies the org's IdP and tends to be
shared across their other properties.
"""
from __future__ import annotations

from .helpers import update_entity
from .patterns import AUTH_PROVIDER_PATTERNS


_PIVOT_TEMPLATES = {
    "auth0": "https://manage.auth0.com/dashboard/eu/{slug}/",
    "okta": "https://{slug}.okta.com/admin/dashboard",
    "cognito": "https://{slug}.console.aws.amazon.com/cognito/v2/idp/user-pools",
    "keycloak": "",
    "workos": "https://dashboard.workos.com/",
    "clerk": "https://dashboard.clerk.com/",
}

# Common false-positive slugs: the provider's own subdomains / hosts
# (cdn.auth0.com, assets...) and built-in realms that exist on every
# install (Keycloak's 'master' admin realm, the 'account' realm), none of
# which identify a customer org.
_RESERVED_SLUGS = {
    "www", "app", "api", "auth", "admin", "blog", "help", "docs",
    "support", "login", "signup", "dashboard", "manage", "console",
    "cdn", "assets", "master", "account",
}


def extract_auth_providers(raw_text: str, month: str, accum: dict) -> None:
    """Populate ``accum['auth_providers']`` with tenant references."""
    for platform, pattern in AUTH_PROVIDER_PATTERNS.items():
        for match in pattern.finditer(raw_text):
            slug = next((g for g in match.groups() if g), None)
            if not slug:
                # Pattern matched without a tenant capture (e.g. workos);
                # record it as a presence signal under a sentinel key.
                slug = "_"
            slug = slug.strip("/").lower()
            if slug != "_" and (len(slug) < 2 or slug in _RESERVED_SLUGS):
                continue
            key = f"{platform}:{slug}"
            tmpl = _PIVOT_TEMPLATES.get(platform, "")
            pivot = tmpl.format(slug=slug) if tmpl and "{slug}" in tmpl else tmpl
            update_entity(
                accum["auth_providers"],
                key,
                month,
                {
                    "platform": platform,
                    "tenant": slug if slug != "_" else "",
                    "pivot_url": pivot,
                },
            )
