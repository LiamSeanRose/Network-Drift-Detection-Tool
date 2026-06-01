# Proposed Additions — Post-Roadmap Ideas

> **Status:** Proposal — not yet integrated into `docs/PROJECT_PLAN.md` or `docs/ROADMAP_POST_V2.5.md`.
> Reviewed by design council 2026-06-01. Both Liam and Matthew have agreed these are
> worth building. Sequencing and ownership to be agreed before any branch is created.
>
> **Last updated:** 2026-06-01

---

## Overview

Nine feature ideas and one portfolio site recommendation, sourced from a design council
session. Grouped into three tiers by sequencing dependency. None require a new language
or a new tech stack — all fit the existing Python/React/NAPALM/FastAPI foundation.

Items marked **⚠ schema sign-off required** touch `docs/schema.md` and need a joint
review PR before implementation begins (same process as any other schema change).

---

## Portfolio Site

### What

A static landing page (`index.html`, plain HTML/CSS) separate from the MkDocs docs site
Matthew already has planned. The two serve different jobs:

- **Landing page** — for someone who found the GitHub link and needs to understand what
  the tool does in 30 seconds. Not a docs page.
- **Docs site** — for someone who installed the tool and needs to configure it.

### Structure

Landing page content, in order:

1. One-line pitch: *"The open-source alternative to NetBox Assurance, with an
   institutional memory."*
2. A 10–15 second looping WebM/GIF of the dashboard showing a drift event detected,
   the known fix surfaced, dry-run run, apply confirmed.
3. Architecture diagram SVG (NetBox → intent + Device → reality → differ → Postgres).
4. Vendor/OS tested table (exact versions — more credible than logos).
5. Schema contract snippet (15 lines of JSON showing the normalized shape).
6. `docker compose up` quickstart (3 commands).
7. Two CTAs: `Get started →` (links to docs) and `GitHub`.

### Hosting

GitHub Pages or Cloudflare Pages — both free, zero ops burden, better uptime than
the lab VM. `mkdocs gh-deploy` handles the docs site. The landing page is a separate
`index.html` in the same repo.

### Multi-project org

Create a GitHub org (e.g. `netops-tools` or similar). All future tools live there.
The org README becomes the portfolio index — a short table: Tool | What it does | Status.
Each tool gets its own landing page. Future employers see a pattern of shipping, not
just one project.

**Do NOT:** self-host on the lab VM; build a live hosted demo (screen capture is better);
describe it as a "NetBox plugin" (it is a standalone service); build a multi-tool React SPA.

---

## Tier 1 — Build these (no sequencing dependency)

### 1. `netfind` — IP/MAC trace CLI

**What it solves:** "Which port is this IP on?" is the most common question in a network
ops team — asked 10+ times a day. Today the answer requires manual ARP table lookup,
MAC table cross-reference, LLDP neighbor trace, and NetBox cross-reference. No good
open-source tool does this in one command.

**What it is:** A standalone CLI tool in a separate repo. Given an IP or MAC address:

```bash
netfind 10.1.1.50
# → 10.1.1.50 is at MAC aa:bb:cc:dd:ee:ff
#   Learned on Ethernet3 of access-sw-04 (172.20.20.14)
#   Connected to core-sw-01 Ethernet12 via LLDP
```

Uses NAPALM `get_arp_table()`, `get_mac_address_table()`, `get_lldp_neighbors()` —
all four current vendors already support these. Reads from the same `devices.yml`
format. Dependencies: `napalm`, `pyyaml`, `typer`. No database, no API, no Docker.

**Ownership:** Liam (network-side data collection; Matthew can add a JSON output flag
if he wants a machine-readable mode).

**Effort:** 2–3 weeks.

**Why it matters for the portfolio:** Gets GitHub stars independently of netdrift.
Any network engineer can understand the use case in one sentence and install it in
two minutes.

---

### 2. Pre-Change Snapshot / Compare

**What it solves:** Before a maintenance window, engineers want a baseline. After the
change, they want to know: did anything break that wasn't supposed to? Today netdrift
has no way to take an ad-hoc snapshot outside the poll schedule or compare two
specific points in time.

**What it is:** Two new `driftcheck` subcommands:

```bash
# Tag a snapshot before the maintenance window
driftcheck snapshot core-sw-01 --tag pre-maint-2026-06-01

# After the window, compare
driftcheck compare pre-maint-2026-06-01 post-maint-2026-06-01
```

