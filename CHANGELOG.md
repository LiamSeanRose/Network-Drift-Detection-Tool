# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Tunnel / overlay drift** (v4.75 track). An optional top-level `tunnels` block
  in the normalized schema; per-vendor tunnel collection (Arista, Cisco, Junos,
  Nokia); tunnel intent from NetBox `local_context_data`; differ rules that raise
  `critical` when intent says a tunnel is up but it is down, and `warning` for
  config mismatches; and bundled tunnel patterns.
- **Edge-triggered SLA alerting.** `sla_breached` fires once when a breach opens
  and `sla_resolved` once when the drift clears, instead of re-firing every
  scheduler cycle. Backed by an `sla_breach_state` table.
- **Severity-aware SLA payloads.** Breach and resolve webhooks carry structured
  `severity`, `fingerprint`, `object`, `field`, and `window_minutes` so a
  receiver can route by tier; the resolve event carries the same severity as the
  breach that opened it.
- **Per-object-type SLA windows.** An alert rule can scope to one object type
  (interface, vlan, bgp_neighbor, …), so interfaces and VLANs on the same device
  can carry different SLAs.
- **Maintenance windows.** Time-boxed, whole-device change windows that suppress
  SLA alerting and auto-apply while active — the time-boxed sibling of
  acknowledgement. `maintenance_windows` table, `POST`/`GET`/`DELETE
  /maintenance-windows`, and a dashboard panel.
- **Drift triage (new vs chronic).** `GET /drifts` reports each drift's
  `first_seen` and a `triage` flag (`new` if first seen within the hour, else
  `chronic`), so a fresh problem stands out from long-standing noise.
- **AI assist** — opt-in, **off by default**, local model by default, grounded
  with a deterministic fallback, sharing one `NETDRIFT_EXPLAIN_*` config:
  natural-language drift explanations (AI1, cached per fingerprint, generated on
  the scheduler cycle), remediation summaries on dry-run (AI2), a suggested
  cause/fix that pre-fills the record-fix form (AI3), and incident-vs-noise
  anomaly triage (AI4). Each falls back to a deterministic result and never
  calls the network when off.
- **`driftcheck demo`** — run the diff engine over a bundled two-device network
  with no NetBox, device, or database; `--seed` loads it into the dashboard.

### Changed
- **Redesigned dashboard** into a dark network-ops console: summary stat cards,
  severity pills, device status dots, a sticky table header, a responsive layout,
  and AI / triage callouts in the expanded row.

### Fixed
- Dashboard writes failed silently when no API key was set or a route was not
  proxied. Failures now surface an error banner, and every panel route is proxied.
- The drift-history sparkline and the drift table no longer force a horizontal
  page scroll, and the sticky table header no longer overlaps the first row.

## [4.0.0] - 2026-06-01 — Community Pattern Library

### Added
- **Bundled drift patterns** (`patterns/`, 20 files: interface, VLAN, BGP, OSPF)
  validated against a Pydantic `PatternSchema`. The loader computes each
  pattern's fingerprint with the differ's own function, so a pattern only ever
  matches drift the differ actually produces.
- **`driftcheck import-patterns`** — idempotent upsert of patterns into
  `known_issues`; imports always land with auto-apply **off**.
- **`driftcheck validate-patterns`** + a CI step — schema and
  fingerprint-collision validation with no database, so adding a valid pattern
  needs no Python change and a malformed one fails the build.
- **`GET /known-issues/export`** — round-trippable YAML export of the knowledge
  base (requires an API key).
- `patterns/README.md` field reference and a `CONTRIBUTING.md` pattern-submission
  section.

## [3.5.0] - 2026-06-01 — Security + SLA + Acknowledge

### Added
- **API-key authentication.** `api_keys` table + migration; an `X-API-Key`
  middleware that requires a valid key on every mutating request (401 otherwise)
  while leaving `GET /drifts` and `/health` public by design; `POST`/`GET`/
  `DELETE /api-keys` endpoints (raw key shown once, only its SHA-256 hash
  stored); and a `driftcheck create-api-key` CLI to bootstrap the first key.
- **Per-device drift SLA.** `alert_rules` table + `POST`/`GET`/`DELETE
  /alert-rules`; an `evaluate_sla` evaluator (injectable clock) wired into the
  scheduler that fires a `sla_breached` webhook for unresolved, unacknowledged
  drift older than a rule's window; and `device_unreachable` detection
  (`device_settings.last_collected_at`, stamped on every successful collection)
  so stale drift on a silent device raises `device_unreachable` instead of a
  false breach.
- **Drift acknowledgement.** `acknowledgements` table keyed by
  `(device, fingerprint)`; `POST`/`DELETE /drifts/{id}/acknowledge` (optional
  expiry, capped at 90 days). Active acknowledgements suppress webhook dispatch,
  SLA evaluation, and auto-apply; `GET /drifts` carries an `acknowledged` flag.
- **`GET /drifts` query support.** `?since=<ISO8601>` filter and `Link`
  pagination header.
- **Drift retention.** A daily cleanup job pruning events older than
  `DRIFT_RETENTION_DAYS` (default 90) and a composite index on
  `drift_events(device, severity, detected_at)`.
- **Dashboard.** Paste-a-key API-key field; an SLA alert-rules panel; a
  per-device auto-apply Pause/Resume toggle (`GET /devices`); and an
  acknowledge toggle that dims acknowledged rows.

### Changed
- `sla_breached` and `device_unreachable` added to the webhook default events.

