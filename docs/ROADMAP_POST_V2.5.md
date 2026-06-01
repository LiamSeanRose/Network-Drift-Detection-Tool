# Post-v2.5 Roadmap

> **Status:** Draft — not yet integrated into `docs/PROJECT_PLAN.md`.
> These are agreed candidate directions, not committed scope. Versions, DoD items,
> and ownership assignments should be reviewed jointly by Matthew and Liam before
> any branch is created.
>
> **Last updated:** 2026-05-31

---

## Overview

v2.5 completes the core remediation loop: detect → diagnose → suggest fix → dry-run →
apply → verify. Everything from here builds on that foundation.

Seven directions are proposed, grouped into three tiers by value vs effort.

| Version | Theme | Tier | Est. (part-time, 2 people) |
|---------|-------|------|---------------------------|
| v3.0 | Operational loop | 1 — High value, low effort | 4–6 weeks |
| v3.5 | Security + SLA | 1 — High value, low effort | 4–6 weeks |
| v4.0 | Auth hardening | 2 — Grows project reach | 4–6 weeks |
| v4.5 | Community patterns | 2 — Grows project reach | 6–8 weeks |
| v5.0 | Fuzzy matching | 3 — Genuinely novel | 3–4 months (after corpus) |

---

## Tier 1 — High value, low effort

### v3.0 — Operational loop

**Theme:** Close the auto-remediation loop and make the tool observable outside the UI.

All the enforcement infrastructure is already in place from v2.5 (`auto_apply_enabled`,
`confirmed_count`, kill-switch, `RemediationEvent` audit log). v3.0 wires the last pieces
together so the tool can fix drift automatically and notify a team when it does.

**Features:**

1. **Background auto-apply in the scheduler**
   The scheduler already polls every device on a timer. The new behaviour: after each
   drift check, cross-reference the results against `known_issues` where
   `auto_apply_enabled = true` and `AUTO_REMEDIATION_ENABLED` is set. For each match,
   call `get_applier(platform)` and apply — subject to the same `check_blocked()` guard
   that the manual API path uses. Record a `RemediationEvent` and schedule a re-poll.
   Roughly one new function in `scheduler.py`; the rest is already built.

2. **Webhook / notification output**
   Fire an outbound HTTP POST when a significant event occurs: new critical drift
   detected, auto-apply succeeded, auto-apply failed, apply failure rate threshold
   exceeded. Receivers: Slack incoming webhook, Teams, PagerDuty, or any generic URL.
   Config: `WEBHOOK_URL` + `WEBHOOK_EVENTS` env vars. Uses FastAPI's background tasks
   so the scheduler cycle is not blocked.

3. **Drift acknowledgement**
   Let an engineer mark a drift event as acknowledged — "this is intentional, stop
   alerting on it" — with an optional expiry time. Acknowledged drift is dimmed in the
   UI and suppressed from webhook alerts. One new column on `drift_events`; one API
   endpoint; one frontend toggle.

**v3.0 Ownership**

| Work stream | Owner | Notes |
|---|---|---|
| Background auto-apply loop in `scheduler.py` | Matthew (B) | Cross-references `known_issues.auto_apply_enabled` against current drift; calls `get_applier()` from Liam's registry |
| Webhook client + background dispatch | Matthew (B) | `api/webhooks.py`; fires from scheduler and from `remediate/apply` endpoint |
| `acknowledged` + `acknowledged_until` columns on `drift_events` + migration | Matthew (B) | Nullable expiry; scheduler skips acknowledged events |
| `POST /drifts/{id}/acknowledge` endpoint | Matthew (B) | Accepts `{expires_at: ISO str \| null}` |
| Acknowledge toggle + dimmed display in frontend | Matthew (B) | Greyed row; "Ack until: …" label |
| Webhook trigger on apply result | Matthew (B) | Reuses webhook client; fires `apply_success` / `apply_failure` |
| Confirm management-interface blocklist is complete (per vendor) | Liam (A) | Already in `appliers/`; verify before auto-apply loop ships to avoid accidental mgmt-iface remediations |