The snapshot is a normal collector run stored with a user-supplied tag. The compare
uses the existing `differ.diff()` against two stored snapshots. One new CLI subcommand,
a `snapshot_tag` column on a new `DeviceSnapshot` table (or as a column on the
existing drift event groups), and a `GET /snapshots` endpoint for the dashboard.

**Schema impact:** New `DeviceSnapshot` table — does not touch the normalized schema
contract in `docs/schema.md`. Matthew owns the migration; Liam owns the snapshot
collection call.

**Ownership:** Joint — Liam (CLI + collector trigger), Matthew (storage + API + dashboard view).

**Effort:** 3–4 weeks.

**Why it matters:** Maintenance windows are where tools earn trust. "Here is what
changed vs. the pre-maintenance baseline, and here is one thing that drifted outside
the change ticket" is understood by everyone on the bridge call.

---

### 3. Firmware Version Drift ⚠ schema sign-off required

**What it solves:** Nobody has a complete, up-to-date list of what software version
is running on each device. NetBox has a `platform` field but it rarely tracks the
running OS version reliably. Finding out which devices are behind on a software
update requires logging into each one.

**What it is:** NAPALM's `get_facts()` returns `os_version` for all four current
vendors. Add `software_version` as a collected field. NetBox's `platform` or
`config_context` stores the desired version. The existing `differ.diff()` produces a
`software_version|value_mismatch` drift event when they diverge. Shows up in the
existing dashboard with zero new UI work.

**Schema impact:** Add `software_version: str` to the top-level schema dict in
`docs/schema.md`. **Requires joint sign-off PR before implementation.**

**Ownership:** Liam (schema proposal + `get_facts()` call in all 4 collectors),
Matthew (differ + schema.md co-sign).

**Effort:** 1–2 days of coding once schema is agreed.

**Why it matters:** Highest value-to-effort ratio of anything in this document.
Visible from day one on any existing netdrift install.

---

## Tier 2 — Add to roadmap (build after v4.0)

### 4. Compliance / Hardening Checker

**What it solves:** No self-hosted, multi-vendor compliance checker exists in open
source that does this continuously and stores history. Batfish requires a heavy JVM
stack and is pre-deployment only. Commercial tools (CNA, NetMRI) are expensive and
closed-source.

**What it is:** A `netcompliance` module (or `netdrift` extension) that defines
compliance rules as YAML:

```yaml
name: SSH version 2 required
check: ssh_version == 2
severity: critical
reference: CIS_EOS_1.1_Section_2.3
vendors: [arista_eos, cisco_iosxe]
```

The rule format mirrors the v4.0 community pattern library schema — shared design,
shared YAML validator. The differ produces `drift_kind: compliance_violation`. History,
alerting, and auto-remediation all reuse existing infrastructure.

**Dependency:** Build after v4.0 community patterns — the YAML schema design is shared.
The compliance rule format is a superset of the drift pattern format.

**Ownership:** Both (joint YAML schema design), Liam (vendor-specific rule accuracy),
Matthew (differ + storage + dashboard compliance view).

**Effort:** 6–8 weeks for engine + initial CIS-aligned rule sets for Arista and Cisco.

**Why it matters:** Expands the user base to security and compliance teams — a
different buyer than network ops. Community rule-set model (same as v4.0 patterns)
means others contribute rules over time.

---

### 5. Ansible `netdrift_facts` Module

**What it solves:** 70%+ of network automation teams already use Ansible. They want
netdrift to surface drift as Ansible facts so their existing playbooks can act on it.
Today there is no connection between the two tools.

**What it is:** A standalone Ansible collection in a separate repo (`netdrift-ansible`),
published to Ansible Galaxy:

```yaml
- name: Get current drift for this device
  netdrift_facts:
    api_url: "{{ netdrift_api }}"
    device: "{{ inventory_hostname }}"
  register: drift_data

- name: Apply known fix if one exists
  arista.eos.eos_config:
    lines: "{{ drift_data.facts.known_fix.rendered_commands }}"
  when: drift_data.facts | selectattr('known_fix', 'defined') | list | length > 0
```

The module is a thin HTTP wrapper: calls `GET /drifts?device=...`, registers the
response as Ansible facts. No new netdrift backend code required — the API already
exists. Add `POST /devices/{name}/repoll` as a companion endpoint so Ansible can
trigger a fresh check post-change.

**Ownership:** Matthew (new repoll API endpoint), Liam (Ansible collection, since he
understands the vendor-side data shape).

