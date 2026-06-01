# Security Policy

netdrift reads intended state from NetBox/Nautobot, reads live state from network
devices, and — when remediation is enabled — **writes configuration back to those
devices**. A bug in the apply path or the API that fronts it can affect a whole
managed network, so we take security reports seriously and ask that they reach us
privately first.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it through one of these private channels:

1. **GitHub private vulnerability reporting** (preferred) — go to the
   **Security** tab of this repository and choose **Report a vulnerability**.
   This opens a private advisory visible only to the maintainers.
2. **Email** — `liam.sean.rose@gmail.com` with `netdrift security` in the subject.

Please include: affected version or commit, a description of the issue and its
impact, and the minimal steps or conditions needed to reproduce it. A proof of
concept helps but is not required.

### What to expect

- **Acknowledgement** within 3 business days.
- An initial assessment (severity, whether we can reproduce it) within 7 business
  days.
- We will keep you updated as we work on a fix and will credit you in the release
  notes once it ships, unless you prefer to remain anonymous.

We ask that you give us a reasonable window to release a fix before any public
disclosure.

## Supported versions

netdrift is pre-1.0 for public deployment. Security fixes are applied to the
latest released version on `main`; older tags are not patched. Track `main` for
security updates.

## Known posture (not vulnerabilities)

Some current behaviour is intentional and already on the roadmap — reporting it
as a vulnerability is welcome but will be treated as a duplicate of known work:

- **The HTTP API ships without authentication before v3.5.** Authentication and
  authorization are roadmapped for the v3.5 release. Until then, the API — and in
  particular any endpoint that triggers a remediation apply — **must not be
  exposed to an untrusted network**. Run it behind your own authentication layer,
  or expose only the read-only, non-mutating deployment (demo mode).
- **Remediation apply is off by default.** Pushing configuration to a device is
  gated behind explicit opt-in (`AUTO_REMEDIATION_ENABLED`, a per-issue
  `auto_apply_enabled` flag, and a per-device pause). Operational-symptom fields
  and management interfaces are on a hard do-not-apply list.

If you find a way to bypass one of these controls — for example, triggering an
apply that should have been blocked, or reaching a mutating endpoint that should
have been gated — that **is** a vulnerability. Please report it.

## Deploying safely

Until v3.5 authentication lands, the safe public deployment is the read-only
instance with no device-mutating surface exposed. Operator hardening guidance
(secrets handling, network exposure, TLS) lives in the project documentation.
