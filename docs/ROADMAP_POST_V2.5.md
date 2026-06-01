# Post-v2.5 Execution Plan

> **Status:** Formal — supersedes the draft. Reviewed by design council 2026-06-01.
> Versions, DoD items, and ownership assignments are agreed. A new branch must not be
> created for any version without checking this document first.
>
> **Integrate into `docs/PROJECT_PLAN.md §15`** incrementally as each version ships —
> copy the ownership table and DoD checklist into §15 when the version branch opens.
>
> **Last updated:** 2026-06-01

---

## What Changed from the Draft

Five decisions made by design council that differ from the original rough outline:

1. **Drift acknowledgement moves from v3.0 → v3.5.** An unauthenticated acknowledge
   endpoint is a targeted attack vector: make a config change, suppress the resulting
   alert. It ships in v3.5 alongside the API keys that make it safe.

2. **Webhook dispatch is not FastAPI `BackgroundTasks`.** The scheduler and API are
   separate Docker processes. All scheduler-fired webhooks use a `WebhookDispatcher`
   daemon thread with a bounded `queue.Queue` in `netdrift/webhook.py`. FastAPI
   `BackgroundTasks` only covers the API-triggered dispatch path.

3. **Auto-apply logic goes in `netdrift/auto_apply.py`, not `scheduler.py`.** Putting it
   in `scheduler.py` couples Matthew's module to `get_applier()` and `KnownIssue` (both
   Liam-domain). The new module is called from `pipeline.run_drift_check()` after
   `save_drifts()`.

4. **Per-device kill-switch is a v3.0 must-have.** The global env-var kill-switch requires
   a process restart. When auto-apply is actively harming one device you need to stop it
   immediately. New: `device_settings` table + `PATCH /devices/{name}/auto-apply`. API-only
   in v3.0; dashboard toggle in v3.5.

5. **Juniper JunOS support is a new parallel Liam track (v3.75)**, running concurrently
   with Matthew's v3.0–v3.5 work. It has zero dependency on either of those versions.

---

## Version Overview

| Version | Theme | Tier | Est. (part-time, 2 people) |
|---------|-------|------|---------------------------|
| v3.0 | Operational loop | 1 — High value, low effort | 4–6 weeks |
| v3.5 | Security + SLA + Acknowledge | 1 — High value, low effort | 5–7 weeks |
| v3.75 | Juniper JunOS support | 1 — Liam parallel track | 3 weeks (Liam only) |
| v4.0 | Community pattern library | 2 — Grows project reach | 4–6 weeks |
| v4.5 | Advanced SLA + Juniper advanced | 2 — Grows project reach | 6–8 weeks |
| v5.0 | Fuzzy/semantic fingerprint matching | 3 — Genuinely novel | 3–4 months (after corpus) |

v3.75 runs in parallel with v3.0–v3.5. It targets merge before v4.0 opens.

---

## Tier 1 — High value, low effort

### v3.0 — Operational Loop

**Theme:** Close the auto-remediation loop and make the tool observable outside the UI.

**Dependency gate (definition of ready):**
- v2.5 tagged ✓
- Liam has confirmed the management interface blocklists are complete for all 3 vendors (see
  Blocklist Audit section below — this is a hard gate before any auto-apply branch merges)
- Both partners agree on the `auto_apply.py` module placement (this document)
- Both partners agree on the webhook dispatch mechanism (this document)

#### Features

**Feature 1: Background auto-apply in the scheduler**

After each drift check, cross-reference drift results against `known_issues` where
`auto_apply_enabled = true` and `AUTO_REMEDIATION_ENABLED = true`. For each match, call
`check_blocked()` then `get_applier(platform)(..., dry_run=False)`. Record a
`RemediationEvent`. Schedule a one-shot re-poll within 60 seconds.

**Module:** `netdrift/auto_apply.py` — new module, jointly owned.

Function signature:
```python
def run_auto_apply(
    drifts: list[dict],
    device: dict,
    session_factory,
    *,
    applier_fn=get_applier,   # injectable for tests
    now_fn=datetime.utcnow,   # injectable for tests
) -> list[RemediationEvent]:
```

Called from `pipeline.run_drift_check()` after `save_drifts()`, conditional on
`AUTO_REMEDIATION_ENABLED`.

Required safety rails (all mandatory before shipping):
- `check_blocked()` fires before every apply — raises `RemediationBlockedError` for
  `session_state`, `adjacency_state`, and management interfaces
- `kind` gate re-enforced at execution time: only `restore_intent` is eligible for
  auto-apply (do not trust the DB invariant alone)
- Consecutive-failure auto-disable: 3 consecutive `outcome=failure` rows for the same
  `KnownIssue` → set `auto_apply_enabled=False` and log a WARNING
- `RemediationEvent` written on both success and failure — never swallowed without recording
- Per-device runtime kill-switch (Feature 3 below) checked before dispatching

**Feature 2: Webhook / notification output**

Fire outbound HTTP POST on: new `critical`-severity drift persisted, auto-apply
`outcome=success`, auto-apply `outcome=failure`.

**Module:** `netdrift/webhook.py` — `WebhookDispatcher` class.

