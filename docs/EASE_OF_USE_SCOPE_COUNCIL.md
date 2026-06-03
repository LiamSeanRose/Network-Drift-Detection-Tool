# netdrift — Ease-of-Use Scope Decision (design council)

**Date:** 2026-06-03
**Method:** design council, 8 role-specialised seats, debate mode. CEO: Liam.
**Verdicts:** 7 × CONCERNS, 1 × conditional APPROVE, 0 × BLOCK.
**Status:** adopted, with the persona reframe in §2 accepted by the CEO.

> A planning record, not a spec. It sets the direction and the guardrails for an
> ease-of-use expansion; the per-phase tickets in §6 are the implementable
> follow-ups.

## 1. The question

Expand netdrift from "a tool for network engineers (CLI + dashboard)" to an
application someone can deploy on their own network and operate entirely from the
UI — few clicks, strong in-app help, no external research required — accepting a
hardware floor rather than compromising the experience for weak machines.

## 2. The decision: approve the ambition, reframe the user

The council was unanimous on one point, so it leads. **"Usable by a
non-network-worker" does not cohere with the product.** A drift record *is*
`bgp_neighbor 10.0.0.2 session_state: idle (intent: established)` — meaningless
without network knowledge, and a person with no network literacy never seeded
NetBox with intent in the first place. The app can teach *operating netdrift*; it
cannot teach *networking* in a tooltip.

**Adopted reframe:** aim at the user who exists and is underserved — the
**network-literate, sysadmin-light operator** (homelabber, one-person SMB IT team,
junior NOC tech). The win is **removing the platform-ops tax, not dumbing down the
domain.** The "no research needed" promise covers *using the app*, not
*understanding BGP*. This honours the original intent (few clicks, great help,
self-deploy, full-UI operation) while targeting a real persona.

## 3. What we build — phased, CLI stays, v5.0 not displaced

### Phase 1 — coherent MVP (does not cross the security line)
- **One-command deploy.** `docker compose up` bundling Postgres, an auto-migrate
  init container, a named volume, and the API + built frontend on a single port.
  **localhost-HTTP by default.** A nightly `pg_dump` to a bind-mount ships on day
  one — `docker compose down -v` silently destroying drift history was the
  devops seat's top risk.
- **First-run experience.** The empty dashboard becomes a guided checklist, not a
  blank console that reads as broken. **In-UI API-key generation** (show-once,
  auto-attached — retire the "paste a key" field). A **per-device "Run check
  now"** trigger so a new user is not left waiting for the scheduler. Pattern
  import and demo-seed become one-click buttons.
- **Deterministic help layer** (this carries "no research", not the LLM): a
  persistent intent-vs-reality explainer, a ~15-term hover glossary on drift rows,
  a severity legend, and coached empty states that route first-run users into
  `driftcheck demo`.
- **WCAG 2.1 AA pass.** The sparkline gets a text equivalent / visually-hidden
  data table (zero information without vision or colour was the a11y seat's top
  offender); colour is never the sole signal; modals trap focus; date pickers are
  labelled.

### Phase 2 — "operate *entirely* from the UI" (crosses the security line; design first)
- **Encrypted-at-rest credential store** with a key held *outside* the database,
  then a full connection wizard including device credentials entered in the
  browser. This is a secrets-vault backend, not a settings form, and it is built
  before "configure everything in the UI" is promised.
- **Auth on by default whenever the bind is not localhost.** Harden the apply
  path: a second distinct secret, off in the default compose, and a persistent
  red banner while auto-apply is active.

### AI-assist — a separate, documented tier, off by default
Core floor stays **2 vCPU / 2 GB**; the AI tier is **4 vCPU / 8 GB+ (or a GPU)**.
A local LLM only conflicts with "self-hostable on modest hardware" if it is
bundled default-on, so it stays a pluggable, opt-in provider. The UI gates AI
features on an `/ai/health` check. Both promises then hold. The deterministic help
layer — not the model — is what carries a non-expert through the app.

## 4. Non-negotiable guardrails (security seat, near-BLOCK)

- `NETDRIFT_DISABLE_API_AUTH` stays out of every user-facing guide, wizard, and
  the default compose file. It must never become the path of least resistance.
- Device credentials entered through the UI are encrypted at rest — never
  plaintext in Postgres. A single DB leak must not yield the whole network.
- Auto-apply (config push to live devices) is never reachable by "minimal clicks."
  Novice + config-push + few-clicks is how a production fabric goes down.

## 5. Risks, and what we deferred

**Top risk:** scope creep — the project's named #1 risk — consuming the v5.0
fuzzy-matching slot, while UI-stored credentials quietly turn a read-mostly diff
tool into a network-wide config-push surface. **Mitigation:** Phase 1 delivers
value without crossing the credential/security line; Phase 2 waits behind the
vault design; the CLI stays as the power-user path; v5.0 is sequenced explicitly,
not displaced.

**Deferred / out of scope:** teaching networking inside the app (unbounded, and AI
as the *teaching* foundation is a correctness and liability surface — AI stays an
enhancement); a Tauri/Electron desktop app; Helm aimed at novices (Helm stays
expert-tier).

## 6. Phase 1 ticket seeds (for triage, not yet claimed)

- Single-image `docker-compose.yml`: bundled Postgres, auto-migrate init
  container, named volume, one published port, localhost bind default.
- Nightly `pg_dump` to a bind-mount + a "down -v deletes your data" warning.
- `POST /api-keys` already exists; add in-UI key generation (show-once) and drop
  the paste field.
- `POST /devices/{name}/check` (manual drift check) + a dashboard "Run now" button.
- First-run empty-state checklist component + one-click "Load starter patterns" /
  "Load demo data."
- Help layer: intent-vs-reality banner, glossary data + hover component, severity
  legend.
- a11y sweep to WCAG 2.1 AA, sparkline text-equivalent first.

## Appendix — seat verdicts

| Seat | Verdict | One-line position |
|------|---------|-------------------|
| principal-engineer | CONCERNS | The API must now trigger the pipeline and own device credentials — a real new write surface, not a veneer; keep the CLI. |
| product-manager | CONCERNS | "Non-network-worker" is incoherent; re-aim at the no-sysadmin, network-literate solo operator — same effort, a user who exists. |
| ui-ux-designer | CONCERNS | The empty dashboard is the highest-leverage screen; ship a 3-step connection wizard with live "Test" buttons and in-UI key generation. |
| technical-writer | CONCERNS | We can explain the tool, not the domain; deterministic glossary + intent-vs-reality + empty-state coaching is the "no research" floor. |
| devops-engineer | CONCERNS | `docker compose up` with bundled PG, auto-migrations, named volume, localhost HTTP; ship backups day one or users lose history. |
| security-engineer | CONCERNS | Browser credentials are a vault surface; encrypt at rest, auth-on when not localhost, keep DISABLE_API_AUTH out of every guide. |
| accessibility-specialist | CONCERNS | A broader audience makes WCAG 2.1 AA table-stakes; the sparkline is the top offender — give it a text equivalent. |
| platform-engineer | APPROVE* | Core stays 2 vCPU / 2 GB; the spec stance only bites if AI is bundled default-on — keep AI a separate opt-in tier and both promises hold. |

*conditional on the AI tier staying opt-in.

*Generated by a design-council debate pass on 2026-06-03. No code was changed.*
