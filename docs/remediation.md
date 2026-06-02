# Remediation & Auto-Apply

netdrift can do more than *detect* drift — it can **push configuration back to a
device to restore intent**. Because that writes to live network hardware, the
whole feature is **off by default** and gated behind several independent
switches. This page explains what apply does, the safety rails, and how to turn
it on deliberately.

!!! danger "This path writes to live devices"
    Auto-apply renders and commits configuration to real Arista/Cisco/Nokia/
    Juniper devices via NAPALM/gNMI. The blast radius is your managed network.
    Read this whole page before enabling it on anything you care about.

## The two ways a fix is applied

| Path | Trigger | Who runs it |
|------|---------|-------------|
| **Manual apply** | An operator calls the apply endpoint for one drift | A human, on demand |
| **Auto-apply** | The scheduler matches new drift against an enabled known issue | The background scheduler, unattended |

Both render the same remediation and go through the same safety checks. The only
difference is who pulls the trigger.

## Remediation kinds

A known issue carries an optional `remediation` payload with a `kind`:

- **`restore_intent`** — deterministic: re-apply the intended value the differ
  already knows. This is the **only** kind eligible for auto-apply.
- **`raw_snippet`** — operator-supplied config text. Can be applied **manually**
  but is **never** auto-applied — arbitrary config text needs a human in the loop.
- **`null`** — diagnosis only; there is no executable fix.

## The kill-switches (all must be open)

Auto-apply only runs when **every** one of these allows it. Any single one shut
stops it:

1. **Global env switch.** `AUTO_REMEDIATION_ENABLED=true` must be set in the
   scheduler's environment. Unset or `false` → no auto-apply runs at all
   (the default).
2. **Per-issue flag.** A known issue's `auto_apply_enabled` must be `true`. It
   can only be turned on (via `PATCH /known-issues/{id}/auto-apply`) when the
   remediation kind is `restore_intent` **and** the issue has accumulated at
   least `CONFIRM_THRESHOLD` (default **3**) successful manual applies. Patterns
   imported from the community library always arrive with this **off**.
3. **Per-device pause.** `PATCH /devices/{name}/auto-apply {"paused": true}`
   suspends auto-apply for one device regardless of the above — the operator's
   per-device circuit breaker.

## The hard do-not-apply list

Independent of the switches above, `check_blocked()` refuses certain drift
*before any vendor code runs*:

- **Operational-symptom fields** (`session_state`, `adjacency_state`) — these
  are symptoms, not directly configurable, so there is nothing safe to "restore."
- **`missing_in_intent` with empty intent** — an object on the device that isn't
  documented in NetBox is *not* authorization to delete it.
- **Management interfaces** — each vendor applier additionally refuses to touch
  its management interfaces (e.g. Juniper `fxp0`/`em0`), so a remediation can
  never sever the path netdrift uses to reach the device.

## Auto-disable on repeated failure

If the same known issue fails to apply **3 times in a row** on the scheduler
path, its `auto_apply_enabled` flag is cleared automatically and a warning is
logged. Re-enable it via the API only after investigating why it was failing.
The failure counter is tracked **per known issue** — one issue's failures never
disable another.

## Verifying before you trust it: the dry-run flow

```
POST /known-issues/{id}/remediate/dry-run   { "drift_event_id": <id> }
```

This makes a **live** device call (NAPALM `compare_config` / gNMI read-back) and
returns the candidate diff **without committing**. It records a `dry_run_only`
entry in the audit log. Use it to confirm a fix produces exactly the change you
expect before applying for real:

```
POST /known-issues/{id}/remediate/apply     { "drift_event_id": <id> }
```

After a successful apply the scheduler schedules a one-shot **re-poll** of the
device within ~60 seconds so the dashboard reflects the restored state quickly.

## The audit trail

Every apply attempt — success, failure, blocked, or dry-run — writes an
append-only `RemediationEvent`. Review the history for an issue with:

```
GET /known-issues/{id}/remediation-events
```

Apply outcomes also fire `apply_success` / `apply_failure`
[webhooks](deployment.md) if a `WEBHOOK_URL` is configured.

!!! note "Authentication"
    Every endpoint on this page mutates state and therefore requires a valid
    `X-API-Key` header. Only `GET /drifts*` and `/health` are public. See the
    [Deployment & Hardening](deployment.md) guide for key management.

## Turning it on, safely

A sane rollout order:

1. Leave `AUTO_REMEDIATION_ENABLED` unset and use **manual dry-run + apply**
   only, building confidence per issue.
2. Once an issue has ≥3 confirmed successful manual applies and you trust its
   `restore_intent` fix, enable its per-issue flag.
3. Set `AUTO_REMEDIATION_ENABLED=true` on the scheduler.
4. Keep the per-device pause handy as your fast "stop touching this box" lever.
