# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[3.0.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v3.0
[2.5.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v2.5
[2.0.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v2.0
[1.5.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v1.5
[1.0.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v1.0
[0.3.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v0.3
[0.2.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v0.2
[0.1.0]: https://github.com/LiamSeanRose/Network-Drift-Detection-Tool/releases/tag/v0.1
