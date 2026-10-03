# Security policy

## Reporting something you found

Use **"Report a vulnerability"** on this repository's Security tab. It opens a
private thread with the maintainer and nothing is public until there is
something to publish.

If you would rather email, the address is published at
`https://waytrace.org/.well-known/security.txt`. It is not written here on
purpose: this file ships inside every self-hosted copy, and the contact for
your own install is you, not the person who runs waytrace.org.

Please do not open a public issue for a suspected vulnerability.

There is no bounty. The project is free and unfunded. What there is: an answer
from a person, and credit under whatever name you prefer.

## Testing

**Your own install: go ahead.** Clone it, run it, break it, that is what the
code is for. Nothing you do there affects anyone else.

**waytrace.org: please ask first.** Say what you want to look at and where you
will be testing from, and we will agree a scope before you start.

The reason is not secrecy, and it is not that anything here is fragile. It is
what sits on that server. The scans on it are investigations in progress, run
by journalists and researchers, and a report link is a capability: whoever
holds it sees who was being looked into. A scan that dies because the queue was
being hammered is somebody's work lost. That data is not mine to gamble with,
so I would rather know.

There is also a practical side. Unannounced, I cannot tell your testing apart
from an attack, so I have to treat it as one: reading logs at midnight, warning
users, tightening limits that then get in everyone's way. Announced, I can
watch while you work, tell the affected people that nothing is wrong, and read
what you find as a finding instead of an incident.

## In scope

The code in this repository, and waytrace.org once a scope is agreed.

What is actually interesting here: authentication and session handling, reading
or altering another account's scans or reports, injection of any kind, anything
that lets one user affect another's scan, and the handling of the data a scan
produces.

## Out of scope

Please do not send these, they will be closed without much of an answer:

- Load generation, denial of service, volumetric or stress testing of any kind.
- Anything aimed at archive.org. It is a third party this project only reads
  from, and it owes you nothing.
- Raw scanner output with no demonstrated impact, and reports where the proof
  of concept does not run.
- Missing headers, TLS configuration grades, SPF and DMARC observations, and
  similar hardening notes with no exploit path attached.
- Findings that require a compromised device, a malicious browser extension, or
  physical access.
- Content found inside somebody's scan results. That is archived material from
  the open web, not a vulnerability in this tool.

## What to expect

An acknowledgement within 5 days and a real assessment within 14. Fix timing
depends on severity and on one person's week. Whatever happens, you will be
told where it stands rather than left in silence.

## Safe harbour

If you follow this policy, report promptly, keep to the agreed scope, and do
not access, modify or retain anyone else's data, then your work is authorised
and no action will be taken over it. If you are unsure whether something is in
scope, ask before you try it.
