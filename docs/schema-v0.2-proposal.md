# `docs/schema-v0.2-proposal.md` — v0.2 Schema Additions (Proposal)

> **Status: PROPOSAL — not yet agreed.** This document is Person A's proposed set
> of v0.2 schema additions, written up for the joint schema call. Nothing here is
> part of the contract until both partners review and approve it. On approval, these
> changes fold into `docs/schema.md` (Section 2, the rules, Section 4, Section 6/7,
> and the worked example) and this proposal file can be deleted or archived.
>
> **Scope:** v0.2 only — VLAN / layer-2 fields, as outlined in `schema.md` Section 9.
> No routing state (that is v0.3). No config-text (v1.0). Scope deliberately held
> narrow per the project's #1 risk (scope creep on the diff engine).

---

## 1. Purpose of this document

`schema.md` Section 9 sketches the v0.2 fields but leaves real design questions
open. This proposal resolves each one with a concrete decision and the reasoning
behind it, so the schema call is a *review-and-ratify* meeting rather than a
design-from-scratch one. Section 2 below is the proposed final shape; Section 3
records the decisions and rationale; Section 4 is the checklist for the call.

---

## 2. Proposed v0.2 device-state object

The v0.1 fields are unchanged. v0.2 adds three keys to each interface and one new
top-level key.

```python
{
    "device": "core-sw-01",
    "platform": "arista_eos",
    "collected_at": "2026-05-20T14:32:00Z",
    "interfaces": {
        "Ethernet1": {
            # --- v0.1 fields (unchanged) ---
            "description": "Uplink to dist-01",
            "enabled": True,
            "ip_addresses": ["10.1.1.5/24"],
            # --- v0.2 additions ---
            "mode": "routed",          # "access" | "tagged" | "routed"
            "untagged_vlan": None,     # int | None
            "tagged_vlans": [],        # list[int], sorted ascending
        },
        "Ethernet2": {
            "description": "Access port - users",
            "enabled": True,
            "ip_addresses": [],
            "mode": "access",
            "untagged_vlan": 10,
            "tagged_vlans": [],
        },
        "Ethernet3": {
            "description": "Trunk to dist-01",
            "enabled": True,
            "ip_addresses": [],
            "mode": "tagged",
            "untagged_vlan": None,
            "tagged_vlans": [10, 20, 30],
        },
    },
    # --- v0.2 addition: top-level VLAN definitions ---
    "vlans": {
        # keys are STRINGS, not ints — see Decision 4
        "10": {"name": "users"},
        "20": {"name": "voice"},
        "30": {"name": "mgmt"},
    },
}
```

### New field reference

| Field                      | Type          | Meaning                                                                 |
|----------------------------|---------------|-------------------------------------------------------------------------|
| `interfaces[].mode`        | `str`         | One of `access`, `tagged`, `routed`. Always present. Never `None`.      |
| `interfaces[].untagged_vlan` | `int \| None` | The access/untagged VLAN ID. `None` when the interface has none (routed interfaces; trunks with no native VLAN). |
| `interfaces[].tagged_vlans` | `list[int]`  | Tagged VLAN IDs, sorted ascending. Empty list `[]` when none.           |
| `vlans`                    | `dict`        | Top-level VLAN definitions. Keys are VLAN IDs **as strings**. Value is a dict with at least `name`. |
| `vlans[].name`             | `str`         | VLAN name. Empty string `""` if unset — never `None`.                   |

---

## 3. Decisions and rationale

### Decision 1 — `mode` is an always-present string; `routed` is a real mode

`mode` is one of exactly three values: `access`, `tagged`, `routed`. It is always
present and is never `None`.

The three values are **exhaustive**. An interface is always doing exactly one of:
carrying one untagged VLAN (`access`), carrying tagged VLANs (`tagged`), or acting
as a layer-3 interface with no VLANs (`routed`). A routed L3 interface is **not**
"modeless" — `routed` *is* its mode. There is therefore no fourth "unknown / no
mode" case the schema needs to represent.

If a collector ever encounters device output it genuinely cannot classify into one
of the three, that is a **collector bug to surface loudly** — not a schema value to
invent. The schema stays clean; the collector is responsible for producing one of
the three.

This keeps `mode` consistent with `schema.md` Rule 4 (no `None`) without needing an
exception, because there is genuinely never a case where an interface lacks a mode.

### Decision 2 — `untagged_vlan` is `int | None`; Rule 4 is reworded

`untagged_vlan` is `int | None`. It is `None` when the interface has no untagged
VLAN — i.e. routed interfaces, and trunk interfaces with no native VLAN.

`schema.md` Rule 4 currently says absent values are *never* `None` — empty string
for unset descriptions, empty list for no IPs. **Proposed reword:** Rule 4 governs
**string and list** fields, where "empty" and "absent" are the same real-world
thing and must look identical so the diff engine never reports representation
noise as drift. It does **not** govern nullable **scalar** fields.

`untagged_vlan` is a nullable scalar. "This interface has no untagged VLAN" is a
genuine, meaningful state, not noise. The honest representations are `None` or a
sentinel int like `0`. `0` is rejected: it is a plausible-looking value ("VLAN 0"
is not a real thing) that a diff, sort, or display could silently mishandle.
`None` states exactly what is true.

