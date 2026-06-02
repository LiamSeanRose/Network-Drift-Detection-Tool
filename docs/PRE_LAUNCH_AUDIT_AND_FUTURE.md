# netdrift — Pre-Launch Audit & Forward Plan

**Date:** 2026-06-01
**Method:** design-council review, 9 role-specialised seats, findings-only (no code changed by this pass).
**Codebase state:** v3.0 merged. Repo is **public**.

> **Public-repo notice.** This document is a *hardening checklist* — what to fix, why it
> matters, how to verify. It deliberately contains no step-by-step attack instructions and no
> live-host details. Security findings are written so a maintainer can act on them without
> handing a reader an exploit.

## How to read this

- **P0 = launch blocker.** Do not expose a public instance with these open.
- **P1 = high.** Fix before the tool is promoted for production use; none individually blocks a
  read-only demo launch.
- Every finding names evidence (`file:line`) and an owner side — **Liam** (data-in:
  `collectors/`, `netbox_client.py`, `lab/`, appliers data side) or **Matthew** (logic-and-out:
  `differ.py`, `storage/`, `api/`, `scheduler.py`, `frontend/`). Items on the schema contract
  are flagged as proposals — `docs/schema.md` needs both partners' sign-off.
- Two items are **deliberately deferred and are NOT bugs** (Nokia 57-port chassis noise;
  Loopback0 not seeded). They are excluded here by design — do not re-raise them.

---

## 0. The one-paragraph verdict

netdrift is in good structural shape for v3.0, and the test suite (378+) is real. But the
codebase crossed a line at v2.5/v3.0 that the *deployment posture has not caught up to*: it now
**pushes configuration to live network devices**, and the API that triggers that push **ships
with no authentication** (auth is roadmapped for v3.5). For a private lab that is fine. For a
public instance it is not — unauthenticated plus network-mutating is a whole-network blast
radius, not a "wait for v3.5" gap. The launch that is safe today is the **read-only demo-mode**
instance already approved by a prior council. Everything live and mutating waits for v3.5 auth
plus the hardening below. Separately, the audit found one genuine **correctness bug in the
config-push path** (Cisco applier reports success after a rollback) that should be fixed
regardless of launch timing.

---

## 1. Pillar I — Audit findings (correctness & optimisation)

### 1.1 P0 — launch blockers

#### B1. Unauthenticated endpoints push config to live devices
- **Evidence:** `api/app.py:507` (`POST /known-issues/{id}/remediate/apply`), `:425` (dry-run),
  `:321` / `:383` (auto-apply enable / per-device pause). The only FastAPI dependency anywhere in
  the file is `get_session` — there is no auth dependency on any route.
- **Why P0:** anyone who can reach the API can render and push config to real
  Arista/Cisco/Nokia/Junos devices via NAPALM/gNMI. The blast radius is the managed network, not
  just drift data. This is the single highest-impact issue in the review.
- **Fix:** gate every state-changing route (everything that is not `GET /health`, `GET /drifts*`,
  `GET /known-issues*`) behind an auth check before public exposure. Minimum viable: a
  constant-time-compared bearer token from env, 401 on absence/mismatch. Full auth is the v3.5
  work. If a token gate cannot land first, **do not expose the mutating routes to the public
  origin at all** (split router / ingress path allow-list).
- **Verify:** curl each mutating route with no/invalid token → 401; read-only routes still 200.
- **Owner:** Matthew (`api/`).

#### B2. Cisco applier reports `applied=True` after a rollback
- **Evidence:** `appliers/cisco.py:144-160` — on a non-empty post-commit diff it logs a warning,
  calls `rollback()`, then returns `ApplyResult(..., applied=True)` unconditionally.
  `tests/test_cisco_applier.py:273` asserts `applied is True` on this path, locking the wrong
  value in as the tested contract.