**Paired seam:** the auto-apply loop calls `get_applier()` — the same seam agreed for
v2.5. No new interface required.

**v3.0 Definition of Done**

- [ ] Scheduler auto-applies matching drift when `auto_apply_enabled = true` and global kill-switch is on.
- [ ] Every auto-apply is recorded in `remediation_events` and triggers a post-apply re-poll.
- [ ] At least one webhook receiver (Slack) fires on new critical drift and on apply result.
- [ ] Engineers can acknowledge drift events with an optional expiry; acknowledged events are suppressed from alerts.

---

### v3.5 — Security + SLA

**Theme:** Make the tool safe to run in a shared environment and give oncall a reason to trust it.

**Features:**

4. **Per-device drift SLA / alerting rules**
   Let operators configure thresholds: "if `core-sw-01` has any `critical` drift for
   more than 10 minutes, fire an alert." A thin rules layer on top of the existing
   history table — no schema change to the core drift record. Alert dispatch reuses the
   webhook client from v3.0.

5. **REST API authentication (API keys)**
   Right now the API has zero authentication. Any client that can reach the port can
   write to `/known-issues` or trigger a remediation. A simple API-key middleware
   (one header check, keys stored hashed in Postgres) is the prerequisite for any real
   deployment and for the community pattern library in v4.5.

**v3.5 Ownership**

| Work stream | Owner | Notes |
|---|---|---|
| `alert_rules` table + migration | Matthew (B) | Columns: `device` (nullable = all devices), `severity`, `duration_minutes`, `enabled` |
| SLA evaluation on each scheduler cycle | Matthew (B) | Query drift history; compare against rules; dispatch via webhook client |
| `POST/GET/DELETE /alert-rules` endpoints | Matthew (B) | CRUD for operator-defined rules |
| Alert rules UI (simple list + form) | Matthew (B) | New panel in dashboard |
| API key table + migration | Matthew (B) | `api_keys`: `id`, `key_hash` (SHA-256), `name`, `created_at`, `last_used_at` |
| FastAPI auth middleware | Matthew (B) | `X-API-Key` header; exempt `/health`; return 401 on missing/invalid |
| Key management endpoints (`POST /api-keys`, `DELETE /api-keys/{id}`) | Matthew (B) | Admin-only scope (first key bootstrapped via env var or CLI) |
| Lab validation of SLA timing against real poll cadence | Liam (A) | Confirm timing edge cases don't produce false alerts in the lab environment |

**v3.5 Definition of Done**

- [ ] SLA rules fire webhook alerts when drift persists beyond the configured window.
- [ ] All write endpoints (`POST /known-issues`, `PATCH`, `POST .../remediate/*`) require a valid API key.
- [ ] `/health` and `GET /drifts` remain unauthenticated (read-only public endpoints).
- [ ] A key can be created, listed, and revoked via the API.

---

## Tier 2 — Grows the project's reach

### v4.0 — Community pattern library

**Theme:** Make the knowledge base valuable on day one for a new user, without needing
weeks of real drift data flowing through their instance.

The "north star" item from `docs/PROJECT_PLAN.md`. A curated, version-controlled set
of `known_issues` records (fingerprint + cause + fix + optional remediation payload)
that ships with the tool. A contributor opens a PR adding a `patterns/` YAML file.
Users import it on install or with a CLI command.

**v4.0 Ownership**

