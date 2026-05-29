# `docs/schema-v1.0-proposal.md` — v1.0 Schema Additions (Proposal)

> **Status: PROPOSAL — not yet agreed.** This document is Person B's proposed set
> of v1.0 schema additions, written up for the joint schema call. Nothing here is
> part of the contract until both partners review and approve it. On approval, these
> changes fold into `docs/schema.md` (Section 2, the rules, Sections 6/7, and the
> changelog) and this proposal file can be deleted or archived.
>
> **Scope:** v1.0 only — config-level drift (`running_config`), as outlined in
> `schema.md` Section 9. No other v1.0 items change the data contract. Scope
> deliberately held narrow per the project's #1 risk (scope creep on the diff
> engine).

---

## 1. Purpose of this document

`schema.md` Section 9 introduces `running_config` but leaves the real design
questions open: how does the intent side produce it, how does the diff engine
compare two raw text strings, and what drift records come out? This proposal
resolves each question with a concrete decision and the reasoning behind it, so
the schema call is a *review-and-ratify* meeting rather than a design session.

Section 2 is the proposed final shape. Section 3 records the decisions and
rationale. Section 4 is the checklist for the call.

---

## 2. Proposed v1.0 device-state object addition

All v0.3 fields are unchanged. v1.0 adds **one new top-level key**: `running_config`.

```python
{
    "device": "core-sw-01",
    "platform": "arista_eos",
    "collected_at": "2026-05-20T14:32:00Z",
    "interfaces":     { ...unchanged from v0.3... },
    "vlans":          { ...unchanged from v0.3... },
    "bgp_neighbors":  { ...unchanged from v0.3... },
    "ospf":           { ...unchanged from v0.3... },

    # --- v1.0 addition ---
    "running_config": "",   # str — full device running config as text, "" if unavailable
}
```

### Field reference (new field only)

| Field            | Type  | Meaning |
|------------------|-------|---------|
| `running_config` | `str` | The device's full running configuration as plain text. On the **reality** side this is the output of `show running-config` (or equivalent). On the **intent** side this is the config rendered from a NetBox Config Template. Empty string `""` when the config cannot be obtained or no template exists — never `None`, following Rule 4. |

---

## 3. Decisions and rationale

### Decision 1 — Field type is `str`, not a structured dict

The running config is raw text. Parsing it into a structured dict (commands,
sections, ACL entries) is the semantic-equivalence problem — deep, vendor-specific,
and explicitly deferred in `PROJECT_PLAN.md`. Keeping `running_config` as `str`
means the v1.0 diff is text-level; vendor-semantic comparison comes in a later
version when we have real drift data to guide the design.

### Decision 2 — Intent side: NetBox Config Templates (Render Config API)

NetBox has a built-in Config Template feature (Jinja2 templates stored as
`/api/extras/config-templates/`). When a device has a template assigned,
calling:

```
GET /api/dcim/devices/{id}/render-config/
```

returns the rendered intended config as plain text. This is the canonical
"what should this device's config look like" answer that NetBox already provides —
no new NetBox plugins or external templating engine required.

**If no template is assigned to the device in NetBox**, `get_intent()` returns
`running_config: ""`. An empty string on the intent side means "we have no
intended config to compare against", and the diff engine skips the field
entirely (see Decision 4). This is not drift — it is an unconfigured intent
source, and a device with no template produces zero config-drift records.

### Decision 3 — Reality side: `show running-config` (or equivalent)

Each collector captures the full running config text and returns it verbatim,
after minimal normalization (see Decision 5). This is Liam's side — each vendor
collector adds one new operation:

| Platform          | Command / API call |
|-------------------|--------------------|
| `arista_eos`      | `show running-config` (eAPI) |
| `nokia_srlinux`   | `info full-context flat` (gNMI / CLI) |
| `cisco_iosxe`     | `show running-config` (NETCONF or SSH) |

The exact method (eAPI, NETCONF, SSH) is the collector's implementation choice
and does not affect the schema contract.

### Decision 4 — Skip the diff if either side is `""`

If `running_config` is `""` on either the intent or reality side, the diff engine
produces **no drift records** for the config field. Rationale:

- Intent `""` → no template in NetBox → nothing to compare against. Not drift.
- Reality `""` → collector could not retrieve the config (auth issue, platform
  limitation, timeout). Emitting a spurious `missing_in_reality` record here would
  be noise, not signal. The underlying failure should surface through other
  monitoring, not as config drift.

This means config drift records are only generated when both sides have content.

### Decision 5 — Minimal normalization only (accept false positives for now)

Before comparing, both sides strip trailing whitespace from each line and
normalize line endings to `\n`. Nothing else. This eliminates the most common
representation-only differences (CRLF vs LF, trailing spaces) without attempting
semantic equivalence.

**Known false positives accepted at v1.0:** comment lines that differ (e.g.
`! Last change: ...` timestamps), section ordering differences, interface
abbreviation differences in the rendered config. These are recorded here as known
gaps, not bugs, and are addressed in a future version once real drift data
informs the design.

### Decision 6 — One drift record for config drift (not per-line)

The diff engine compares the normalized strings. If they differ, it emits
**exactly one drift record** with:

```python
{
    "device": "core-sw-01",
    "object": "config",
    "field": "running_config",
    "intent": "<full normalized intended config text>",
    "reality": "<full normalized running config text>",
    "drift_kind": "value_mismatch",
    "severity": "warning",
    "detected_at": "2026-05-20T14:32:00Z",
}
```

Rationale for one record rather than per-line: per-line records would produce
dozens or hundreds of drift events for a single config mismatch, overwhelming the
severity/count signals the UI relies on. The full text in `intent`/`reality`
carries enough information for a human (or future UI feature) to compute and
display a line-level diff. One record keeps the drift history table compact and
the severity signal meaningful.

**Severity:** `warning`. A config difference means something was changed outside
the documented intended state — important to know, but not an outage indicator.

### Decision 7 — `"config"` as the object type

Existing object types follow `"<type>:<identifier>"` (e.g.
`"interface:Ethernet1"`, `"vlan:20"`). Config drift uses `"config"` with no
identifier suffix — there is only one running config per device. This is a
deliberate exception to the type:identifier pattern, not an oversight. A new
object type entry is added to Section 6 of `schema.md`.

---

## 4. Schema call checklist

Before approving, both partners should confirm:

- [ ] **Decision 1** — `str` field type is agreed. No structured config parsing in v1.0.
- [ ] **Decision 2** — Intent from NetBox Render Config API is agreed. Empty string `""` when no template exists.
- [ ] **Decision 3** — Reality from `show running-config` (or equivalent) is agreed. Exact command/API is each collector's choice.
- [ ] **Decision 4** — Skip diff when either side is `""` is agreed (no spurious drift records).
- [ ] **Decision 5** — Minimal normalization (trailing whitespace + line endings only) is agreed. False positives from timestamps/ordering are accepted.
- [ ] **Decision 6** — One drift record per device (not per-line), `severity: warning`, is agreed.
- [ ] **Decision 7** — `"config"` as the object identifier (no `:<id>` suffix) is agreed.
- [ ] Both partners have signed off on this document (record below).

### Sign-off

| Partner | Sign-off | Date |
|---------|----------|------|
| Person A (Liam) | | |
| Person B (Matthew) | | |

---

## 5. What each side builds after approval

### Person B — intent side + diff engine (this PR's follow-up)

- `netbox_client.py`: call `GET /api/dcim/devices/{id}/render-config/` and add
  `running_config` to the returned dict. Return `""` if the device has no template.
- `differ.py`: add config diff logic — normalize both strings, compare, emit one
  drift record if they differ, skip if either is `""`.
- `tests/fixtures/`: add intent/reality pairs covering: match (no drift), mismatch
  (one record), intent `""` (no drift), reality `""` (no drift).
- `tests/test_differ.py`: tests against the new fixtures.

### Person A — reality side (per collector)

- `collectors/arista.py`: add `show running-config` → `running_config` field.
- `collectors/nokia.py`: add equivalent gNMI / CLI call → `running_config` field.
- `collectors/cisco_iosxe.py`: add `show running-config` → `running_config` field.
- Apply Decision 5 normalization (trailing whitespace + line endings) before returning.

---

*Once both partners sign off on Section 4, fold the changes into `docs/schema.md`
and open implementation branches.*