Design:
- Daemon thread draining a bounded `queue.Queue(maxsize=100)`. If queue is full, log a
  WARNING and drop (never block the scheduler).
- `fire(event_type, payload)`: enqueues the event.
- Dispatcher calls `httpx.post()` synchronously with `timeout=5` and logs the result.
  Redact `WEBHOOK_URL` to host+path only in logs (tokens are often in the URL).
- URL validation at instantiation: reject `localhost`, RFC 1918, link-local (`169.254.x`),
  and non-HTTP(S) schemes. A `WEBHOOK_ALLOW_PRIVATE=true` env var overrides RFC 1918
  for on-prem Slack/PagerDuty.
- Initialized once in `scheduler.py`'s `main()` alongside `SyslogReceiver`.
- A thin `BackgroundTasks` wrapper in `api/app.py` calls the same `fire()` method
  for API-triggered apply results.

Config: `WEBHOOK_URL` (required for any dispatch), `WEBHOOK_EVENTS` (comma-separated,
default: `critical_drift,apply_success,apply_failure`).

**Feature 3: Per-device auto-apply kill-switch (new — not in original draft)**

When auto-apply is actively harming one device, operators need to stop it without
restarting the scheduler.

New table: `device_settings` — `device_name` (PK), `auto_remediation_paused` (bool,
default False), `paused_at` (nullable datetime), `paused_reason` (nullable text).

New endpoint: `PATCH /devices/{name}/auto-apply {"paused": bool, "reason": str|null}`.

`run_auto_apply()` checks `device_settings.auto_remediation_paused` before dispatching
any apply for a device. API-only for v3.0; dashboard toggle added in v3.5.

#### Management Interface Blocklist Audit (Liam — gate item)

The council's domain-expert identified gaps in all three vendors:

**Arista EOS** (current: `{Management0, Management1}`)
- Missing: `Management0/0`, `Management0/1`, `Management1/0`, `Management1/1` (chassis platforms)
- Missing: sub-interface variants (`Management0.0`, etc.)
- Recommended fix: replace exact set with `iface.startswith("Management")` prefix guard

**Cisco IOS-XE** (current: `{GigabitEthernet0, GigabitEthernet0/0, Management0, Management1}`)
- Missing: `GigabitEthernet0/0/0` (ISR 4000 management port)
- Risk: `GigabitEthernet0` is a *data* interface on CSR 1000v and C8000v — current
  blocklist would incorrectly block data-plane remediation on cloud routers
- Recommended fix: add `GigabitEthernet0/0/0` to set; add per-device `mgmt_interface`
  override field in `devices.yml` for CSR/C8000v disambiguation

**Nokia SR Linux** (current: `{mgmt0}`)
- Missing: `mgmt0.0` subinterface
- Recommended fix: add `mgmt0.0` to set; add `startswith("mgmt")` prefix guard

**Also required before auto-apply ships:**
Add `optional_args["timeout"] = 30` to `_napalm_conn()` in both `appliers/arista.py` and
`appliers/cisco.py`. Without this, NAPALM hangs indefinitely on a dead device and ties up
APScheduler worker threads. This is a 1-line change per file.

#### v3.0 Ownership Table

| Work stream | Owner | Notes |
|---|---|---|
| `netdrift/auto_apply.py` — `run_auto_apply()` | Joint (Liam primary, Matthew review) | Calls `get_applier()` (Liam side) + writes `RemediationEvent` (Matthew schema) |
| Wire `run_auto_apply` into `pipeline.run_drift_check` | Matthew | One conditional call after `save_drifts()`; gated on `AUTO_REMEDIATION_ENABLED` |
| `device_settings` table + Alembic migration | Matthew | |
| `PATCH /devices/{name}/auto-apply` endpoint | Matthew | Per-device kill-switch; no UI in v3.0 |
| `netdrift/webhook.py` — `WebhookDispatcher` | Matthew | Daemon thread + bounded queue + URL validation |
| Wire `WebhookDispatcher` into scheduler `main()` | Matthew | Initialize alongside `SyslogReceiver`; fire on auto-apply results |
| Thin FastAPI `BackgroundTasks` wrapper for API-path applies | Matthew | Calls `dispatcher.fire()` |
| `tests/test_auto_apply.py` — 8+ test cases | Joint | Fake applier fixture in `conftest.py` (Matthew writes, Liam reviews against applier protocol) |
| `tests/test_webhook.py` — 8+ test cases | Matthew | Add `pytest-httpserver` to dev extras |
| Consecutive-failure auto-disable in `run_auto_apply` | Joint | Requires a `KnownIssue` table write to disable; Matthew schema, Liam logic |
| Mgmt interface blocklist audit and fix (all 3 vendors) | Liam | See blocklist section above; pre-ship gate |
| NAPALM `timeout=30` in Arista + Cisco `_napalm_conn()` | Liam | Pre-ship gate |
| `database.py` engine singleton fix | Matthew | `get_engine()` + `get_sessionmaker()` must be module-level singletons — do as a pre-v3.0 chore PR; opens multiple connection pools per poll cycle without this fix |
| `docker-compose.yml` + `.env.example` updates | Matthew | Add `AUTO_REMEDIATION_ENABLED`, `WEBHOOK_URL`, `WEBHOOK_EVENTS`; add `restart: unless-stopped` to `api` and `scheduler` services |
| APScheduler event listener logging | Matthew | Replace all `print()` in `scheduler.py` with `logging.getLogger("netdrift.scheduler")`; wire `EVENT_JOB_EXECUTED` + `EVENT_JOB_ERROR` listeners |
| README + CHANGELOG.md v3.0 + `docs/PROJECT_PLAN.md §15` | Matthew (release shepherd) | |

