# Ansible / AWX integration

netdrift detects drift; Ansible changes configuration. The clean division of
labour is: **netdrift emits an event when reality diverges from intent, and your
existing Ansible automation decides what to do about it.** netdrift never pushes
configuration through Ansible on its own — the remediation stays where your
change control already lives.

This keeps netdrift read-only from the network's point of view, which matters in
locked-down environments: the tool that reaches every device only ever *reads*,
and the system already authorised to change devices (AWX/Tower, or an operator
running a playbook) does the writing, with its own approvals and audit trail.

There are two integration points. The first works today with no new code.

## 1. Trigger a playbook on drift (webhook → AWX job template)

netdrift's webhook dispatcher (`netdrift.webhook`) fires an HTTP POST when
something worth acting on happens. Point it at an AWX/Tower job template's launch
webhook and a drift event becomes a playbook run.

### Configure netdrift

Set these in the scheduler's environment:

```bash
# Where to POST. For an AWX job template webhook this is:
#   https://<awx-host>/api/v2/job_templates/<id>/github/   (or /gitlab/)
export WEBHOOK_URL="https://awx.internal.example/api/v2/job_templates/42/github/"

# Which events to send. Default sends all of them; narrow it if you only want
# to act on confirmed problems.
export WEBHOOK_EVENTS="critical_drift,sla_breached,device_unreachable"

# AWX is on your own network, so the SSRF guard must be told the private
# destination is intended.
export WEBHOOK_ALLOW_PRIVATE=true
```

Available event types: `critical_drift`, `sla_breached`, `sla_resolved`,
`device_unreachable`, `apply_success`, `apply_failure`.

### The payload

Each POST body is JSON:

```json
{
  "event_type": "critical_drift",
  "device": "core-sw-01",
  "timestamp": "2026-06-03T12:00:00Z",
  "detail": "..."
}
```

`device` is the device name as netdrift knows it. Map it to the host your
playbook targets (usually the same name, since both sides read the inventory
from NetBox).

### On the AWX side

1. Create a job template for the remediation playbook (for example, one that
   re-applies the intended interface or VLAN configuration for a single host).
2. Enable its webhook (Settings → Webhook on the job template) and copy the
   webhook URL and key into `WEBHOOK_URL`.
3. Have the playbook read the posted `device` from the webhook payload
   (`tower_webhook_payload`) and limit the run to that host.

Keep the job template **manual-approval** for anything that writes to a device
until you trust the loop. netdrift firing the event is not the same as netdrift
approving the change.

## 2. Dynamic inventory of drifting devices (planned)

A read-only `/inventory` endpoint that returns drifting devices in Ansible
dynamic-inventory format — so a playbook can target *exactly the set that has
drifted* (or breached an SLA) without anyone maintaining a host list. This is on
the roadmap, not yet shipped. It composes well with point 1: the webhook says
"something changed," the dynamic inventory says "here is everything currently
out of compliance."

## What netdrift deliberately does not do

- **Push configuration directly.** netdrift has no Ansible-runner inside it.
  Writes go through your AWX/Tower or an operator, never through netdrift's
  network connection.
- **Store playbook credentials.** AWX holds those. netdrift only POSTs an event.
- **Auto-remediate by default.** The built-in remediation path (separate from
  Ansible) ships off by default and is gated behind per-issue opt-in; see
  `docs/remediation.md`.
