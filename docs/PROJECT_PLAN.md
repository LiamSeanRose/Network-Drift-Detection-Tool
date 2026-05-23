Network Drift Detection Tool — Master Project Plan
What this is: A self-hosted, open-source application that continuously compares the intended state of a network (documented in NetBox) against the actual live state of network devices, surfaces the differences ("drift") in a web UI, and — over time — learns the causes and fixes for recurring drift so it can diagnose and remediate issues automatically.
One-line pitch: The open-source alternative to NetBox Assurance, with an institutional memory.
Status: Planning. Last updated: 2026-05-23.

Table of Contents
The Concept
Why This Project Exists
Architecture Overview
The Normalized Schema — The Core Contract
Technology Stack
What You Both Need To Learn
Roadmap — All Versions
v0.1 Sprint — Detailed Tickets
Repository Layout
How To Divide The Work
Working Practices
Lab Environment Setup
Costs
Risks And How To Handle Them
Definition Of Done — Per Version
Glossary


1. The Concept
NetBox is a database where a network team documents what their network is supposed to be: which devices exist, what interfaces they have, which VLANs and IPs are assigned, which BGP sessions should exist. This is the intended state (or "intent"). NetBox never checks reality — it just stores the blueprint.
Our tool is the thing that checks. On a schedule (every 1–5 minutes) it:
Pulls the intended state for each device from NetBox.
Connects to the real device and pulls its actual operational state.
Computes a structured diff — the drift.
Stores every drift event with timestamps, building a history.
Presents all of it in a web UI.
Layered on top of that core loop, in later versions:
Diagnosis: when drift appears, suggest probable causes.
Knowledge base: let an engineer record "this drift → this cause → this fix." The tool fingerprints the drift so that when an identical pattern recurs — even on a different device or IP — it recognizes it and surfaces the known fix.
Remediation: optionally apply known fixes automatically, with strict safety rails.
The knowledge base is what makes this more than "yet another drift tool." It captures the tribal knowledge that normally lives only in a senior engineer's head.

2. Why This Project Exists
The pain is real and recognized. Configuration/operational drift is a known, named problem. NetBox Labs sells a commercial product (NetBox Assurance) for it, and rConfig sells a NetBox-integrated drift product. The pain is validated.
There is no free, open equivalent. NetBox Assurance is closed-source and enterprise/cloud only. NetBox Discovery's agent is open but its orchestration is commercial. That is the gap we fill: a fully open, self-hostable drift tool.
The knowledge-base layer is genuinely novel. Drift detection exists; generic incident knowledge bases exist; nobody has fused them for networking so that a drift event itself becomes the key into a team's accumulated fixes.
Honest caveat: we are not first to the drift idea. We are first to a free one, and first to the learning layer. The hard, original engineering is the signature matching in the knowledge base — expect to iterate on it several times.

3. Architecture Overview
This is one application — a single codebase — that runs as a few cooperating processes and talks to external systems it does not own.
       OUR APPLICATION  (one repo; runs as a few containers via docker-compose)
        +------------------------------------------------+
        |  - FastAPI web/API server                      |
        |  - Scheduler / worker (does the polling)       |
        |  - Postgres database                           |
        |  - React frontend (static files)               |
        +---------------------+--------------------------+
                              |
              reads intent    |    reads reality
                  +-----------+-----------+
                  |                       |
            +-----v------+        +-------v-------------+
            |   NetBox   |        |  Network devices    |
            | (user's,   |        |  (user's routers /  |
            |  external) |        |   switches)         |
            +------------+        +---------------------+