#### Paired Seams (joint sign-off before code is written)

1. **`auto_apply.py` function signature and injectable seams** — agree on parameters before
   implementation. Specifically: does it accept `session_factory` or an open `session`?
   Does `applier_fn=get_applier` default work for the test injection pattern?

2. **`device_settings.device_name` key** — Matthew owns the migration; Liam must confirm
   the key maps to the `name` field in `devices.yml` (not the NetBox slug).

#### v3.0 Definition of Done

- [ ] Scheduler calls `run_auto_apply()` for each device when `AUTO_REMEDIATION_ENABLED=true`. For each matching `KnownIssue` with `auto_apply_enabled=True`, `check_blocked()` is called; a `RemediationEvent` with `outcome` (`success`/`failure`/`blocked`) is written regardless of result. Verified by test with injected fake applier asserting call count, args, and written `RemediationEvent` rows.
- [ ] `AUTO_REMEDIATION_ENABLED=false` (or absent) prevents all auto-apply calls regardless of per-issue `auto_apply_enabled` flags. Verified by unit test asserting zero applier calls.
- [ ] Management interfaces are never auto-applied regardless of any flag. `RemediationBlockedError` is raised and logged; no `RemediationEvent` is written for a blocked dispatch. Verified explicitly in `test_auto_apply.py`.
- [ ] `kind == "restore_intent"` gate re-enforced at execution time; a `raw_snippet`-kind known issue is not auto-applied even if `auto_apply_enabled=True`. Verified in test.
- [ ] 3 consecutive `outcome=failure` rows for the same `KnownIssue` cause `auto_apply_enabled` to be set `False` and a WARNING to be logged. Verified in test.
- [ ] A successful auto-apply schedules a one-shot re-poll of the affected device within ≤60 seconds. Verified by asserting the re-poll job is enqueued (not by waiting for it to fire).
- [ ] `WebhookDispatcher` HTTP POST is dispatched for: new `critical`-severity drift persisted, auto-apply `outcome=success`, auto-apply `outcome=failure`. Payload contains `event_type`, `device`, `timestamp`, `detail`. `WEBHOOK_URL` unset → no dispatch, no error. Verified using `pytest-httpserver`.
- [ ] `PATCH /devices/{name}/auto-apply {"paused": true}` causes subsequent auto-apply calls for that device to be skipped and logged. Verified in test.
- [ ] All `print()` in `scheduler.py` replaced with structured logging. APScheduler `EVENT_JOB_EXECUTED` and `EVENT_JOB_ERROR` listeners emit at INFO and ERROR.
- [ ] Liam has confirmed in a PR comment that all three vendor management interface blocklists are correct and complete.
- [ ] `docker-compose.yml` and `.env.example` updated with all new env vars. `restart: unless-stopped` added to `api` and `scheduler` services. README, CHANGELOG.md, and `docs/PROJECT_PLAN.md §15` updated.

#### v3.0 Implementation Order

1. **Matthew (pre-v3.0 chore PR):** Fix `database.py` engine singleton. No features — just correctness. Merge to main before any v3.0 feature work.
2. **Liam (week 1, small PRs):** NAPALM timeout fix + all three vendor blocklist fixes. These land on the v3.0 branch early and are the gate items.
3. **Parallel tracks (weeks 1–3):**
   - Matthew: `device_settings` table + `PATCH /devices/{name}/auto-apply` endpoint
   - Matthew: `netdrift/webhook.py` `WebhookDispatcher` class + `test_webhook.py`
4. **Week 3–4:** Matthew wires `WebhookDispatcher` into `scheduler.py`'s `main()`.
5. **Week 3–4:** Liam + Matthew implement `netdrift/auto_apply.py` + `test_auto_apply.py`.
6. **Week 4–5:** Matthew wires `run_auto_apply` into `pipeline.run_drift_check`.
7. **Week 5–6:** Lab integration testing. Matthew wires API-path webhook dispatch. Docs + tag.

---

### v3.5 — Security + SLA + Acknowledge

**Theme:** Make the tool safe to run in a shared environment and give oncall a reason to trust it.

**Dependency gate:**
- v3.0 complete and tagged
- `WebhookDispatcher` from v3.0 is the SLA alert dispatch mechanism

#### Features

**Feature 4: Per-device drift SLA / alerting rules**

`alert_rules` table: `id`, `device` (nullable = all devices), `severity`,
`window_minutes`, `enabled`, `created_at`.

