"""OSINT-value-ranked highlights from extraction results.

Severity labels:
  LEAK      . actual sensitive exposure the owner didn't mean to publish.
  PIVOT     . lead for further investigation (pivots to linked entities).
  CONTEXT   . useful background for understanding the target.
  BACKGROUND. noise-tier, listed for completeness, never highlighted.
"""
from __future__ import annotations

import re


# Lower integer = higher priority. BACKGROUND is never highlighted.
SEVERITY_ORDER = {"LEAK": 0, "PIVOT": 1, "CONTEXT": 2, "BACKGROUND": 3}

# Common public mailboxes that don't carry PIVOT weight.
_GENERIC_EMAIL_LOCAL_PARTS = (
    "info@", "contact@", "support@", "hello@", "team@", "office@",
    "marketing@", "press@", "sales@", "rgpd@", "dpo@", "careers@",
    "handicap@", "accessibilite@", "hr@", "recruiting@",
)

_INTERESTING_ENDPOINT_RE = re.compile(
    r"^/(api|admin|login|auth|dashboard|internal|staging|debug|graphql)",
    re.IGNORECASE,
)


def compute_highlights(results: dict, domain: str) -> list[dict]:
    """Analyze extraction results and generate prioritized OSINT highlights."""
    highlights: list[dict] = []

    def _add(severity: str, category: str, title: str, detail: str, pivot_tip: str) -> None:
        highlights.append({
            "severity": severity,
            "category": category,
            "title": title,
            "detail": detail,
            "pivot_tip": pivot_tip,
        })

    # ----- LEAK: things the target didn't mean to publish ----------------
    api_keys = results.get("api_keys", [])
    secret_keys = [k for k in api_keys if k.get("tier", "secret") == "secret"]
    public_keys = [k for k in api_keys if k.get("tier") == "public"]
    if secret_keys:
        types = set(k.get("type", "Unknown") for k in secret_keys)
        _add(
            "LEAK", "api_keys",
            f"{len(secret_keys)} secret API key(s) exposed ({', '.join(sorted(types))})",
            ", ".join(k.get("value", "")[:20] + "..." for k in secret_keys[:5]),
            "Test if key is still active; rotate if you own it",
        )
    if public_keys:
        types = set(k.get("type", "Unknown") for k in public_keys)
        _add(
            "PIVOT", "api_keys_public",
            f"{len(public_keys)} public-by-design API key(s) ({', '.join(sorted(types))})",
            ", ".join(k.get("value", "")[:20] + "..." for k in public_keys[:5]),
            "Frontend SDK / OAuth client IDs cluster sites on the same operator account",
        )

    cloud_buckets = results.get("cloud_buckets", [])
    if cloud_buckets:
        _add(
            "LEAK", "cloud_buckets",
            f"{len(cloud_buckets)} cloud bucket(s) exposed",
            ", ".join(b.get("value", "") for b in cloud_buckets[:5]),
            "Check bucket permissions (aws s3 ls, gsutil ls)",
        )

    cred_conns = [c for c in results.get("connection_strings", []) if c.get("has_credentials")]
    if cred_conns:
        types = set(c.get("type", "") for c in cred_conns)
        _add(
            "LEAK", "connection_strings",
            f"{len(cred_conns)} connection string(s) with credentials ({', '.join(sorted(types))})",
            ", ".join(c.get("value", "")[:60] for c in cred_conns[:3]),
            "Credentials were in the archived HTML. assume compromised, rotate",
        )

    dirlistings = results.get("directory_listings", [])
    if dirlistings:
        _add(
            "LEAK", "directory_listings",
            f"{len(dirlistings)} directory listing(s) exposed in archive",
            ", ".join(d.get("path", "") for d in dirlistings[:5]),
            "Check if the listed files are still reachable",
        )

    sensitive_jwts = [j for j in results.get("jwt_tokens", []) if j.get("sensitive_claims")]
    if sensitive_jwts:
        _add(
            "LEAK", "jwt_tokens",
            f"{len(sensitive_jwts)} JWT token(s) with sensitive claims",
            ", ".join(", ".join(j.get("sensitive_claims", []))[:60] for j in sensitive_jwts[:3]),
            "Decode claims; if alg is 'none' or RS256→HS256 is accepted, test forged signatures",
        )

    ips = results.get("internal_ips", [])
    if ips:
        _add(
            "LEAK", "internal_ips",
            f"{len(ips)} internal IP address(es) leaked",
            ", ".join(i.get("ip", "") for i in ips[:5]),
            "169.254.169.254 = AWS IMDS SSRF evidence; RFC1918 IPs map internal network topology",
        )

    # ----- PIVOT: the next breadcrumb ------------------------------------
    subdomains = results.get("subdomains", [])
    if subdomains:
        _add(
            "PIVOT", "subdomains",
            f"{len(subdomains)} subdomain(s) discovered",
            ", ".join(s.get("value", "") for s in subdomains[:5]),
            "Pivot via crt.sh / Shodan / DNS brute. archive.org lists ≠ live surface",
        )

    # Named-mailbox emails (jane.doe@, j-smith@) are PIVOT; generic
    # info@/contact@/support@ are CONTEXT and shown in that bucket below.
    emails = results.get("emails", [])
    domain_emails = [e for e in emails if e.get("value", "").endswith(f"@{domain}")]
    named_emails = [
        e for e in domain_emails
        if not any(e["value"].startswith(g) for g in _GENERIC_EMAIL_LOCAL_PARTS)
        and ("." in e["value"].split("@", 1)[0] or "-" in e["value"].split("@", 1)[0])
    ]
    if named_emails:
        _add(
            "PIVOT", "emails",
            f"{len(named_emails)} named mailbox(es) @{domain}",
            ", ".join(e.get("value", "") for e in named_emails[:5]),
            "Cross-check on HIBP, LinkedIn, GitHub commits. Staff naming scheme = guessable peers",
        )

    endpoints = results.get("endpoints", [])
    interesting_endpoints = [e for e in endpoints if _INTERESTING_ENDPOINT_RE.match(e.get("path", ""))]
    if interesting_endpoints:
        _add(
            "PIVOT", "endpoints",
            f"{len(interesting_endpoints)} admin / API / auth endpoint(s) archived",
            ", ".join(e.get("path", "") for e in interesting_endpoints[:5]),
            "Check each path live. old staging paths often survive deploys",
        )

    trackers = results.get("analytics_trackers", [])
    if trackers:
        types = set(t.get("type", "") for t in trackers)
        _add(
            "PIVOT", "analytics_trackers",
            f"{len(trackers)} analytics tracker ID(s) ({', '.join(sorted(types))})",
            ", ".join(f"{t.get('type', '')}:{t.get('id', '')}" for t in trackers[:5]),
            "Same tracker ID across domains ⇒ same operator. Use PublicWWW / BuiltWith for pivot",
        )

    persons = results.get("persons", [])
    if persons:
        _add(
            "PIVOT", "persons",
            f"{len(persons)} named person(s)",
            ", ".join(p.get("name", "") for p in persons[:5]),
            "Feed into LinkedIn/Intelligence X; confirm role via archive snapshots",
        )

    changed_techs = [
        t for t in results.get("technologies", [])
        if t.get("first_seen") != t.get("last_seen")
    ]
    if changed_techs:
        details = ", ".join(
            f"{t.get('technology', '')} ({t.get('first_seen', '')} → {t.get('last_seen', '')})"
            for t in changed_techs[:3]
        )
        _add(
            "PIVOT", "technologies",
            f"{len(changed_techs)} technology change(s) over time",
            details,
            "Find the migration window; check old version for CVEs still live on unmerged forks",
        )

    jwts = results.get("jwt_tokens", [])
    plain_jwts = [j for j in jwts if not j.get("sensitive_claims")]
    if plain_jwts:
        _add(
            "PIVOT", "jwt_tokens",
            f"{len(plain_jwts)} JWT token(s) found",
            f"alg seen: {', '.join(sorted({j.get('alg', '?') for j in plain_jwts}))}",
            "Decode claims for user/role hints; archive.org often caches debug tokens",
        )

    changed_favs = [
        f for f in results.get("favicons", [])
        if f.get("first_seen") != f.get("last_seen")
    ]
    if changed_favs:
        _add(
            "PIVOT", "favicons",
            f"{len(changed_favs)} favicon change(s) detected",
            ", ".join(f.get("url", "")[:60] for f in changed_favs[:3]),
            "Hash the favicon with mmh3 → Shodan http.favicon.hash: and Censys services.http.response.favicons.hash",
        )

    crypto = results.get("crypto_addresses", [])
    if crypto:
        types = set(c.get("type", "") for c in crypto)
        _add(
            "PIVOT", "crypto_addresses",
            f"{len(crypto)} cryptocurrency address(es) ({', '.join(sorted(types))})",
            ", ".join(c.get("address", "")[:20] + "..." for c in crypto[:3]),
            "Trace on blockchain explorer; same address across domains ⇒ same operator",
        )

    verif = results.get("verification_tags", [])
    if verif:
        services = set(v.get("service", "") for v in verif)
        _add(
            "PIVOT", "verification_tags",
            f"{len(verif)} domain verification tag(s) ({', '.join(sorted(services))})",
            ", ".join(f"{v.get('service', '')}:{v.get('verification_id', '')[:20]}" for v in verif[:3]),
            "Some services (google-site-verification) chain to the registrant account",
        )

    adsense = results.get("adsense_ids", [])
    if adsense:
        pubs = [a for a in adsense if a.get("type") == "adsense_publisher"]
        if pubs:
            _add(
                "PIVOT", "adsense_ids",
                f"{len(pubs)} Adsense publisher ID(s)",
                ", ".join(f"ca-pub-{a.get('id', '')}" for a in pubs[:3]),
                "Pub IDs cluster sites owned by the same operator (spyonweb, publicwww)",
            )

    hidden = results.get("hidden_fields", [])
    if hidden:
        _add(
            "PIVOT", "hidden_fields",
            f"{len(hidden)} hidden form field(s)",
            ", ".join(f"{h.get('name', '')}={h.get('value', '')[:30]}" for h in hidden[:3]),
            "Check for CSRF tokens, workflow states, internal IDs",
        )

    sensitive_js = [
        u for u in results.get("js_urls", [])
        if any(p in u.get("url", "").lower() for p in ("/api", "/internal", "/admin", "/staging", "/debug"))
    ]
    if sensitive_js:
        _add(
            "PIVOT", "js_urls",
            f"{len(sensitive_js)} sensitive URL(s) in JavaScript",
            ", ".join(u.get("url", "")[:80] for u in sensitive_js[:3]),
            "Archive.org caches unminified bundles. scan for endpoints / API shape clues",
        )

    github_repos = results.get("github_repos", [])
    if github_repos:
        _add(
            "PIVOT", "github_repos",
            f"{len(github_repos)} GitHub repo(s) referenced",
            ", ".join(g.get("pivot_url", "") for g in github_repos[:5]),
            "Browse commits and contributor list. Same owner across domains ⇒ same operator",
        )

    pgp_keys = results.get("pgp_keys", [])
    if pgp_keys:
        kinds = sorted({k.get("kind", "") for k in pgp_keys})
        _add(
            "PIVOT", "pgp_keys",
            f"{len(pgp_keys)} PGP material reference(s) ({', '.join(kinds)})",
            ", ".join(k.get("identifier", "") for k in pgp_keys[:3]),
            "Lookup fingerprint on keys.openpgp.org / keybase to map to identities",
        )

    # security.txt and ads.txt are PIVOT-grade discoveries: security.txt
    # exposes a contact channel + scope; ads.txt links to ad networks.
    pivot_disclosures = [
        s for s in results.get("sitemaps_and_robots", [])
        if s.get("kind") in ("security", "ads")
    ]
    if pivot_disclosures:
        kinds = sorted({s.get("kind", "") for s in pivot_disclosures})
        _add(
            "PIVOT", "sitemaps_and_robots",
            f"{len(pivot_disclosures)} disclosure file(s) ({', '.join(kinds)})",
            ", ".join(s.get("url", "")[:80] for s in pivot_disclosures[:3]),
            "Fetch directly: security.txt → researcher contact; ads.txt → ad network pivots",
        )

    # ----- CONTEXT: understanding the target -----------------------------
    hosts = results.get("hosting", [])
    if hosts:
        providers = [h.get("provider", "") for h in hosts]
        _add(
            "CONTEXT", "hosting",
            f"Hosting signals: {', '.join(providers)}",
            ", ".join(providers),
            "Identifies infrastructure provider; informs follow-up scope",
        )

    cookie_consent = results.get("cookie_consent", [])
    if cookie_consent:
        platforms = sorted({c.get("platform", "") for c in cookie_consent})
        _add(
            "CONTEXT", "cookie_consent",
            f"CMP detected: {', '.join(platforms)}",
            ", ".join(
                f"{c.get('platform', '')}:{c.get('account_id', '') or '?'}"
                for c in cookie_consent[:3]
            ),
            "Account-id (when present) clusters sites on the same operator's CMP",
        )

    rss_feeds = results.get("rss_feeds", [])
    if rss_feeds:
        _add(
            "CONTEXT", "rss_feeds",
            f"{len(rss_feeds)} feed(s) discovered",
            ", ".join(f.get("url", "")[:80] for f in rss_feeds[:3]),
            "Feeds list publication cadence and authors; cross-reference with persons",
        )

    # Sitemaps + robots that are NOT security/ads (those went into PIVOT above).
    informational_sitemaps = [
        s for s in results.get("sitemaps_and_robots", [])
        if s.get("kind") not in ("security", "ads")
    ]
    if informational_sitemaps:
        kinds = sorted({s.get("kind", "") for s in informational_sitemaps})
        _add(
            "CONTEXT", "sitemaps_and_robots",
            f"{len(informational_sitemaps)} sitemap/robots/humans file(s) ({', '.join(kinds)})",
            ", ".join(s.get("url", "")[:80] for s in informational_sitemaps[:3]),
            "Sitemaps enumerate site structure; humans.txt sometimes exposes the team",
        )

    # ----- BACKGROUND: bundled once so the panel isn't cluttered ---------
    socials = results.get("social_profiles", [])
    outgoing_other = [
        o for o in results.get("outgoing_links", [])
        if o.get("category") == "other"
    ]
    if socials or outgoing_other:
        parts = []
        if socials:
            parts.append(f"{len(socials)} social profile(s)")
        if outgoing_other:
            parts.append(f"{len(outgoing_other)} external link(s)")
        _add(
            "BACKGROUND", "outreach",
            "Public-web presence: " + " + ".join(parts),
            (", ".join(f"{s.get('platform', '')}:{s.get('handle', '')}" for s in socials[:3])
             if socials else ""),
            "Useful for profile confirmation and content-timing checks",
        )

    highlights.sort(key=lambda h: (SEVERITY_ORDER.get(h["severity"], 99), h["category"]))
    return highlights