| Work stream | Owner | Notes |
|---|---|---|
| `patterns/` YAML schema design | Both (joint sign-off) | Mirrors `known_issues` shape; versioned; vendor-tagged |
| Pattern loader CLI (`driftcheck import-patterns`) | Matthew (B) | Reads YAML; upserts into `known_issues`; idempotent |
| `GET /known-issues/export` endpoint | Matthew (B) | Exports current `known_issues` as importable YAML |
| Initial bundled patterns (interfaces, VLANs, BGP, OSPF) | Both | Each partner contributes patterns observed in the lab |
| Vendor accuracy review on contributed patterns | Liam (A) | Validate command snippets and field names before merging |
| `CONTRIBUTING.md` section for pattern PRs | Both | Agree review criteria; add CI lint for YAML schema |

**v4.0 Definition of Done**

- [ ] `patterns/` directory ships with at least 20 curated patterns covering interfaces, VLANs, BGP, and OSPF.
- [ ] `driftcheck import-patterns` loads them into a fresh instance in under 5 seconds.
- [ ] A contributor can add a new pattern via PR without touching Python code.
- [ ] CI validates YAML schema on every PR.

---

### v4.5 — Per-device drift SLA (advanced) and Juniper support

**Theme:** Round out vendor coverage and deepen the SLA layer with per-interface and
per-prefix granularity.

*(Placeholder — scope to be agreed jointly before any branch is created.)*

---

## Tier 3 — The genuinely novel direction

### v5.0 — Fuzzy / semantic fingerprint matching

**Theme:** The hard, original engineering that separates this tool from every other
drift detector that already exists.

The current fingerprint is `object_type|field|drift_kind` — an exact string match.
A real-world knowledge base needs *fuzzy* matching: "this BGP session-state drift on
`10.0.0.x` is the same pattern as the one we fixed on `10.1.1.x` last month, on a
different device in a different site." The fingerprint needs to abstract away the
variable parts (IPs, hostnames, VLAN IDs) while retaining the structural signature
of the drift.

**Why this is hard:**
- Too strict: every IP variation is treated as a new unknown issue, defeating the
  point of the knowledge base.
- Too loose: unrelated drift patterns are incorrectly matched and the wrong fix is
  suggested — or worse, auto-applied.
- The right similarity threshold is corpus-dependent. It cannot be designed well
  without real drift data.

**Prerequisites:** at least 2–3 months of production-scale drift data flowing through
a real deployment. Do not build this first.

**v5.0 Ownership**

> **Do not split this work.** Pair on the algorithm design before writing any code.
> The implementation touches `fingerprint.py` (Person B's territory) but the design
> requires Person A's understanding of which fields carry vendor-specific variance.

| Work stream | Owner | Notes |
|---|---|---|
| Fuzzy fingerprint algorithm design | Both (paired) | Agree on similarity metric and threshold before any code |
| Corpus analysis — which fields vary per-vendor vs per-instance | Liam (A) | Input to the algorithm design; needs real lab data |
| `fingerprint.py` fuzzy implementation | Both (paired) | Likely TF-IDF, Jaccard, or a small embedding model depending on corpus size |
| Matching threshold tuning | Both (paired) | Requires real drift corpus to evaluate false-positive rate |
| Migration to re-fingerprint existing `known_issues` | Matthew (B) | One-off migration script; must be reversible |
| UI: confidence score display on known-fix matches | Matthew (B) | Show "85% match" rather than exact-match binary |

**v5.0 Definition of Done**

- [ ] A known issue recorded for device A matches a structurally identical drift on device B, even when IP addresses differ.
- [ ] False-positive rate (wrong match) is < 5% on the lab corpus.
- [ ] Exact-match fingerprints still work (backward compat — zero regression on existing known issues).
- [ ] Confidence score is surfaced in the UI and in the `GET /drifts` response.

---

## Recommended starting point

**Start with v3.0 — background auto-apply.** It is the direct payoff of everything
built in v2.5, it requires the smallest amount of new code (one scheduler function),
and it lets you demo the full closed loop:

```
detect → diagnose → suggest fix → dry-run → apply → verify → auto-apply on recurrence
```

That closed loop is the tool's headline story and the thing that makes it a portfolio
piece worth showing.