**Effort:** 1–2 weeks.

**Why it matters:** Single integration most likely to increase install rate for
automation-mature teams. An Ansible collection on Galaxy is also a credible portfolio
artifact in its own right.

---

### 6. Change Window Suppression

**What it solves:** Alert fatigue. When drift fires during a scheduled maintenance
window, engineers already know about it — the alert is noise, not signal. Ignoring
repeated false positives trains teams to ignore all alerts. This is the #1 reason
monitoring tools lose adoption.

**What it is:** A lightweight `change_windows` table in netdrift:

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `device` | str (nullable) | null = applies to all devices |
| `start_time` | datetime UTC | |
| `end_time` | datetime UTC | |
| `description` | str | change ticket reference, free text |
| `created_by` | str | |

When a drift webhook would fire, the dispatcher checks if an open change window covers
the device at the current time. If yes: suppress the notification and tag the drift
event `expected_change`. The drift is still recorded — just not alerted.

CRUD endpoints: `POST/GET/DELETE /change-windows`. A simple UI panel (list + form).
No ITSM sync — document webhook→Jira as a worked example in the docs instead.

**Ownership:** Matthew (table + API + UI panel), Liam (validate timing against lab).

**Effort:** 2–3 days.

**Why it matters:** Prevents the false-positive fatigue that kills adoption of every
monitoring tool. Short to build, high operational impact.

---

## Tier 3 — Longer-term (sequencing dependencies)

### 7. Network Topology Map ⚠ schema sign-off required

**What it solves:** The drift table tells you *what* is drifting. A topology map tells
you *where* it is in the network and whether a drifting device is on a critical path.

**What it is:** A new dashboard tab showing a D3.js or `reactflow` force graph of the
network with devices as nodes. Drifting devices glow red/amber; clean devices are grey.
Clicking a node opens the drift table filtered to that device.

**Data source:** NAPALM `get_lldp_neighbors()` is available on all four current vendors.
Add an `lldp_neighbors` field to the reality schema — the differ can detect LLDP
topology drift (missing or unexpected neighbors) as a bonus.

**Dependency:** Requires `lldp_neighbors` added to `docs/schema.md` — **joint sign-off
PR required.** Then a new `GET /topology` API endpoint. Then the React component.
These are three sequential steps; the component comes last.

**Ownership:** Liam (`lldp_neighbors` in collectors), Matthew (API + React component).

**Effort:** 4–6 weeks end-to-end (schema → collector → API → React).

---

### 8. LLDP Topology Crawler (`netmap`)

**What it solves:** "Is what's in NetBox actually how devices are connected?" LLDP
neighbor tables are the ground truth for physical topology, but most teams never
export them systematically. Devices get recabled without NetBox updates.

**What it is:** A standalone CLI / scheduled tool that walks LLDP neighbors from a
seed device, builds a full topology graph, and either writes it to NetBox
(`dcim.cables` API) or diffs it against the existing NetBox topology. Surfaces:
new neighbors (unauthorized device?), missing neighbors (link down or recabled?),
system-name mismatches (device renamed in NetBox but not on wire).

**Relationship to #7:** This is the data-collection companion to the topology map.
The crawler populates NetBox; the topology map visualizes it. They can be built
independently — the crawler has standalone value without the map.

**Ownership:** Liam (NAPALM LLDP collection + NetBox write), Matthew (NetBox cable
API integration if needed).

**Effort:** 2–3 weeks for the crawler; another 1–2 weeks for the NetBox write-back.

---

### 9. Git-Based Intent Source (`SOURCE_OF_TRUTH=git`)

**What it solves:** Some teams manage network intent as code (YAML files in a git
repo) rather than in NetBox. A PR to the intent repo represents an intended state
change; CI should validate it against live device state before merging.

**What it is:** A third source-of-truth adapter alongside `netbox` and `nautobot`.
`pipeline.py`'s `_resolve_intent_fn()` already dispatches on `SOURCE_OF_TRUTH` — add
a `git` case that reads a YAML file from a local checkout of the intent repo.

The schema contract is unchanged. The new work is:
- `git_client.get_intent(device_name) -> dict` that reads `intent/<device>.yaml`
- A differ change to handle *partial* intent files (skip missing top-level keys
  rather than treating them as `missing_in_intent`) — **requires joint design
  discussion before implementation**
- Documentation for the GitOps workflow: PR → CI runs `driftcheck` → report shows
  what would change → merge gate