**Proposed new wording for Rule 4 in `schema.md`:**

> *Absent values are explicit for string and list fields, never a missing key.
> Unset description → `""`. No IPs → `[]`. Nullable scalar fields (e.g.
> `untagged_vlan`) are typed `X | None` and use `None` to mean "genuinely absent";
> they must still always be present as a key.*

Every interface dict still has **all** its keys, always — `untagged_vlan` is
present as a key, its value is just `None` when there is no VLAN.

### Decision 3 — native VLAN is NOT modelled in v0.2

A trunk port can have a *native VLAN* (untagged traffic on an otherwise-tagged
port). This is a real concept but is an edge case. Per the project's #1 risk
(scope creep on the diff engine), it is **out of scope for v0.2**.

For v0.2, `untagged_vlan` means strictly "the access VLAN of an access port." A
trunk's `untagged_vlan` is `None` even if the device has a native VLAN configured.

Recorded as a known future gap; revisit if a real false positive forces it.

### Decision 4 — `vlans` dict keys are STRINGS, not ints

The top-level `vlans` dict is keyed by VLAN ID **as a string**: `{"10": {...}}`,
not `{10: {...}}`.

Reason: this data will be JSON-serialized — Postgres JSON column, FastAPI response
bodies. **JSON object keys are always strings.** An int-keyed dict `{10: ...}`
silently becomes `{"10": ...}` the moment it round-trips through JSON. If one side
of the diff has int keys and the other has string keys, the diff engine reports
false drift, or key lookups miss. Using string keys everywhere makes the in-memory
representation and the JSON representation identical, removing the landmine.

Note the asymmetry, which is deliberate: `tagged_vlans` stays a `list[int]`. Lists
survive JSON round-trips unchanged, and sorting integers is correct (string sort
would order `["10", "100", "20"]` wrongly). Only **dict keys** must be strings.

### Decision 5 — routed interfaces carry empty VLAN fields, not missing keys

A routed interface has `mode: "routed"`, `untagged_vlan: None`, `tagged_vlans: []`.
The keys are present; the values express "no VLANs here."

This follows the v0.1 precedent (`ip_addresses: []` for an interface with no IPs).
The diff engine never special-cases interface type — it sees consistent keys on
every interface and compares values directly.

### Decision 6 — new drift-record object type `vlan:<id>`

v0.1 drift records only had `object` type `interface:<name>`. The top-level
`vlans` block introduces drift on a VLAN *itself* (e.g. VLAN 20 named `voice` in
NetBox but `Voice-VLAN` on the device). This needs a new object type:

```
"object": "vlan:20"
```

Person B should account for this when extending `differ.py`. The `<id>` is the
VLAN ID as a string, consistent with Decision 4.

### Decision 7 — severity rows for the new fields

Proposed additions to `schema.md` Section 7. These are **starting defaults** —
Section 7 already states severity is refined with experience.

| Situation                                         | Severity   |
|---------------------------------------------------|------------|
| `mode` mismatch (e.g. access vs tagged)           | `warning`  |
| `untagged_vlan` mismatch                          | `warning`  |
| `tagged_vlans` mismatch                           | `warning`  |
| VLAN present in intent, missing in reality        | `warning`  |
| VLAN present in reality, missing in intent        | `info`     |
| VLAN `name` mismatch                              | `info`     |

---

## 4. Checklist for the schema call

Decide each of these together and record the outcome (and any changes) in
`schema.md`'s change-log table.

- [ ] **Decision 1** — `mode` is `access` / `tagged` / `routed`, always present, no `None`. Agree?
- [ ] **Decision 2** — `untagged_vlan` is `int | None`; Rule 4 reworded to scope it to string/list fields. Agree to the reworded Rule 4 text?
- [ ] **Decision 3** — native VLAN out of scope for v0.2. Agree?
- [ ] **Decision 4** — `vlans` dict keys are strings; `tagged_vlans` stays `list[int]`. Agree?
- [ ] **Decision 5** — routed interfaces carry `untagged_vlan: None`, `tagged_vlans: []`. Agree?
- [ ] **Decision 6** — new `vlan:<id>` drift-record object type. Person B to confirm `differ.py` impact.
- [ ] **Decision 7** — severity defaults for the new fields. Agree as a starting point?
- [ ] **Open item** — confirm `vlans[].name` is the only required key in a VLAN value dict for v0.2 (no `vlan_id` duplicated inside, since it's the key).
- [ ] **Open item** — does `seed_netbox.py` need VLAN objects added to mirror the lab? (Person A — likely yes.)
- [ ] On agreement: update `schema.md` Sections 2, 3 (Rule 4), the field tables, Section 6, Section 7, the worked example, and the change-log row. Archive or delete this proposal.

---

## 5. What this unblocks

Once agreed, both sides can proceed independently against the widened contract,
exactly as in v0.1:

- **Person A** — extends the Arista collector to populate `mode`, `untagged_vlan`,
  `tagged_vlans`, and the top-level `vlans` block; builds the second-vendor
  collector against the same shape; adds VLANs to `seed_netbox.py`.
- **Person B** — extends `differ.py` to diff the new fields and the new `vlan:<id>`
  object type; designs the Postgres `drift_events` table; builds FastAPI + the
  React dashboard; the scheduler.

The schema is the seam. Agree it first, then neither side blocks the other.