SLA evaluation runs on each scheduler cycle. Query: find unacknowledged drift events
matching `severity` (and `device` if set) where `detected_at <= now() - window_minutes`.
If found, dispatch via `WebhookDispatcher`.

Critical: the SLA evaluator function **must accept a `now: datetime` parameter**
(default `datetime.now(tz=timezone.utc)`). Tests pass a fixed datetime — no `time.sleep()`
anywhere. This is non-negotiable given the prior CI flake history.

Device unreachability: if a device has had no successful collection in the last 2 poll
cycles, the alert type is `device_unreachable`, not `sla_breached`. Do not fire a false
SLA alert when the collector is broken.

Jitter note: a 10-minute SLA with a 5-minute poll interval can fire up to 15 minutes
after drift first appears (worst case). Document this in the alert rule UI.

Also in v3.5: per-device kill-switch toggle added to the dashboard (the API was added
in v3.0).

**Feature 5: REST API authentication (API keys)**

`api_keys` table: `id`, `key_hash` (SHA-256 of the full random key), `name`,
`key_hint` (first 8 chars), `created_at`, `last_used_at`, `expires_at` (nullable).

Key generation: `secrets.token_urlsafe(32)` with prefix `sk-netdrift-`. Hash stored,
raw key shown once on creation and never again.

Middleware: `X-API-Key` header on all mutating endpoints. `X-API-Key` header **must be
excluded from FastAPI access logs** — never log secrets.

Exemptions: `/health`, `GET /drifts` (read-only, unauthenticated by design — a deliberate
joint decision; document the rationale in a code comment in the middleware).

Bootstrap: `driftcheck create-api-key --name "admin"` CLI command writes directly to
Postgres. First key bootstrapped this way; subsequent keys via `POST /api-keys`
(requires a valid key).

Also add: `GET /drifts?since=<ISO8601>` filter and `Link: <next>` pagination header.
Required before any serious external API consumer, and this is the release where auth
makes external consumers realistic.

**Feature 6: Drift acknowledgement (moved from v3.0)**

An engineer marks drift as acknowledged — "this is intentional, stop alerting" — with an
optional expiry. Max expiry: 90 days (422 on longer values).

**Storage design — joint decision required before implementation:**

Storing `acknowledged` as columns on `drift_events` is wrong: `DriftEvent` rows are
immutable (a new row is created on every poll cycle). An acknowledgement on event ID 123
is silently lost when the next poll creates event ID 456 for the same drift. **The correct
model is a separate `acknowledgements` table keyed by `(device, fingerprint,
acknowledged_until)`**, so the acknowledgement persists across poll cycles. Both partners
must agree on this before Matthew writes the migration.

Endpoint: `POST /drifts/{id}/acknowledge` — body `{acknowledged_until: ISO8601 | null}`.
Requires API key.

Suppression logic: event is suppressed from webhook dispatch, SLA evaluation, and
`run_auto_apply()` when an active acknowledgement record exists for
`(device, fingerprint)` where `acknowledged_until IS NULL OR acknowledged_until > now()`.

UI: acknowledged events dimmed in drift table; acknowledge toggle in the row action menu.

Also required: drift retention migration (delete events older than 90 days or a
configurable TTL). The table grows at ~21M rows/year at 10 devices × 5-minute polls.
Ship this migration with v3.5 or drift history queries slow down materially.

#### v3.5 Ownership Table

| Work stream | Owner | Notes |
|---|---|---|
| `alert_rules` table + migration | Matthew | `device` nullable; `severity`; `window_minutes`; `enabled` |
| `evaluate_sla(session, now=datetime.utcnow)` in scheduler | Matthew | Injectable clock; no `time.sleep` anywhere near this function |
| `POST/GET/DELETE /alert-rules` endpoints | Matthew | Requires API key |
| Alert rules panel in dashboard | Matthew | Simple list + form |
| Per-device auto-apply toggle in dashboard | Matthew | UI for the v3.0 API |
| `api_keys` table + migration | Matthew | |
| FastAPI auth middleware | Matthew | `X-API-Key`; exclude from access logs; exempt `/health`, `GET /drifts` |
| `driftcheck create-api-key` CLI command | Liam | `cli.py` extension; writes directly to DB via `DATABASE_URL` |
| `POST /api-keys`, `GET /api-keys`, `DELETE /api-keys/{id}` endpoints | Matthew | |
| `acknowledgements` table + migration | Matthew | Joint design decision on schema shape required first |
| `POST /drifts/{id}/acknowledge` endpoint | Matthew | Requires API key |
| Acknowledgement check in `run_auto_apply()` + webhook dispatch | Matthew + Liam | `run_auto_apply` (Liam side) must check the acknowledgements table |
| Acknowledge toggle in dashboard | Matthew | Dim acknowledged rows |
| `GET /drifts?since=<ISO8601>` + pagination | Matthew | |
| Composite index `drift_events(device, severity, detected_at)` | Matthew | Required for SLA query performance |
| Drift retention migration (TTL / delete events older than N days) | Matthew | Default: 90 days; configurable via env var |
| Lab validation of SLA timing against real poll cadence | Liam | Sign off in PR description that SLA fires correctly in the lab |
| README + CHANGELOG.md v3.5 + `docs/PROJECT_PLAN.md §15` | Matthew (release shepherd) | |