**Dependency:** The partial-intent differ change needs a design discussion between
Liam and Matthew before any code is written. This is the most design-heavy item
in this document.

**Ownership:** Liam (`git_client.py`), Matthew (differ partial-intent handling +
docs), Joint (design discussion on the differ change).

**Effort:** 3–4 weeks once the differ question is resolved.

---

## Dashboard UX Improvements

Three additions to the existing React dashboard identified by the design council.
All three work with data the tool already collects — no new backend seams required
beyond what is noted. These are Matthew's frontend domain; Liam reviews for
operational correctness.

### UI-1. Per-Device Health Summary Cards

**What it solves:** The current dashboard opens on a flat list of drift events across
all devices. There is no answer to "which device is in the worst shape right now"
without scanning the full table.

**What it is:** Replace the 24h history panel with a grid of device cards — one per
device — each showing:
- Device name (monospace)
- Current drift count broken down by severity: `2 critical · 3 warning · 1 info`
- A mini sparkline for 24h trend (reuses existing history data)
- A status dot: grey (clean), amber (warning drift), red pulse (critical drift)
- Auto-apply status: `auto-apply: ON / OFF` in muted text

Clicking a card filters the drift table to that device. A clear-filter affordance
returns to all devices.

**Implementation:** Refactor the existing `HistoryPanel` component. Group drift events
by device on the client side, or add a `/drifts/summary` endpoint returning current
counts per device. CSS grid, no new dependencies.

**Ownership:** Matthew (frontend + optional API endpoint).

**Effort:** 1–2 weeks.

---

### UI-2. Remediation Audit Trail — First-Class View

**What it solves:** The remediation audit log is currently buried inside an expanded
row in the drift table. When a manager, post-incident review, or change-management
process asks "what did the tool apply and when?", there is no easy answer. Invisible
audit trails make auto-apply feel unsafe to enable.

**What it is:** A new `/audit` route (or a tab in the header) showing all remediation
events across all devices chronologically:

| When | Device | Drift pattern | Action | Result | Applied by |
|------|--------|--------------|--------|--------|------------|
| 2m ago | core-sw-01 | interface·description·value_mismatch | apply | ✓ success | auto |
| 14m ago | core-sw-02 | vlan·name·value_mismatch | dry-run | ✓ | liam |

Failure rows get a red left-edge bar (mirrors the severity bar on the drift table).
Filter bar: by device, by result, by date range.

**Implementation:** New route in the React app. The `RemediationEvent` rows are
already written to Postgres on every apply — this is purely a new view over existing
data. Add a `GET /remediation-events` endpoint or reuse `GET /known-issues/{id}/remediation-events`.

**Ownership:** Matthew (frontend + API endpoint if needed).

**Effort:** 1–2 weeks.

---

### UI-3. Drift Timeline — "What Changed When"

**What it solves:** The 24h sparklines show drift counts but not the events behind
them. "This device went from 0 to 7 drift events at 14:32 — what happened?" is
currently unanswerable without scanning the full events table with a timestamp filter.

**What it is:** An SVG timeline panel (sits between the device summary cards and the
drift table) showing drift events as dots on a per-device horizontal timeline. The
x-axis is time (last 24h), one row per device. Each dot is coloured by severity.
Hovering a dot shows a tooltip: timestamp, field, severity, known fix attached?
Remediation events that resolved drift appear as a checkmark on the same timeline.

**Implementation:** SVG component in React. Requires the `/drifts/history` response
to include event-level data (currently returns aggregated 5-minute buckets only) — a
small backend change to add individual events alongside the bucket data, or a new
`/drifts/timeline` endpoint.

**Ownership:** Matthew (frontend SVG component + minor API change).

**Effort:** 2–3 weeks.

---

## Public Hosting — Running the App Beyond Localhost

Right now netdrift only runs on the local lab VM and is accessible at
`localhost:5173` / `localhost:8001`. For a portfolio demo, employer review, or
community testing, it needs to be reachable from outside.

### The challenge

A live netdrift instance needs:
1. A running NetBox (or Nautobot) instance for intent
2. Live network devices for reality collection (the cEOS/SR Linux lab)
3. A Postgres database
4. The FastAPI backend + APScheduler scheduler
5. The React frontend

The lab devices are on `172.20.20.x` — only reachable from within the lab VM. A
public instance can't poll them directly unless there's a VPN or the VM is the host.

### Option A — Demo mode with pre-seeded fixture data (recommended)

