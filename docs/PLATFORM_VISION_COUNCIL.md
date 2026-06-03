# netdrift — Platform / Suite Vision (design council)

**Date:** 2026-06-03
**Method:** design council, 7 role-specialised seats, debate + ideation.
**Verdicts:** 1 BLOCK (product), 6 CONCERNS, 0 APPROVE.
**Status:** adopted — reject the platform shell now; pursue the sequenced,
spine-reusing expansion below.

> A planning record, not a spec. It settles the direction (front page + more
> apps, without becoming a premature platform) and seeds the buildable work.

## 1. The question

Should the project grow from a single tool into a multi-app, self-hostable
network-operations platform — a "front page" / app shell where netdrift is one
app, NetBox is integrated "as an app", and additional apps get built — and which
apps/features form the portfolio?

## 2. The decision

**Do not build the multi-app platform shell or bundle NetBox now.** Every seat,
including those drawn to the ambition, landed in the same place: a
front-page-with-bundled-NetBox is the project's **#1 named risk (scope creep)
wearing a strategy hat**. It abandons the just-chosen sysadmin-light user,
dilutes the one-sentence pitch ("free drift detection for NetBox"), and funds
auth + a shell for apps that do not exist yet instead of network value.

**Approve instead a sequenced expansion that reuses netdrift's existing spine**
(collectors + normalized schema + Postgres history + the diff engine) and
delivers the "front page" value inside the current app.

## 3. "NetBox as an app" — pinned

It must **not** mean bundling, embedding, or forking NetBox. That turns netdrift
into a "NetBox distribution", **collapses the Nautobot user base on day one**, and
inherits NetBox's upgrade churn — exactly what the commercial vendor would want.
It means: netdrift stays a separate service that integrates with the user's
**own** NetBox via the stable API, with **deep-links + optional SSO + an optional
read-only companion plugin** for UI surface only.

## 4. What we build — sequenced, low-risk, reuses the spine

1. **Fleet-health home / overview page inside netdrift.** The "front page" users
   actually want: devices, drift counts, SLA status, last poll, at a glance. It
   delivers the platform *feeling* at a fraction of the cost. Design it against a
   per-app **"summary contract"** (count / status / severity / href) so it is
   platform-ready later without a rebuild.
2. **`IntentSource` adapter interface.** Abstract the intent source — NetBox,
   Nautobot, Infrahub, SuzieQ, flat YAML / golden-config template — behind one
   `get_intent()`. Decouples the platform question from the NetBox question and
   protects the normalized schema (the moat).
3. **Golden-config / bring-your-own-intent mode.** Drift and compliance for
   operators with **no NetBox** (a large reach expansion; pure `differ.py` reuse).
4. **Config Backup & Timeline.** The highest-pull net-new capability — a
   self-hosted Oxidized/RANCID *with a good UI*: nightly running-config snapshots,
   "what changed last night", side-by-side history. The target user knows they
   are missing this and solves it today with abandonware. Reuses running-config
   collection + Postgres history.
5. **Prometheus `/metrics` exporter.** Integrate with the monitoring they already
   run; do not build monitoring.
6. **Firmware / EOL tracker.** A thin, sticky add on the software-version data
   already collected.
7. **NetBox webhook receiver.** Trigger an immediate re-check on a NetBox change.

## 5. Do NOT build

Monitoring (LibreNMS / Zabbix / Prometheus own it) and IPAM / CMDB (that is
NetBox — competing with your own dependency). `netmap` discovery stays a separate
repo (prior council ruling holds).

## 6. Defer the multi-app shell — behind a hard gate

Only once **(a)** a real, demanded second app exists on paper **and (b)**
security's conditions (§7) are specced. When it comes, the minimal shape all
seats agreed on:

- reverse-proxy + static app-launcher + SSO handoff;
- each app an independent FastAPI + SPA (no shared-core monolith, no
  plugin-system inside netdrift — it pollutes the differ/pipeline seams);
- shared Postgres with a **schema per app**;
- a **published design-system package** (tokens/components as a lib, not copied
  CSS);
- **compose profiles** (`base` / `netbox` / `ai`) to bound the footprint.

## 7. Security non-negotiables for any future suite

- A shared login is a single point of compromise that reaches the **config-push**
  app, so the auto-remediation gate needs **confirm-on-action, not
  confirm-at-login**.
- **RBAC / authorization scoping before app two.**
- Secrets held by the platform, never exposed to the browser or to other apps;
  NetBox's token stays server-side.
- Never internet-expose a config-pushing platform without VPN / mTLS in front.

## 8. The reframe in one line

You do not need a platform to get the "front page + more apps" you want — you need
a **home page, an intent-adapter seam, and two or three spine-reusing
capabilities (golden-config, config-backup)**. That is most of the vision at a
fraction of the risk, and it keeps the door open to a real suite later.

## Appendix — seat verdicts

| Seat | Verdict | One-line position |
|------|---------|-------------------|
| product-manager | BLOCK | A shell dilutes the one-sentence pitch and abandons the chosen user; build a fleet-health home view inside netdrift instead. |
| principal-engineer | CONCERNS | A shell is cheap, a shared core is where platforms die; smallest honest version is reverse-proxy + launcher + SSO over unchanged apps. |
| domain-expert | CONCERNS | Approve only apps that reuse the collector+diff+history spine; config-backup is the #1 unmet job. Skip monitoring/IPAM. |
| ui-ux-designer | CONCERNS | The front page is a signal-aggregating home, not a launcher grid; define the per-app summary contract before building it. |
| integration-engineer | CONCERNS | Never bundle NetBox (kills Nautobot users); ship an `IntentSource` adapter and integrate via the stable API. |
| platform-engineer | CONCERNS | Shared Postgres with schema-per-app; compose profiles to bound footprint; NetBox-docker-as-substack is a maintenance trap. |
| security-engineer | CONCERNS | Shared auth reaches the config-push app; need RBAC + confirm-on-action + server-side secrets before app two. |

*Generated by a design-council debate pass on 2026-06-03. No code was changed.*