- **Why P0:** `auto_apply.py` records this as a success — writes `result="success"`, increments
  `confirmed_count`, leaves `auto_apply_enabled` live — on a device that IOS-XE did *not*
  converge and may have left partially modified. The next scheduler cycle re-applies to a device
  in an unknown state. This is a correctness bug on the most dangerous path in the system.
- **Fix:** return `applied=False` (or an explicit `applied_with_warning`) when
  `post_diff.strip()` is truthy; correct the test to assert the safe value.
- **Verify:** unit test with a non-empty post-commit diff asserts the result is not counted as
  success by `auto_apply`.
- **Owner:** Liam (applier) with Matthew (the `auto_apply` success-accounting contract).

#### B3. Committed default credentials, exposed database, root containers
- **Evidence:** `helm/netdrift/values.yaml:53` ships `password: "changeme"`;
  `docker-compose.yml:42` sets `POSTGRES_PASSWORD: devpassword` and `:62/:71/:101` hardcode it in
  three `DATABASE_URL` strings; `docker-compose.yml:47` binds Postgres `5432:5432` on all
  interfaces; `dockerfile:9` has no `USER` directive, so api/scheduler/migrate all run as UID 0
  with the mounted `devices.yml` (device credentials) readable by root.
- **Why P0:** any operator who copies the documented compose/Helm path onto an internet-facing
  host exposes Postgres with a well-known password and runs every process as root. The dev
  passwords are now permanent in git history.
- **Fix:** (a) replace the Helm literal with a `required` template guard so an unset password is a
  hard install error; (b) in compose, move the DB password to `${POSTGRES_PASSWORD}` (add to
  `.env.example`) and bind `127.0.0.1:5432:5432`; (c) add a system user + `USER netdrift` to the
  Dockerfile and `securityContext.runAsNonRoot: true` to the Helm deployments.
- **Verify:** `helm template` with no password set fails; `docker compose config` shows no literal
  password and localhost-only DB binding; `docker run ... id` shows non-root.
- **Owner:** Liam/Matthew joint (deploy surface).

#### B4. No security disclosure policy
- **Evidence:** no `SECURITY.md` at repo root or `.github/`.
- **Why P0 for a public tool:** a tool that pushes device config and ships an unauthenticated API
  has the highest-consequence bug class there is. With no private disclosure channel, a researcher
  who finds an apply-path or SSRF hole either goes public or stays silent. It is the cheapest P0
  to close.