Three categories — never confuse them:
Our application. What we build. One repo. At runtime: API server + worker + Postgres + frontend, started together with docker compose up. To the user it installs as one thing.
External systems we are a client of. NetBox (the user already runs it; we hit its REST API) and the network devices (the user's gear; we SSH / use gNMI into them). We do not build or bundle these.
Our lab. Containerlab + a local NetBox, used only for our own development and testing. Scaffolding. Never ships to users.
The five logical components of our application:
Component
Responsibility
Source-of-truth client
Wraps the NetBox API. Returns intent in the normalized schema.
Collectors
Per-vendor device connections. Return reality in the normalized schema.
Diff engine
Pure function. Two normalized dicts in, structured drift records out.
Storage + API
Postgres for state/history; FastAPI exposing it.
Web UI
React dashboard: devices, drift, history, acknowledge/remediate.


4. The Normalized Schema — The Core Contract
This is the single most important section. The collector side and the diff side are developed by different people. They meet here. Both get_intent() (NetBox side) and get_reality() (device side) must return data in exactly this shape, so the diff engine can compare them field-by-field without caring where the data came from.
Rule: changing this schema requires both partners to sign off.
4.1 Device-state object (v0.1 scope)
{
    "device": "core-sw-01",          # str: device name, must match NetBox + device
    "platform": "arista_eos",        # str: normalized platform identifier
    "collected_at": "2026-05-20T14:32:00Z",  # ISO 8601 UTC, when this snapshot was taken
    "interfaces": {
        # key = canonical full interface name (NOT abbreviated)
        "Ethernet1": {
            "description": "Uplink to dist-01",  # str, "" if unset
            "enabled": True,                     # bool: admin up/down (NOT link state)
            "ip_addresses": ["10.1.1.5/24"],     # list[str], CIDR notation, sorted
        },
        "Ethernet2": {
            "description": "",
            "enabled": False,
            "ip_addresses": [],
        }
    }
}

4.2 Schema rules (apply to BOTH sides)
Interface names are canonical and full. Ethernet1, not Et1. GigabitEthernet1/0/1, not Gi1/0/1. Each collector is responsible for expanding abbreviations. The diff engine assumes names already match.
Timestamps are ISO 8601, UTC, with Z suffix. No local time, ever.
Lists are sorted before returning (e.g. ip_addresses, later tagged_vlans) so the diff engine never reports drift caused only by ordering.
Absent values are explicit. Empty string for unset descriptions, empty list for no IPs. Never None, never a missing key.
enabled means administrative state (is the interface shutdown?), not link state. Link state is a later field.
4.3 Schema growth by version
Version
New fields added to the schema
v0.1
interfaces: description, enabled, ip_addresses
v0.2
interfaces: mode (access/tagged), untagged_vlan, tagged_vlans; top-level vlans
v0.3
top-level bgp_neighbors (peer, remote_as, state); ospf adjacencies
v1.0
running_config (raw text, for config-level diff)

4.4 Drift record format (output of the diff engine)
{
    "device": "core-sw-01",
    "object": "interface:Ethernet1",   # type:identifier
    "field": "ip_addresses",
    "intent": ["10.1.1.5/24"],         # what NetBox says
    "reality": ["10.1.1.9/24"],        # what the device says
    "drift_kind": "value_mismatch",    # see kinds below
    "severity": "warning",             # info | warning | critical
    "detected_at": "2026-05-20T14:32:00Z",
}

drift_kind values: value_mismatch (both sides have a value, they differ), missing_in_reality (intent has it, device doesn't), missing_in_intent (device has it, NetBox doesn't — undocumented config), extra (generic catch-all).

5. Technology Stack
Layer
Choice
Why
Language
Python 3.11+
Best network-automation library ecosystem
NetBox client
pynetbox
Official NetBox API client
Device access
Scrapli + NAPALM
Scrapli for speed/modern, NAPALM getters as fallback
Concurrency
asyncio (Scrapli has async)
Polling many devices in parallel
Orchestration
Nornir (optional, v0.2+)
Inventory + concurrent task runner
Modern telemetry
pygnmi
gNMI client, for devices that support it (v0.3+)
Database
PostgreSQL 15+
Reliable, good JSON support
Migrations
Alembic
Schema version control
ORM
SQLAlchemy 2.x
Standard, async-capable
Backend/API
FastAPI
Async, auto OpenAPI docs, type-driven
Scheduler
APScheduler (v0.2) → Celery/RQ/Asynq (at scale)
Start simple, upgrade when needed
Frontend
React (or Svelte)
Pick whichever you already know best
Lab
Containerlab
Free, runs real network OS containers
Packaging
Docker + docker-compose
One-command install
Lint/format
ruff
Fast, all-in-one
Tests
pytest
Standard
CI
GitLab CI (or GitHub Actions)
Built into the repo host
License
Apache-2.0 (recommended) or MIT
Permissive; Apache-2.0 adds a patent grant

v0.1 first vendor: Arista EOS (via the free cEOS container). It has a real API (eAPI), is free to download, and is widely used. Skip Cisco IOS-XE for v1 — Cisco does not give away images freely. Free second/third vendors: Nokia SR Linux, FRR.

6. What You Both Need To Learn
You both have the networking fundamentals (BGP, OSPF, VLANs, what configs look like). On top of that:
Both of you:
The NetBox data model — devices, interfaces, IP addresses, prefixes, VLANs, config contexts, custom fields. Budget one week with the API.
Git workflow — branches, merge requests, reviewing each other's code.
Docker and docker-compose basics.
Person A (data-in side):
NAPALM — getter-based, multi-vendor, returns structured data.
Scrapli — faster, lower-level, async-capable SSH/eAPI.
(v0.3+) gNMI and YANG basics — pygnmi, structured streaming telemetry.
Containerlab — topology files, deploying labs.
Vendor quirks — how each platform names interfaces, formats output, etc.
Person B (logic/out side):
FastAPI — routing, dependency injection, async endpoints.
SQLAlchemy 2.x + Alembic — models, sessions, migrations.
PostgreSQL — schema design, indexing, querying JSON.
React (or Svelte) — components, state, fetching from an API.
APScheduler then later a real job queue.
Shared deep-dive when you reach v2: signature/fingerprint design and fuzzy matching. Do not split this — pair on it.
If neither of you has used the device-automation libraries before, budget two weeks at the start of v0.1 just for learning them on the lab.

7. Roadmap — All Versions
Every version is independently shippable and independently impressive. If life interrupts the project at any version boundary, what exists is still a real, demonstrable portfolio piece.
Version
Theme
Est. (part-time, 2 people @ ~10-15 hrs/wk each)
v0.1
Detector PoC
4–6 weeks
v0.2
Persist + UI
6–8 weeks
v0.3
Production-ish
8–10 weeks
v1.0
Detector complete
6–8 weeks
v1.5
Static diagnosis
4–6 weeks
v2.0
Knowledge base
3–4 months
v2.5
Opt-in auto-remediation
6–8 weeks

Total: roughly 12–16 months part-time. Full-time, ~6–8 months.
v0.1 — Detector PoC
One vendor (Arista cEOS). Pull intent from NetBox, pull reality from the device, diff three fields (interface description, enabled state, IP addresses). CLI output. No database, no UI. Containerlab demo topology + seed_netbox.py. Ship to GitLab, post on r/networking to validate interest.
v0.2 — Persist + UI
Add Postgres + the drift_events history table. Add a second free vendor (Nokia SR Linux or FRR). Add VLANs to the comparison. FastAPI backend + basic React dashboard. Scheduled polling every 1–5 minutes — this is the "actively listens" behaviour.
v0.3 — Production-ish
Add routing state to the schema and diff (BGP neighbors, OSPF adjacencies). Diff history/trends in the UI. Webhooks for Slack/Teams. Optional syslog receiver: a device logs an event, the tool immediately polls just that device instead of waiting for the cycle. Nautobot support alongside NetBox.
v1.0 — Detector complete
Config-level drift: compare the device's running config against a NetBox-rendered intended config (not just operational state). Plugin architecture so users can add their own vendors. Documentation site. Helm chart for Kubernetes deployment. This alone is the "open-source NetBox Assurance" portfolio piece.
v1.5 — Static diagnosis
A hand-built rules table mapping (object, field, drift_kind) to a list of likely causes. No learning, no stored fixes yet — just "here is what usually causes this." ~30–50 rules covers most real drift. Cheap to add, immediately useful.
v2.0 — Knowledge base (the headline feature)
Signature generation, the known_issues table, the record-cause-and-fix workflow, and matching on recurrence. The tool starts getting smarter the more it is used. Do not build this first — it cannot be designed well without a month of real drift data flowing through the detector.
v2.5 — Opt-in auto-remediation
Suggest by default; never auto-apply by default. Auto-apply is opt-in per known-issue, and only unlocks after a fix has been confirmed-correct N times. Always generate the exact commands and require explicit confirmation. Dry-run mode. Full audit log. Post-fix verification on the next poll cycle.
North star (far future)
A community-contributed library of known drift patterns + fixes that ships with the tool, so new users get diagnostic value on day one without having recorded anything.

8. v0.1 Sprint — Detailed Tickets
Do these roughly in order. Tickets 4, 5, and 6 can run in parallel once the normalized schema (Section 4) is agreed and written down.
Ticket 1 — Repo skeleton. GitLab repo. Python 3.11+ project with pyproject.toml. Configure ruff and pytest. Add an Apache-2.0 (or MIT) LICENSE. Write a real README.md with the architecture diagram from Section 3. Add CONTRIBUTING.md. Create docs/ with schema.md, architecture.md, roadmap.md (copy the relevant sections of this plan into them — the repo is the canonical source of truth).
Ticket 2 — Lab environment. Write a Containerlab topology.yml with two Arista cEOS nodes and one link between them. Document the cEOS image download step in docs/lab.md. Commit the topology.
Ticket 3 — seed_netbox.py. A script using pynetbox that creates the two lab devices, their interfaces, and their IPs in NetBox, mirroring the Containerlab topology. Goal: a contributor can reproduce the entire dev environment in about 60 seconds.
Ticket 4 — netbox_client.py. Wrap the NetBox API. Public function get_intent(device_name) -> dict returning the normalized schema from Section 4.1.
Ticket 5 — collectors/arista.py. Connect to a cEOS node via eAPI/Scrapli. Public function get_reality(device) -> dict returning the same normalized schema. Must expand abbreviated interface names.
Ticket 6 — differ.py. A pure function: two normalized dicts in, a list of structured drift records (Section 4.4) out. No I/O — this makes it trivial to unit test. Covers the three v0.1 fields.
Ticket 7 — cli.py. Tie it together. driftcheck core-sw-01 fetches intent, fetches reality, diffs, and pretty-prints the drift to the terminal.
Ticket 8 — Tests + CI. Unit tests for differ.py using hand-built dict pairs (no network needed). GitLab CI runs ruff + pytest on every push. Stretch goal: CI spins up the Containerlab topology for a real end-to-end integration test.
v0.1 is done when driftcheck prints real drift from a real (lab) device. Ship it.

9. Repository Layout
network-drift/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── CHANGELOG.md
├── pyproject.toml
├── docker-compose.yml          # full app stack (added in v0.2)
├── .gitlab-ci.yml
├── docs/
│   ├── schema.md               # the normalized schema — canonical
│   ├── architecture.md
│   ├── roadmap.md
│   └── lab.md                  # how to set up Containerlab + NetBox
├── lab/
│   ├── topology.yml            # Containerlab topology
│   └── seed_netbox.py          # populate NetBox to mirror the lab
├── src/
│   └── netdrift/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py           # loads settings: NetBox URL, credentials
│       ├── schema.py           # dataclasses / typed dicts for the normalized schema
│       ├── netbox_client.py    # get_intent()
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── base.py         # abstract collector interface
│       │   └── arista.py       # get_reality() for Arista EOS
│       ├── differ.py           # the diff engine
│       ├── storage/            # added in v0.2: SQLAlchemy models, Alembic
│       ├── api/                # added in v0.2: FastAPI app
│       └── scheduler.py        # added in v0.2: the polling loop
├── frontend/                   # added in v0.2: React app
└── tests/
    ├── test_differ.py
    ├── test_netbox_client.py
    └── fixtures/               # sample intent/reality dicts

One repo holds everything — API, worker, collectors, frontend. It is not "a bunch of VMs"; it is one application that, at runtime, happens to start a few small containers together.

10. How To Divide The Work
Split by layer, along the normalized-schema seam.
Person A — "data in": netbox_client.py, the collectors, the Containerlab lab, seed_netbox.py. Becomes the expert on NAPALM / Scrapli / gNMI and vendor quirks.
Person B — "logic and out": differ.py, the Postgres schema, FastAPI, the scheduler, the React frontend. Becomes the expert on the app architecture.
They meet at the normalized schema. Agree it first, write it in docs/schema.md, treat changes as a joint decision. While the schema holds, neither blocks the other: A builds collectors against the spec, B builds the differ against hand-written sample dicts in tests/fixtures/.
Two rules:
This is primary ownership, not a wall. Read each other's merge requests — even after merge — so neither person becomes the only one who understands a half of the system. Reviewing before merge is not required; staying aware of what merged is.

For v2 (knowledge base + signature matching), drop the split and pair. It is the hard, original part — two brains beats two halves.
One person should act as informal release shepherd: decides when a version meets its Definition of Done and tags the release.

11. Working Practices
Git workflow.
main is always working and shippable.
One branch per ticket: feat/arista-collector, fix/vlan-sort, etc.
Every change goes through a merge request with a meaningful description. Reviewing the other person's merge request before merge is encouraged but not required — given differing time availability, the author may self-merge once CI passes, but MUST post a description on the MR and alert the partner (e.g. by text) so they can review after the fact and comment on the closed MR.
Write meaningful commit messages — the history is part of the project's story.
Issue tracking.
Use GitLab issues. Label every issue with its version (v0.1, v0.2, …).
Assign an owner. Keep a simple board: To Do / In Progress / Review / Done.
Cadence.
One short weekly sync: what's blocked, what's next, any schema changes.
Don't let a branch live longer than ~a week — merge small and often.
Documentation lives in the repo. docs/ is canonical. A Claude Project (see below) can hold copies for convenience, but the repo wins any conflict.
Testing discipline.
differ.py and any pure logic: real unit tests, from day one.
Collectors: integration-tested against the Containerlab lab.
CI must pass before any merge.
On using Claude / AI assistants.
Do create a shared Claude Project. Put this plan, docs/schema.md, and architecture decisions in it so any chat — yours or your partner's — has accurate context and you don't have to re-explain the project every time.
Understand the limit: a Project shares documents, not live memory. Your Claude conversation and your partner's are independent — they do not see each other and cannot auto-deduplicate your work. There is no "two chatbots coordinating" feature.
Deduplication of actual work comes from you two: the schema contract, clear issue ownership, and staying aware of each other's merge requests. Git catches literal conflicts. The Project just keeps both assistants on the same page about the plan.
When asking an assistant for help, paste in the relevant docs/ file so the answer fits the real, current design.

12. Lab Environment Setup
Everything below runs on one laptop. 16 GB RAM is comfortable; 8 GB works if careful. Linux is easiest; on macOS run Containerlab inside a Linux VM; on Windows use WSL2.
1. Host prep. Install Docker, Python 3.11+, git.
2. NetBox locally.
git clone https://github.com/netbox-community/netbox-docker.git
cd netbox-docker
docker compose up -d
# NetBox is now at http://localhost:8000

Create an admin user and an API token. Explore the data model.
3. Containerlab.
bash -c "$(curl -sL https://get.containerlab.dev)"

Download the free Arista cEOS image (needs a free Arista account) and import it. Then sudo containerlab deploy -t lab/topology.yml.
4. Populate NetBox. Run lab/seed_netbox.py to mirror the Containerlab topology into NetBox.
5. Develop. Point the app's config at http://localhost:8000 for NetBox and at the Containerlab node names for devices.

13. Costs
The build phase can cost $0. Everything needed to develop and test is free: GitLab public repo + CI, Containerlab, Arista cEOS, Nokia SR Linux, FRR, NetBox, Python, Postgres, Docker, all the libraries.
Optional spending, only once you want to show the project:
Item
Cost (approx)
Needed?
VPS for a public live demo
$5–6 / month
Optional, good for stars/CV
Domain name
~$12 / year
Optional
Cisco Modeling Labs (IOS-XE)
~$200 / year
Skip. Use free vendors instead

Recommendation: spend nothing until v0.1 works. A reasonable "make it look legit" budget later is about $70/year (cheap VPS + domain). Cisco images are not worth paying for — Arista cEOS is the better first target anyway.

14. Risks And How To Handle Them
Risk
Mitigation
Scope creep on the diff engine — every vendor edge case eats a weekend
Cap v1 at three vendors. Strictly limit which fields you compare per version.
Semantic-equivalence rabbit hole — "permit ip any any" vs "permit ip 0.0.0.0/0"
Accept imperfect diffs early. Build per-vendor normalizers only when a real false positive forces it.
Signature matching too strict or too loose (v2)
Expect to iterate. Don't design it until you have a month of real drift data. Pair on it.
Building the cool v2 feature first
Follow the roadmap order. v2 is worthless with no drift data flowing.
Bus factor — one person owns half the system
Every MR carries a description and a partner alert so both stay aware of all changes; review-after-merge expected. Keep docs/ current.
Credential security — storing device passwords
Never roll your own crypto. Use Fernet with a documented key setup, or Vault later. Never commit secrets.
Long branches / merge hell
Small branches, merge weekly, main always green.
Burnout on a 12–16 month project
Each version ships independently. Celebrate each release. It's a series of wins, not one distant finish line.
NetBox API rate limits at scale
Batch requests; cache intent between polls; respect pagination.
An auto-applied fix causes an outage (v2.5)
Suggest-by-default. Per-issue opt-in. Confirm-N-times. Mandatory confirmation diff. Dry-run. Audit log.


15. Definition Of Done — Per Version
A version is shippable only when all its criteria are met.
v0.1
[x] driftcheck core-sw-01 prints real drift from a live Containerlab device.
[x] Diff covers interface description, enabled state, IP addresses.
[x] differ.py has unit tests; CI runs ruff + pytest and passes.
[x] topology.yml and seed_netbox.py reproduce the environment from scratch.
[x] README has the architecture diagram and a working quickstart.
[x] Repo is public on GitHub.
v0.2
[ ] Drift events persist in Postgres with timestamps; history is queryable.
[ ] A second vendor is supported.
[ ] VLAN fields are in the schema and the diff.
[ ] React dashboard shows devices + their drift; FastAPI serves the data.
[ ] The scheduler polls every 1–5 minutes automatically.
[ ] docker compose up brings up the whole stack.
v0.3
[ ] BGP neighbor + OSPF adjacency drift detected.
[ ] UI shows drift history/trends.
[ ] Slack/Teams webhook alerts work.
[ ] Syslog receiver triggers an immediate targeted poll.
[ ] Nautobot works as an alternative to NetBox.
v1.0
[ ] Running-config vs intended-config drift works.
[ ] A third vendor is supported via the plugin architecture.
[ ] A new vendor can be added without modifying core code.
[ ] Documentation site is published; Helm chart deploys to Kubernetes.
v1.5
[ ] Each drift event shows a list of likely causes.
[ ] At least ~30 diagnosis rules covering common drift.
v2.0
[ ] An engineer can record cause + fix for a drift event.
[ ] A recurring drift pattern is matched to its stored known-issue, even on a different device/IP.
[ ] The UI surfaces the known fix when a match is found.
v2.5
[ ] Fixes are suggest-only by default.
[ ] Auto-apply is per-issue opt-in and gated on N confirmations.
[ ] Every applied fix shows a confirmation diff, is logged, and is verified on the next poll.

16. Glossary
Intent / intended state — what the network should be, documented in NetBox.
Reality / operational state — what a device actually reports right now.
Drift — a difference between intent and reality.
Drift event — one structured record of one such difference, with timestamps.
NetBox — open-source network source-of-truth / documentation database.
Nautobot — a fork of NetBox; similar data model; supported from v0.3.
Collector — code that connects to a device and returns its reality.
Diff engine (differ.py) — pure function comparing intent and reality.
Normalized schema — the agreed dict shape both sides produce; the core contract.
Signature / fingerprint — a normalized hash of a drift event that ignores variable parts (IPs, hostnames) so recurring patterns can be matched (v2).
Known issue — a stored record of a drift signature + its cause + its fix.
Remediation — applying a fix to resolve drift.
Containerlab — tool that builds labs from real network OS containers.
NAPALM / Scrapli / Nornir — Python network-automation libraries.
gNMI / YANG — modern structured device telemetry protocol and its data models.
NetBox Assurance — NetBox Labs' commercial, closed-source drift product; the thing we are building a free, open alternative to.

End of plan. Keep this document in the repo at docs/ and update it as decisions change — it is the shared source of truth for both partners.