#### Paired Seams (joint sign-off)

1. **`acknowledgements` table schema** — agree before Matthew writes the migration.
   Key question: is the lookup key `(device, fingerprint)` or `(device, object_type, field)`?

2. **`GET /drifts` auth exemption** — both partners explicitly agree drift data is
   public-by-default for self-hosted deployments. Document in a code comment in the
   middleware as a deliberate decision, not an oversight.

#### v3.5 Definition of Done

- [ ] An `alert_rule` with `device=core-sw-01`, `severity=critical`, `window_minutes=10` dispatches a webhook when a critical drift event on that device has `detected_at` older than 10 minutes and is not acknowledged. Test uses injected `now` callable (no real sleep). Verified in `tests/test_sla.py`.
- [ ] SLA alert for a device with no successful collection in the last 2 cycles is tagged `device_unreachable`, not `sla_breached`. Verified in test.
- [ ] All mutating endpoints return `401` when `X-API-Key` is absent or invalid. Verified by a parametrized test hitting each of: `POST /known-issues`, `POST /known-issues/{id}/remediate/apply`, `POST /known-issues/{id}/remediate/dry-run`, `POST /drifts/{id}/acknowledge`, `POST /alert-rules`, `DELETE /alert-rules/{id}`, `POST /api-keys`, `DELETE /api-keys/{id}`, `PATCH /devices/{name}/auto-apply`.
- [ ] `driftcheck create-api-key --name "admin"` creates the first key and prints it to stdout exactly once. Key is stored as SHA-256 hash; plaintext is never persisted. Verified in test.
- [ ] A revoked key returns `401` on the next request. Verified end-to-end.
- [ ] `GET /api-keys` response never includes the raw key value. Verified in test.
- [ ] `POST /drifts/{id}/acknowledge` with `acknowledged_until` in the past returns `422`. With a valid future datetime, the event is suppressed from the next SLA evaluation and auto-apply cycle. Verified with manufactured timestamps (no sleep).
- [ ] Acknowledged drift with an expired `acknowledged_until` is NOT suppressed on the following cycle. Verified with manufactured past timestamp.
- [ ] Composite index `(device, severity, detected_at)` exists on `drift_events`.
- [ ] Drift retention migration is present and ran successfully.
- [ ] `GET /drifts?since=<ISO8601>` filter works. Verified in `test_api.py`.
- [ ] Liam has confirmed in a PR description that SLA fires correctly relative to the lab's real 5-minute poll cadence.
- [ ] README, CHANGELOG.md v3.5, `docs/PROJECT_PLAN.md §15` updated.

---

## Tier 1 — Parallel Liam Track

### v3.75 — Juniper JunOS Support

**Theme:** Fourth vendor — EX/QFX switching and MX/SRX routing via NAPALM `junos` driver.

**Dependency gate:**
- No dependency on v3.0 or v3.5
- Liam confirms a reachable JunOS device exists in the lab (vQFX, vJunos-switch, or physical EX)
- Both partners agree on the `juniper_junos` platform slug addition to `docs/schema.md` Section 4 (one-line joint sign-off)

**Start:** Liam can open this branch as soon as the lab device is confirmed, regardless of where Matthew is in v3.0–v3.5.

**Target merge:** before v4.0 opens (so Juniper patterns can be included in the initial bundle).

#### Scope

**Collector (`collectors/junos.py`):**
- Interfaces + IPs: NAPALM `get_interfaces()` + `get_interfaces_ip()`
- BGP: NAPALM `get_bgp_neighbors()`
- OSPF: direct NETCONF RPC or CLI (confirm best path in lab)
- VLANs: NAPALM `get_vlans()` supplemented with `get-vlan-information` NETCONF RPC.
  Important: JunOS has two VLAN models (pre-ELS and ELS); the collector must handle both.
- Interface name handling: JunOS canonical names use slashes (`ge-0/0/0`, `xe-0/0/0`,
  `et-0/0/0`) — these are valid schema names, do not strip. Use `lo0.0` (not `lo0`) for
  the loopback that carries the IP. IRB interfaces (`irb.0`): treat as `mode: "routed"`;
  the VLAN bridge-domain mapping is out of scope for v3.75.
- Management interface blocklist: `fxp0`, `em0`, `fxp0.0`, `em0.0`

**Applier (`appliers/junos.py`):**
- NAPALM merge-candidate flow, same as Arista
- JunOS enhancement: use `commit confirmed 5` (5-minute auto-rollback) before `commit`.
  After `commit_config()` succeeds, issue `commit` to finalize. This is the only
  JunOS-specific behaviour and is a meaningful safety improvement over Arista/Cisco.

**Registration:** `pipeline.COLLECTORS["juniper_junos"]`, `netbox_client.PLATFORM_MAP`,
`appliers/__init__.APPLIER_MODULES += ("junos",)`.

**Schema impact:** Add `"juniper_junos"` to `docs/schema.md` Section 4.
One-line change, joint sign-off required.

#### v3.75 Ownership Table