- **Fix:** add `SECURITY.md` — supported versions, a private contact (GitHub security advisory or
  email, not public issues), a response-time expectation, and a one-line scope note that pre-v3.5
  builds ship without API auth (so "no auth" reports aren't duplicate noise). A policy, not an
  exploit catalogue.
- **Owner:** Liam.

### 1.2 P1 — correctness & data integrity

| ID | Finding | Evidence | Owner |
|----|---------|----------|-------|
| COR1 | **Webhook treats HTTP 4xx/5xx as success.** `_dispatch` logs the status at INFO and never calls `raise_for_status()`, so a 401/429/503 from Slack/PagerDuty is recorded as delivered and never retried — the notification path fails silently. | `webhook.py:200-205`; no failing-status test in `tests/test_webhook.py` | Matthew |
| COR2 | **Migration downgrade misrepresents data.** `c3d4e5f6a1b2` restores `confirmed_count` with `server_default='1'` on rollback; every existing row gets count=1 rather than the safe 0, which a pre-v2.5 auto-apply gate would read as "one confirmed success." | `migrations/versions/c3d4e5f6a1b2_v2_5_remediation.py:69` | Matthew |
| COR3 | **`dry_run_diff` is a misnomer and an enum is out of sync.** Every applier sets `dry_run_diff` to `compare_config()` output even on a committed apply (so it holds the diff that *was* applied), and `RemediationEvent.result`'s model comment lists `"dry_run_only"` while `auto_apply.py` actually writes `"blocked"`. This is the forensic record of what was pushed to devices — it should say what it means. | `appliers/base.py:30-33`, `appliers/arista.py:99-117`, `auto_apply.py:160-211`, `storage/models.py:127-129` | Matthew |

### 1.3 P1 — performance & scale (measure first; these are the reasoned hot paths)

| ID | Finding | Evidence | Owner |
|----|---------|----------|-------|
| PERF1 | **`drift_events` has no index on `(device, detected_at)`.** Every hot query (`get_drifts`, `get_drift_history`) filters/sorts on these columns on the largest table; later tables got their indexes, this one was missed. `get_drift_history` also materialises all rows in Python before bucketing — push the 5-min bucket into SQL (`date_trunc`). | `migrations/.../133d1490bda7...py:24-35`, `storage/repository.py:49-90` | Matthew |
| PERF2 | **`running_config` drift payload is unbounded.** A config drift writes the full normalised config into *both* `intent` and `reality` JSON columns, re-created every poll while drift persists; `_normalize_config` is also called 3× per check on multi-thousand-line configs. Decide a seam: store a unified diff + hash, not two full blobs. (Touches the drift-record contract — proposal, not a unilateral fix.) | `differ.py:323-335`, `storage/models.py:35-36`, `storage/repository.py:31-44` | Matthew + Liam (paired seam) |
| PERF3 | **N+1 NetBox calls in `get_intent`.** One `ip_addresses.filter(interface_id=...)` HTTP round-trip per interface — 26 calls for a 24-port switch, ~50 for a 48-port chassis, sequential, every poll. Replace with one `filter(device_id=...)` and a local join. | `netbox_client.py:218-222` | Liam |
| PERF4 | **N+1 `COUNT(*)` on `GET /drifts`.** `confirmed_count` is called once per known issue in a dict comprehension → K+2 queries per dashboard fetch. Replace with one `GROUP BY known_issue_id`. Same pattern on `GET /known-issues`. | `api/app.py:253-254`, `repository.py:172-184` | Matthew |
| PERF5 | **Syslog repoll has no per-device rate limit.** A flapping device or syslog storm queues unbounded one-shot repoll jobs, each opening NAPALM/gNMI + a NetBox call — thread exhaustion. Add a per-device cooldown. *(An `origin/fix/syslog-cooldown-sentinel` branch may already address this — reconcile before filing.)* | `scheduler.py:204-218`, `syslog_receiver.py` | Liam/Matthew |

### 1.4 P1 — security hardening (the go-live checklist)

| ID | Finding | Evidence | Owner |
|----|---------|----------|-------|
| SEC1 | **Pin CORS explicitly.** No `CORSMiddleware` today (safe by default), but nothing *records* that intent — the next person who "fixes" the dashboard with `allow_origins=["*"]` turns an unauthenticated mutating API into a drive-by target. Serve the dashboard same-origin, or set an exact allow-list; forbid wildcard while the API is unauthenticated. | `grep CORSMiddleware src/ → 0 hits`; `frontend/vite.config.js:9` | Matthew |
| SEC2 | **Bound `limit` and `hours`.** Both are untyped-bound ints flowing straight into queries; `?hours=876000` forces a full-history scan into memory on the unauthenticated box. Add FastAPI `Query(ge=1, le=...)` (e.g. limit ≤ 1000, hours ≤ 168). *(Reported independently by three seats.)* | `api/app.py:241,248` | Matthew |
| SEC3 | **Ingress has no TLS and no edge auth.** If an operator flips `ingress.enabled=true`, the API and its data serve over plaintext with no edge protection. Make TLS the default expectation for public ingress; document that public exposure requires HTTPS + an edge auth layer until v3.5. | `helm/netdrift/templates/ingress.yaml`, `values.yaml:24-28` | Liam/Matthew |
| SEC4 | **Sanitise apply error responses.** `detail=f"Apply failed: {exc}"` / `"Dry-run failed: {exc}"` echo raw NAPALM/gNMI exception text — hostnames, IPs, partial config — to an unauthenticated client. Return a generic message + server-side ref; log the detail. (Webhook handling is already good: `webhook.py:47` redacts the URL, `_validate_url` blocks SSRF.) | `api/app.py:485,571,601` | Matthew |
| SEC5 | **Keep the webhook SSRF guard singular.** Outbound webhook is the one attacker-influenceable request from inside the network, dispatched from *two* paths (scheduler + API). Ensure both funnel through one validated `WebhookDispatcher.fire`, and `WEBHOOK_ALLOW_PRIVATE` is read in exactly one place — not re-implemented per path. | `webhook.py`, `auto_apply.py`, `api/app.py` background dispatch | Matthew |
| SEC6 | **`httpx` is a production dependency declared dev-only.** Prod `webhook.py` imports `httpx`, but it sits under `[project.optional-dependencies].dev`, and the Dockerfile installs without dev extras — a production/container install can fail to import the webhook path. Move `httpx` to the main `[project]` dependencies. | `pyproject.toml:27` vs `webhook.py` | Liam/Matthew |

### 1.5 P1 — platform & operations

| ID | Finding | Evidence | Owner |
|----|---------|----------|-------|
| OPS1 | **Helm deployments have no resource limits and the scheduler has no liveness probe.** A runaway poll consumes unbounded node CPU/memory; a deadlocked scheduler thread is never restarted (the API has a `/health` probe; the scheduler has none). Add `resources:` and a scheduler liveness check. | `helm/netdrift/templates/{api,scheduler}-deployment.yaml`, `values.yaml` | Liam/Matthew |
| OPS2 | **The Helm chart points at an image CI never builds.** `values.yaml:6` references `ghcr.io/liamseanrose/...` but `.github/workflows/` only lints+tests — nothing builds or pushes the image, so a fresh `helm install` pulls a tag that does not exist. Add a `release.yml` (SHA-pinned `build-push-action`) on `v*` tags. | `helm/netdrift/values.yaml:6`, `_helpers.tpl:52-54`, `.github/workflows/` | Liam/Matthew |
| OPS3 | **Database growth needs a TTL before any public instance.** ~2–4 GB/year of `drift_events` at 10 devices / 5-min polls; the Helm PVC defaults to 5 Gi (~fills in months). The v3.5 retention migration is **mandatory** before promoting a public box; raise the PVC default to 20 Gi with a documented growth model; add a TTL for `RemediationEvent` rows too. | `ROADMAP_POST_V2.5.md:302`, `helm/netdrift/values.yaml:51`, `storage/models.py` | Matthew |
| OPS4 | **Syslog receiver uses `print()`.** The two operational signals (bind, per-trigger poll) bypass the logging framework — no level, no timestamp, invisible to log aggregation. Use `logging.getLogger("netdrift.syslog_receiver")`. | `syslog_receiver.py:72,108` | Liam |

### 1.6 P1 — structural readiness (what the architecture needs next, before v4.0)

| ID | Finding | Evidence | Owner |
|----|---------|----------|-------|
| ARCH1 | **Promote one canonical `fingerprint(drift) -> str`.** The `object_type\|field\|drift_kind` identity that ties drift → known-issue → pattern → fuzzy-match is re-derived in `auto_apply._fingerprint`, `diagnose.py`'s tuple keys, and will be again in the v4.0 pattern loader and v5.0 `fingerprint.py`. The whole post-v3.0 pattern/matching roadmap depends on this string matching exactly — give it one owner before v4.0 opens. | `auto_apply.py:53-56`, `diagnose.py:1-60`, `fingerprint.py` | Matthew |
| ARCH2 | **De-duplicate intent-resolution.** `cli.py` re-implements `_resolve_intent_fn` byte-for-byte from `pipeline.py` and repeats the collector-dispatch-or-error block. The registry unified the collector *table* but not the glue around it — a divergence trap as more intent sources land. Extract one shared resolver. | `cli.py:31-45,111-117` vs `pipeline.py:37-51,95-101` | Liam + Matthew |

### 1.7 P1 — test coverage on the dangerous paths

| ID | Finding | Evidence | Owner |
|----|---------|----------|-------|
| TEST1 | **No partial-failure test for multi-drift auto-apply.** The most dangerous path (pushes config to multiple devices in one call) has no test for "two matched issues, one succeeds, one fails" — including whether the consecutive-failure counter is contaminated by a mid-loop commit. | `tests/test_auto_apply.py:427`; `auto_apply.py` loop | Matthew |
| TEST2 | **Nokia gNMI fixtures are unvalidated against a live device.** The BGP/OSPF collector is live pipeline code, but its tests pass against `FakeGNMIClient` shapes hand-derived from the YANG spec. Add `pytest.mark.xfail`/`skip` with the CLAUDE.md reason so the team is forced to re-verify when the Nokia gets routing — otherwise false-green tests mask a live collection failure. | `tests/test_nokia.py`, `collectors/nokia.py`, CLAUDE.md note | Liam |

### 1.8 P1 — documentation gaps that block a safe launch

| ID | Finding | Evidence | Owner |
|----|---------|----------|-------|
| DOC1 | **Docs say v1.0; the code is v3.0.** README and `docs/index.md` describe a v1.0 *detector* and omit that the tool now writes config back to devices — the single most important safety fact about it. Update status/features (Cisco + Juniper, config-level drift, opt-in auto-remediation, webhooks, remediate/apply API); re-verify the Quickstart runs. | `README.md:8`, `docs/index.md:59` | Liam |
| DOC2 | **Auto-remediation is undocumented for users.** Zero user-facing hits for remediate/auto-apply/webhook; the three-layer kill-switch (`AUTO_REMEDIATION_ENABLED` → per-issue `auto_apply_enabled` → per-device pause), the do-not-apply list, and the dry-run flow live only in `.env.example` and `schema.md §9`. Add `docs/remediation.md` (linked in `mkdocs.yml`): what apply does, the kill-switches, "off by default." | `grep remediat README.md docs/ → 0` | Matthew (with Liam) |
| DOC3 | **No deploy/operations + secrets-hardening guide.** No document tells an operator how to run netdrift safely against a real network. Add `docs/deployment.md`: prod env vars, "change the default Postgres password," do-not-expose-the-API-until-v3.5-auth guidance, the webhook SSRF note, Helm quickstart — as a hardening checklist, not exploit detail. | no `docs/*deploy*` / `*security*` | Liam |

*P2 follow-ups (noted, not filed): the two `CONTRIBUTING` files diverge; there is no
`CHANGELOG.md` for a release-tagged public tool.*

---

## 2. Pillar II — Pre-launch security: the go-live gate

### The decision

**Recommendation: launch the read-only demo-mode instance now; gate the live, mutating
instance behind v3.5 auth plus the SEC/B hardening above.**

This is not a new tradeoff — a prior council already settled on demo-mode (static seeded data,
no scheduler, no live devices) as the public-launch vehicle. That decision also *resolves* the
security seat's BLOCK: demo-mode never exposes the network-mutating surface, so B1 and the apply
paths are simply not reachable on the public box. The "zero known vulnerabilities at go-live"
bar is reachable for demo-mode once B3, B4, SEC1, SEC2, and SEC4 are closed.

The live instance is a separate gate. It should not be publicly reachable until: v3.5 auth lands
(B1), TLS + edge auth front it (SEC3), error responses are sanitised (SEC4), and the webhook
SSRF guard is confirmed singular (SEC5).

> **This is the one item to confirm with Liam.** The recommendation aligns with the settled
> demo-mode plan, but "go public before v3.5" is a product/risk call. If the answer is "live
> data now," then B1 (a bearer-token gate) becomes a hard P0 prerequisite, not a v3.5 item.

### Pre-launch hardening checklist (demo-mode)

- [ ] **B4** `SECURITY.md` with a private disclosure channel.
- [ ] **B3** No default credentials; DB bound to localhost; containers non-root.
- [x] **SEC1** CORS posture pinned (same-origin preferred); wildcard forbidden.
- [x] **SEC2** `limit`/`hours` bounded at the API boundary.
- [x] **SEC4** Apply/dry-run error responses sanitised.
- [ ] **SEC6** `httpx` moved to runtime dependencies (prod import path works).
- [ ] **DOC3** Deployment/hardening guide published.

### Additional gate for the live (mutating) instance

- [ ] **B1** Auth on every state-changing route (v3.5).
- [ ] **SEC3** TLS + edge auth in front of the ingress.
- [ ] **SEC5** Single validated webhook dispatch path.

---

## 3. Pillar III — Future roadmap (post-v3.0)

### Inherited and settled (listed, not re-debated)

v3.5 API auth + drift-acknowledge behind it; the `WebhookDispatcher` daemon-thread pattern;
per-device `auto_remediation_paused` kill-switch; Juniper as Liam's parallel track; static
landing page + MkDocs docs site on free Pages hosting; demo-mode as the public-launch vehicle.

### New / re-prioritised

- **R1 — Pull firmware-version drift and change-window suppression into v3.5.** Both are among
  the highest value-to-effort items in the backlog (~1 week combined) but sit unscheduled in
  `PROPOSED_ADDITIONS.md` while 4–8-week items are version-locked. Firmware drift
  (`get_facts().os_version` vs intent) is the most universally relevant drift type and needs only
  a one-line schema field (`software_version: str` — two-partner sign-off). Change-window
  suppression directly serves v3.5's "safe in a shared environment" theme and shares the
  suppression code path with drift-acknowledge. *(Evidence: `PROPOSED_ADDITIONS.md:135-157,
  236-267`.)*
- **R2 — Make demo-mode the default first-run experience, not just hosting plumbing.** Today a
  user needs NetBox + Containerlab + a vendor image + Postgres before seeing a single drift
  event — a funnel that kills most evaluators. `pip install` → `driftcheck demo` → a dashboard
  with realistic seeded drift (the pattern Grafana/Sentry/Supabase all ship) should be step 1 of
  the README. This builds on the settled demo-mode decision rather than re-opening it.
- **Structural prerequisites:** ARCH1 (canonical fingerprint) is the gate for the v4.0 pattern
  library and v5.0 fuzzy matching — land it before that work opens.

---

## 4. Pillar IV — Low-cost AI/LLM opportunities

Framing from the review: netdrift already has a deterministic `diagnose.py` cause-hint table and
a deterministic fingerprint/matching core. Those are assets, not gaps — the bar for "why AI beats
what's here" is therefore *high*, and every credible LLM feature must **augment, not replace**
the deterministic paths. The right shape for an open-source, self-hostable tool is: **opt-in,
read-only, generate-once-and-cache, local model by default, bring-your-own-key for hosted.**

### Build (genuinely low-cost and beneficial)

- **AI1 — Natural-language drift explanations.** Give an LLM the actual intent/reality values +
  platform + co-occurring drift; get a specific plain-English root-cause hint ("Ethernet3 is
  admin-down but intent expects up; went down 14:32 alongside 3 other ports — likely a bulk
  shut"). The static `diagnose.py` table is the offline fallback *and* the grounding prompt.
  **Cost:** ~100–200 input + ~250 output tokens per event ≈ **$0.0002** on a small hosted model
  (gpt-4o-mini), ~**$6/month** at 1,000 events/day — or **$0** self-hosted (Ollama `llama3.2:3b`
  on the lab VM). The design choices that keep it cheap: store the result in a nullable
  `explanation` column keyed on fingerprint (one explanation per *structural* drift, not per
  row); generate on the scheduler cycle, **never** on the `GET /drifts` read path. The expensive-
  theatre failure mode is a hosted call per page load with no cache. *(Ties into ARCH1: the
  fingerprint is the cache key.)*
- **AI2 — Remediation summaries.** Same token profile as AI1; input is the existing
  `rendered_commands` + `dry_run_diff`. Ship alongside AI1.
- **AI3 — Root-cause hints for novel fingerprints.** Adds value only where no `KnownIssue.cause`
  exists yet; low cost. Build after AI1/AI2.
- **AI4 — Anomaly triage (expected noise vs real incident).** Low cost (~500 tokens), pairs with
  v3.5 SLA alerting, but needs enough history to evaluate accuracy first — target v4.5.

### Defer

- **AI5 — Chat-over-drift-history.** *Council arbitrated.* Product valued it as the second-best
  AI bet; finops costed it at RAG over a 21M-row/year table, ~$25/month, and 4–8 weeks of
  chunking infrastructure for a feature one engineer uses at a time. **Decision: DEFER** — the
  value is convenience, not insight, and the dashboard already serves the common cases. *Revisit
  when:* there is multi-user demand, or after AI1 ships and the v3.5 retention TTL bounds history
  size. If built, it must be strictly read-only and must emit the structured query it ran.

### Do not build (theatre or actively harmful)

- **Config translation between vendors** — not a cost problem, a *reliability* problem; output
  needs human review every time, which eliminates the automation value. **Block** until a
  validation harness exists.
- **LLM-generated remediation config** — dangerous: the appliers push to live devices with a
  whole-network blast radius; a hallucinated snippet is a P0 outage. An LLM may *suggest* a fix
  for human review, but must **never** feed the auto-apply path. Remediation stays the settled
  deterministic `restore_intent`/`raw_snippet` union.
- **LLM-based fuzzy fingerprint matching** — already correctly rejected in the roadmap; embeddings
  break the self-hosted 2-vCPU promise, and template-normalisation + Jaccard solves it
  deterministically and testably. Do not reopen.
- **Opaque AI anomaly *scoring*** (distinct from AI4's triage) — an unexplained score over a tool
  whose entire value is *explainable, auditable* diffs is a net negative for trust.

**Net AI recommendation:** build AI1 + AI2 now as opt-in, read-only, cached add-ons defaulting to
a local model; everything touching device config or the matching core stays deterministic.

---

## Appendix — finding index

| ID | Pillar | Priority | Seat(s) |
|----|--------|----------|---------|
| B1 | Security/Audit | P0 | security, technical-writer |
| B2 | Audit | P0 | test |
| B3 | Security | P0 | devops (+security, platform) |
| B4 | Docs/Security | P0 | technical-writer |
| COR1 | Audit | P1 | test |
| COR2 | Audit | P1 | platform |
| COR3 | Audit | P1 | principal |
| PERF1 | Performance | P1 | platform, performance |
| PERF2 | Performance | P1 | principal, performance |
| PERF3 | Performance | P1 | performance |
| PERF4 | Performance | P1 | performance |
| PERF5 | Performance | P1 | finops |
| SEC1–SEC6 | Security | P1 | security (+principal) |
| OPS1–OPS4 | Platform | P1 | platform, devops, finops |
| ARCH1–ARCH2 | Architecture | P1 | principal |
| TEST1–TEST2 | Testing | P1 | test |
| DOC1–DOC3 | Docs | P1 | technical-writer |
| R1–R2 | Roadmap | feature | product |
| AI1–AI5 | AI/LLM | feature | product, finops |

**Glossary:** *drift* — a difference between intent and reality. *intent* — desired state from
NetBox/Nautobot. *reality* — live device state. *remediation* — config that restores intent.
*restore_intent* — the deterministic, auto-apply-eligible remediation grant. *known_issue* — a
recognised, fingerprinted recurring drift. *fingerprint* — the `object_type|field|drift_kind`
identity of a drift.

*Generated by a design-council review pass on 2026-06-01. Findings only — no code was changed.
Detailed orchestration log retained outside the repo.*