Add a `DEMO_MODE=true` environment variable. When set, the API serves from a static
pre-seeded Postgres database instead of polling live devices. The scheduler is
disabled. The dashboard shows real-looking drift history, known issues, remediation
events, and the full UI — but nothing changes unless you manually trigger it.

The pre-seeded data comes from the existing lab fixture JSON files in `tests/fixtures/`
plus a few hand-crafted `RemediationEvent` rows to show the full cycle. A one-time
`driftcheck seed-demo` CLI command populates the demo database.

The demo instance then runs on a cheap VPS (Hetzner CAX11 at ~€4/month, or Oracle
Cloud always-free ARM tier at €0). Only the API and frontend containers need to run —
no scheduler, no lab devices. The Postgres database is small (fixture data only).

**This is the recommended approach.** A hosted demo where employers can click through
the real UI is significantly more compelling than screenshots — and with demo mode it's
zero-maintenance (no live devices, no NetBox to keep running, static data).

**Ownership:** Matthew (demo mode flag in API + scheduler, seed-demo CLI), Liam
(lab fixture data curation for the demo dataset).

**Effort:** 1–2 weeks for demo mode + seeding. VPS setup: 1–2 hours.

---

### Option B — Full live instance on a VPS (with VPN to lab)

Run the full stack on a public VPS with a WireGuard VPN tunnel back to the lab VM
so the scheduler can reach the cEOS/Nokia devices. Shows real live drift from the
real lab.

**Pros:** The demo is real — actual drift from actual devices.
**Cons:** The lab VM must be online 24/7. The VPN must stay up. A lab device
misconfiguration shows up in the public demo. Requires hardening the public API
(the v3.5 API key auth is a prerequisite before this is safe).

**Recommended only after v3.5 ships** (API key auth + per-device SLA alerting make
this safe to run publicly). Not the first step.

---

### Option C — Read-only public API, write operations gated

Run the full stack on a VPS but configure the demo API key as read-only. `GET /drifts`,
`GET /known-issues`, `GET /health`, and `GET /audit` are public. All write/apply
endpoints require a real API key that isn't published. This is the right long-term
posture once v3.5 ships.

---

### Recommended path

1. **Now:** Build Option A (demo mode) and deploy to a free/cheap VPS. Link to it
   from the landing page as "Live demo →".
2. **After v3.5:** Switch to Option C (real data, read-only public API, writes require
   key). This becomes the permanent hosted instance.

The custom domain (`netdrift.dev` or similar, ~$10/year) points to the VPS. The
static landing page and docs site are on GitHub/Cloudflare Pages. The live demo is
on the VPS. Three separate things, all linked.

---

## Summary Table

| # | Item | Type | Effort | Dependency |
|---|------|------|--------|------------|
| 1 | `netfind` IP/MAC trace CLI | New standalone tool | 2–3 weeks | None |
| 2 | Pre-change snapshot/compare | netdrift + CLI | 3–4 weeks | None |
| 3 | Firmware version drift | netdrift extension | 1–2 days | Schema sign-off ⚠ |
| 4 | Compliance/hardening checker | netdrift extension | 6–8 weeks | After v4.0 patterns |
| 5 | Ansible `netdrift_facts` module | Standalone collection | 1–2 weeks | After v3.5 auth |
| 6 | Change window suppression | netdrift extension | 2–3 days | None |
| 7 | Network topology map | netdrift + React | 4–6 weeks | Schema sign-off ⚠ + #8 optional |
| 8 | LLDP topology crawler (`netmap`) | New standalone tool | 2–4 weeks | None |
| 9 | Git-based intent source | netdrift extension | 3–4 weeks | Joint design discussion |
| UI-1 | Per-device health summary cards | React (Matthew) | 1–2 weeks | None |
| UI-2 | Remediation audit trail view | React + API (Matthew) | 1–2 weeks | None |
| UI-3 | Drift timeline with event dots | React + minor API (Matthew) | 2–3 weeks | None |
| — | Portfolio landing page | Static HTML | 1–2 days | None |
| — | Demo mode + VPS hosting | Backend flag + VPS | 1–2 weeks | None (Option A) |

**Schema sign-off required (⚠):** Items 3 and 7 add fields to `docs/schema.md`.
Both require a joint-review PR with sign-off from Liam and Matthew before any
implementation branch is created — same process as all previous schema changes.

**Items with no dependencies (start any time):** 1, 2, 6, 8, all three UI items,
the portfolio landing page, and demo mode hosting.