| Work stream | Owner |
|---|---|
| `collectors/junos.py` | Liam |
| `appliers/junos.py` | Liam |
| `tests/test_junos_collector.py` | Liam |
| `tests/test_junos_applier.py` | Liam |
| Registration in `pipeline.py`, `cli.py`, `appliers/__init__.py`, `netbox_client.py` | Liam |
| `docs/schema.md` Section 4 update | Joint sign-off |
| Lab validation against live JunOS device | Liam |
| README vendor support table update | Liam |

#### v3.75 Definition of Done

- [ ] `collectors/junos.py` returns schema-valid output for interfaces, VLANs, BGP neighbors, and OSPF adjacencies. Verified by fixture-driven tests using `FakeNapalmJunosConn`.
- [ ] `appliers/junos.py` implements `restore_intent` and `raw_snippet` with NAPALM merge-candidate + `commit confirmed 5` flow. Verified in `test_junos_applier.py`.
- [ ] Management interfaces `fxp0`, `em0`, `fxp0.0`, `em0.0` raise `RemediationBlockedError`. Verified in test.
- [ ] `driftcheck <junos-device>` runs against the lab JunOS device and produces schema-valid output. Liam confirms in PR description with sample output.
- [ ] All existing tests still pass (no regressions). `ruff check .` passes.
- [ ] `docs/schema.md` Section 4 updated with `juniper_junos`; Matthew has signed off on the PR.

---

## Tier 2 — Grows the project's reach

### v4.0 — Community Pattern Library

**Theme:** Make the knowledge base valuable on day one for a new user, without needing
weeks of real drift data.

**Dependency gate:**
- v3.5 complete and tagged (API auth is a prerequisite for safe pattern import)
- v3.75 complete and merged (so Juniper patterns can be included in the initial bundle)
- Both partners have agreed on the `patterns/` YAML schema (joint sign-off)
- `CONTRIBUTING.md` pattern submission section drafted before release

#### Features

**`patterns/` directory:** each file `<slug>.yaml`. The loader computes the fingerprint
from `object_type + field + drift_kinds` — contributors do not hand-write fingerprint
strings.

**YAML schema (draft — joint sign-off required on final):**

```yaml
name: "Interface admin-down when intent says up"
object_type: interface
field: enabled
drift_kinds:
  - value_mismatch
vendors: []          # empty list = all vendors; named entries = vendor-specific only
cause: "Interface was manually shut down or put into err-disable state"
fix: "Re-enable the interface (no shutdown / admin-state enable)"
remediation:
  kind: restore_intent
  # For raw_snippet remediations use by_platform:
  # kind: raw_snippet
  # by_platform:
  #   arista_eos:
  #     transport: cli
  #     body: "interface {interface}\n   no shutdown"
```

Schema validation: Pydantic v2 `PatternSchema` model in `netdrift/patterns/schema.py`.
`yaml.safe_load` is required — `yaml.load` is banned. A CI lint rule flags it.

**`driftcheck import-patterns [path]`:**
Reads all `.yaml` files, validates, upserts into `known_issues` (fingerprint as upsert key).
Idempotent. **Always imports with `auto_apply_enabled=False`** — operators must manually
enable auto-apply after the `CONFIRM_THRESHOLD` gate is satisfied. File paths only in
v4.0; no remote URL support.

**`GET /known-issues/export`:** returns all `known_issues` as importable YAML.
Round-trip export → wipe → import → export produces identical output. Requires API key.

**Initial bundled patterns (≥20):**

*Interface (8):* admin-down vs intent-up, description mismatch, missing in reality,
undocumented interface, IP address mismatch, access VLAN mismatch, tagged VLAN list
mismatch, mode mismatch.

*VLAN (3):* VLAN missing in reality, undocumented VLAN, VLAN name mismatch.

*BGP (5):* session down (diagnose-only — remediation kind null), remote_as mismatch,
BGP neighbor missing in reality, neighbor admin-shutdown, neighbor description mismatch.

*OSPF (4):* adjacency down (diagnose-only), adjacency missing in reality, area mismatch,
interface mismatch.

Note: MTU mismatch (a high-value pattern) requires adding `interfaces[].mtu: int | null`
to `docs/schema.md`. This is deferred to a post-v4.0 schema call with joint sign-off.

#### v4.0 Ownership Table

| Work stream | Owner | Notes |
|---|---|---|
| `patterns/` YAML schema design | Joint sign-off | Must agree before implementation |
| `netdrift/patterns/schema.py` — `PatternSchema` Pydantic model | Matthew | |
| `driftcheck import-patterns` CLI command | Matthew | `cli.py` extension |
| `GET /known-issues/export` endpoint | Matthew | Requires API key |
| CI lint rule: flag `yaml.load(` without safe Loader | Matthew | |
| CI job: `validate-patterns` (separate from pytest) | Matthew | `driftcheck import-patterns --dry-run patterns/` |
| Initial patterns — interfaces and VLANs | Matthew | |
| Initial patterns — BGP and OSPF | Matthew | |
| Vendor accuracy review of all patterns | Liam | Sign-off on all vendor-specific fields before merge |
| Arista-specific patterns (MLAG, EOS defaults, etc.) | Liam | |
| `patterns/README.md` — YAML field reference | Matthew | Required before `CONTRIBUTING.md` links to it |
| `CONTRIBUTING.md` — pattern submission section | Matthew (draft) + Liam (vendor section) | Must merge before release tag |
| README + CHANGELOG.md v4.0 + `docs/PROJECT_PLAN.md §15` | Matthew (release shepherd) | |