## [3.0.0] - 2026-06-01 — Operational Loop

### Added
- Background auto-apply in the scheduler (`netdrift/auto_apply.py`): after each drift
  check, matches drift against `known_issues` where `auto_apply_enabled` and
  `AUTO_REMEDIATION_ENABLED` are set, runs `check_blocked()`, applies via
  `get_applier(platform)`, records a `RemediationEvent`, and schedules a one-shot re-poll
  of the device within 60 seconds.
- Auto-apply safety rails: management-interface blocklist enforced before every apply,
  `restore_intent`-only gate re-checked at execution time, and auto-disable after 3
  consecutive failures for the same known issue.
- Outbound webhook notifications (`netdrift/webhook.py` `WebhookDispatcher`): daemon
  thread draining a bounded queue, SSRF-guarded URL validation (rejects localhost,
  RFC 1918, and link-local unless `WEBHOOK_ALLOW_PRIVATE=true`), firing on new critical
  drift, auto-apply success, and auto-apply failure.
- Per-device auto-apply kill-switch: `device_settings` table and
  `PATCH /devices/{name}/auto-apply` endpoint (API-only; dashboard toggle lands in v3.5).
- API-triggered apply results dispatch webhooks via a FastAPI `BackgroundTasks` wrapper.
- Juniper JunOS support (parallel v3.75 track): `collectors/junos.py` and
  `appliers/junos.py` with `commit confirmed` auto-rollback.

### Changed
- Scheduler now uses structured `logging` with APScheduler `EVENT_JOB_EXECUTED` /
  `EVENT_JOB_ERROR` listeners in place of `print()`.
- `docker-compose.yml` and `.env.example` carry `AUTO_REMEDIATION_ENABLED`,
  `WEBHOOK_URL`, `WEBHOOK_EVENTS`, and `restart: unless-stopped` on `api` and `scheduler`.
- Deployment hardening: removed default credentials, bound Postgres to localhost, dropped
  root in the container; added a security policy and deployment guide.

### Fixed
- `storage/database.py` engine/sessionmaker memoized as singletons (no longer opens a new
  connection pool per poll cycle).
- Cisco applier now fails an apply that does not converge instead of reporting success.
- NetBox client resolves interface IPs in one query instead of N+1.

## [2.5.0] - 2026-05-31 — Opt-in Auto-Remediation

### Added
- Appliers for Arista, Cisco, and Nokia (`restore_intent` + `raw_snippet`, dry-run via
  config compare) behind a lazy, fault-isolated `get_applier(platform)` registry.
- `remediation` JSONB column on `known_issues` and an append-only `remediation_events`
  audit table.
- `POST /known-issues/{id}/remediate/dry-run` and `/apply` endpoints.
- Confirm-N gating (default threshold 3) and a global `AUTO_REMEDIATION_ENABLED`
  kill-switch (default off).
- Suggest/apply UI with dry-run diff display and an audit log.
- Hard do-not-auto-apply enforcement for management interfaces, AAA, symptom fields,
  identity, and undocumented deletes.

## [2.0.0] - 2026-05-31 — Knowledge Base

### Added
- `netdrift/fingerprint.py` stable drift signature (`object_type|field|drift_kind`).
- `known_issues` table; record-cause-and-fix workflow; recurrence matching across
  devices/IPs; `known_fix` field on `GET /drifts`; known-fix callout and Record-fix modal.

## [1.5.0] - 2026-05-31 — Static Diagnosis

### Added
- `netdrift/diagnose.py` rules engine; ~30 diagnosis rules across interfaces, VLANs, BGP,
  OSPF, and config; `causes` field on `GET /drifts`; expandable causes row in the UI.

## [1.0.0] - 2026-05-31 — Detector Complete

### Added
- Config-level drift: `running_config` schema field plus differ and intent-side rendering.
- Plugin architecture (`collectors/base.py` + `collectors/registry.py`) so a vendor can be
  added without editing core dispatch.
- MkDocs documentation site (GitHub Pages) and a Helm chart for Kubernetes deployment.

## [0.3.0] - 2026-05-27 — Production-ish

### Added
- BGP neighbor and OSPF adjacency drift in the schema and differ.
- Drift history/trends in the UI.
- Cisco IOS-XE collector (validated against physical hardware).
- Syslog receiver that triggers an immediate targeted poll.
- Nautobot supported as an alternative to NetBox.

## [0.2.0] - 2026-05-24 — Persist + UI

### Added
- Postgres `drift_events` history (queryable with timestamps).
- Nokia SR Linux as a second vendor.
- VLAN fields (`mode`, `untagged_vlan`, `tagged_vlans`, top-level `vlans`) in schema/diff.
- React dashboard served by a FastAPI backend.
- APScheduler polling every 1–5 minutes.
- `docker compose up` brings up the full stack.

## [0.1.0] - 2026-05-20 — Detector PoC

### Added
- `driftcheck` CLI: pull intent from NetBox, reality from Arista cEOS, diff interface
  description / enabled state / IP addresses, and print drift.
- Pure `differ.py` with unit tests; CI running ruff + pytest.
- Containerlab topology and `seed_netbox.py` to reproduce the environment.

[4.0.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v4.0
[3.5.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v3.5
[3.0.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v3.0
[2.5.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v2.5
[2.0.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v2.0
[1.5.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v1.5
[1.0.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v1.0
[0.3.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v0.3
[0.2.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v0.2
[0.1.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v0.1