#### Paired Seams

1. **`patterns/` YAML schema** — joint design call required. Key open questions:
   `vendors: []` semantics (empty = all vs explicit list), whether `auto_apply` field
   in YAML is honoured on import, exact `fingerprint` computation formula.

2. **Fingerprint computation from YAML fields** — the loader computes fingerprint from
   `object_type + field + drift_kinds`. Must match exactly what `differ.py` produces.

#### v4.0 Definition of Done

- [ ] `patterns/` contains ≥20 YAML files, all valid against `PatternSchema`. At least 5 interface, 4 VLAN, 5 BGP, 4 OSPF patterns. Each includes `object_type`, `field`, `drift_kinds`, `cause`, `fix`. Liam has signed off on accuracy of all vendor-specific fields.
- [ ] `driftcheck import-patterns patterns/` is idempotent: running it twice on a fresh Postgres instance produces identical DB state. Verified in test using in-memory SQLite.
- [ ] All patterns imported with `auto_apply_enabled=False` regardless of any YAML content. Verified in test.
- [ ] `GET /known-issues/export` round-trip produces identical output. Verified by export → wipe → import → export → assert identical YAML.
- [ ] CI `validate-patterns` job passes on every PR. Adding a valid `.yaml` to `patterns/` requires no Python change.
- [ ] CI lint rule flags `yaml.load(` without safe Loader. Verified by the lint job itself.
- [ ] `CONTRIBUTING.md` pattern submission section merged and reviewed by both partners.
- [ ] README, CHANGELOG.md v4.0, `docs/PROJECT_PLAN.md §15` updated.

---

### v4.5 — Advanced SLA + Juniper Advanced Features

**Theme:** Deepen the SLA layer and round out JunOS platform coverage.

*(Scope to be agreed jointly before v4.0 ships. Do not open a v4.5 branch until both
v3.5 and v3.75 are tagged and both partners have agreed on scope.)*

**Likely features (not yet committed):**
- Per-interface and per-VLAN SLA windows (Matthew-heavy)
- Escalation tiers: warning-level and critical-level SLA thresholds with separate webhook events
- Alert deduplication: SLA alert fires once per breach window, not every scheduler cycle
- `sla_resolved` webhook event when drift clears after an SLA breach was fired
- JunOS IRB→VLAN mapping if deferred from v3.75 (Liam)
- QFX-specific datacenter features if there is demand (Liam)

---

## Tier 3 — The genuinely novel direction

### v5.0 — Fuzzy / Semantic Fingerprint Matching

**Theme:** The hard, original engineering that separates this tool from every other drift
detector. A known issue recorded for device A matches a structurally identical drift on
device B, even when IP addresses, hostnames, and VLAN IDs differ.

**DO NOT START** until the corpus prerequisite is met. Building this without real drift
data will produce an arbitrary and untestable matching threshold.

**Prerequisite (hard gate):**
Liam confirms ≥500 labeled drift events from ≥2 months of production-scale operation.
Corpus must be curated with ground-truth labels (correct match pairs and incorrect match
pairs). Both partners agree the corpus is sufficient before the branch opens.

#### Algorithm recommendation

**Template-based normalization** (deterministic, no corpus needed to implement):

Before fingerprinting, replace variable parts of the `object` identifier with tokens:
- `interface:Ethernet1` → `interface:{ETHERNET}` (strip port number / slot notation)
- `bgp_neighbor:10.0.0.2` → `bgp_neighbor:{IP}`
- `vlan:10` → `vlan:{VLAN_ID}`
- `ospf_adjacency:2.2.2.2` → `ospf_adjacency:{ROUTER_ID}`

The normalized fingerprint `bgp_neighbor:{IP}|session_state|value_mismatch` matches the
same structural drift on any device with any peer IP.

After normalization, use **Jaccard similarity** on normalized components as a
tie-breaking measure for near-identical patterns. Threshold is tuned against the corpus.

*Rejected alternatives:*
- TF-IDF: treats structured key-value drift records as bags of words — wrong model.
- Embedding models: require a resident LLM dependency; incompatible with the self-hosted
  promise of running on a 2-vCPU VM.

The exact-match fingerprint (`object_type|field|drift_kind`) remains the default.
Fuzzy matching is behind a feature flag `FUZZY_MATCHING_ENABLED` (default `false`).

#### v5.0 Ownership Table

| Work stream | Owner |
|---|---|
| Corpus assembly (label ≥500 drift event pairs) | Liam |
| Fuzzy fingerprint algorithm design | Both (pair before any code is written) |
| `netdrift/fingerprint.py` fuzzy normalization | Both (paired) |
| Matching threshold tuning against corpus | Both (paired) |
| Alembic migration: re-fingerprint `known_issues` + `legacy_fingerprint` column | Matthew |
| UI: confidence score badge on known-fix matches | Matthew |
| `GET /drifts` response: `match_confidence: float | null` | Matthew |
| `docs/v5.0-corpus-eval.md` — evaluation results and sign-off | Both |

#### v5.0 Definition of Done

- [ ] On a labeled corpus of ≥500 drift events, the fuzzy matcher achieves false-positive rate ≤5% and false-negative rate ≤20% at the accepted threshold. Results documented in `docs/v5.0-corpus-eval.md` and signed off by both partners before the release tag.
- [ ] A known issue for `bgp_neighbor:10.0.0.1|session_state|value_mismatch` matches a structurally identical drift with `bgp_neighbor:10.1.1.1`. Verified by a hand-crafted test pair.
- [ ] `FUZZY_MATCHING_ENABLED=false` (the default) leaves existing behaviour entirely unchanged. Verified by running all existing `test_fixtures.py` tests with no modification.
- [ ] `GET /drifts` includes `match_confidence: float | null` (null = no match, 1.0 = exact, 0.0–1.0 = fuzzy). Verified in `test_api.py`.
- [ ] Migration re-fingerprints `known_issues`; `legacy_fingerprint` column preserves the original value; migration is reversible via Alembic downgrade.
- [ ] UI confidence score badge visible alongside known-fix suggestions; scores < 0.7 show a visual warning indicator.

---

## Test Infrastructure Changes (add as each version ships)

### v3.0 — add to `dev` extras in `pyproject.toml`

```toml
[project.optional-dependencies]
dev = [
  "pytest",
  "ruff",
  "httpx",
  "pytest-cov>=5.0",        # coverage enforcement
  "pytest-httpserver>=1.0", # webhook dispatch testing
]
```

Add to CI:
```yaml
- name: Run tests with coverage
  run: pytest --cov=netdrift --cov-report=term-missing --cov-fail-under=80
```

### v3.5 — add to `dev` extras

```toml
"time-machine>=2.0",  # SLA clock injection (if needed beyond parameter injection)
```

### v4.0 — add separate CI job

```yaml
- name: Validate bundled patterns
  run: python -m netdrift.cli validate-patterns patterns/
```

### Testing principles (non-negotiable)

1. **No `time.sleep()` in any test.** Any function with timing behaviour accepts a
   `now: datetime` injectable parameter. SLA evaluator and auto-apply loop are the new
   cases. The syslog cooldown CI flake (fixed in PR #71) is the precedent to not repeat.

2. **Any test that spawns a background thread must `.join(timeout=5.0)` the handle
   and assert `not t.is_alive()`.** No `event.wait()` + deadline races.

3. **Webhook tests use `pytest-httpserver`** — a real local HTTP server, not a
   monkey-patched HTTP client. Transport-agnostic and more trustworthy.

---

## Open Questions (resolve before each version branch opens)

| # | Question | When to resolve | Who |
|---|---|---|---|
| 1 | `acknowledgements` table schema — `(device, fingerprint)` key or `(device, object_type, field)` key? | Before v3.5 branch opens | Joint |
| 2 | `GET /drifts` auth exemption — confirm drift data is public-by-default for self-hosted, document rationale | Before v3.5 branch opens | Joint |
| 3 | `GigabitEthernet0` on CSR 1000v — management or data interface for the Cisco lab targets? | Before v3.0 merges | Liam |
| 4 | JunOS lab target availability — vQFX, vJunos-switch, or physical EX? | Before v3.75 branch opens | Liam |
| 5 | MTU schema proposal — add `interfaces[].mtu: int | null` to `docs/schema.md`? | Before v4.0 ships | Joint |
| 6 | `raw_snippet.body` as literal or template in v4.0? (`{PLACEHOLDER}` substitution has security implications) | Before v4.0 branch opens | Joint |

---

## Emergent Issues Found During Review

These are bugs or gaps in the current codebase (not roadmap features) that should be
addressed as chore PRs:

1. **`database.py` engine singleton** — `get_engine()` is not a singleton; the scheduler
   opens a new connection pool on every call. 10-line fix before v3.0 feature work starts.

2. **`AUTO_REMEDIATION_ENABLED` missing from `docker-compose.yml`** — the env var exists
   in code but is never passed to the `api` or `scheduler` services in compose. Auto-apply
   can never be enabled in the compose deployment today. Fix in the v3.0 docker-compose PR.

3. **No `restart: unless-stopped` on `api` and `scheduler` services** in
   `docker-compose.yml`. A crash requires manual restart. Add to both services.

4. **`devices.yml` cache in `api/app.py` is never invalidated** — adding a new device
   requires an API restart to be visible. Add `?reload=true` to `/health` or document
   the restart requirement prominently.

5. **Webhook rate limiting** — if 50 drift events fire in one poll cycle, the dispatcher
   enqueues 50 HTTP POSTs. Slack will 429 after ~1/second. Design `WebhookDispatcher` in
   v3.0 to accept a `rate_limit_per_minute` parameter (even if the default is generous),
   so rate limiting can be tightened in v3.5 without an API change.
