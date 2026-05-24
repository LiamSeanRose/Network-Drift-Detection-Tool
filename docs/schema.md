# `docs/schema.md` — The Normalized Schema

> **This file is the contract between the collector side (Person A) and the diff
> engine side (Person B).** Both `get_intent()` (NetBox) and `get_reality()`
> (device) must return data in *exactly* the shape defined here. The diff engine
> assumes the data already matches this shape and does not care where it came from.
>
> **Rule: any change to this file is a merge request that BOTH partners review and
> approve.** Do not change the schema unilaterally.
>
> **Status:** v0.2. VLAN / layer-2 fields added 2026-05-23 (see the change log).
> Further changes require a merge request both partners approve.

---
## 1. Why this exists

Person A builds code that pulls data *from NetBox* and *from devices*. Person B
builds the code that *compares* that data. They never touch the same files. The only
thing they share is the **shape of the data that passes between them**. That shape is
defined here.

If both sides honour this document, they can develop completely independently:

- Person A builds collectors against this spec.
- Person B builds the diff engine against hand-written sample dicts (see
  `tests/fixtures/`) that follow this spec.

They only integrate at the end, and it just works — because both followed the same
contract.

---

## 2. The device-state object (v0.2)

This is what `get_intent(device_name)` and `get_reality(device)` both return.

```python
{
    "device": "core-sw-01",                  # str
    "platform": "arista_eos",                # str
    "collected_at": "2026-05-20T14:32:00Z",  # str, ISO 8601 UTC
    "interfaces": {
        # key = canonical full interface name
        "Ethernet1": {
            "description": "Uplink to dist-01",  # str
            "enabled": True,                     # bool
            "ip_addresses": ["10.1.1.5/24"],     # list[str], sorted
            "mode": "routed",                    # "access" | "tagged" | "routed"
            "untagged_vlan": None,               # int | None
            "tagged_vlans": [],                  # list[int], sorted
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
    "vlans": {
        # keys are VLAN IDs as STRINGS, not ints — see Rule 7
        "10": {"name": "users"},
        "20": {"name": "voice"},
        "30": {"name": "mgmt"},
    },
}
```

### Field reference

| Field                  | Type        | Meaning                                                        |
|------------------------|-------------|----------------------------------------------------------------|
| `device`               | `str`       | Device name. Must be identical in NetBox and on the device.    |
| `platform`             | `str`       | Normalized platform id. See Section 4 for the allowed values.  |
| `collected_at`         | `str`       | When this snapshot was taken. ISO 8601, UTC, `Z` suffix.       |
| `interfaces`           | `dict`      | Keyed by canonical full interface name.                        |
| `interfaces[].description` | `str`   | Interface description. Empty string `""` if unset — never `None`. |
| `interfaces[].enabled` | `bool`      | **Administrative** state: is the interface NOT shut down? This is config intent, not link/carrier state. |
| `interfaces[].ip_addresses` | `list[str]` | IPs in CIDR notation (`"10.1.1.5/24"`). Sorted ascending. Empty list `[]` if none. |
| `interfaces[].mode`    | `str`       | One of `access`, `tagged`, `routed`. Always present, never `None`. See Rule 8. |
| `interfaces[].untagged_vlan` | `int \| None` | The access/untagged VLAN ID. `None` when the interface has no untagged VLAN (routed interfaces; trunks). |
| `interfaces[].tagged_vlans` | `list[int]` | Tagged VLAN IDs, sorted ascending. Empty list `[]` if none. |
| `vlans`                | `dict`      | Top-level VLAN definitions. Keyed by VLAN ID **as a string**. Value is a dict with at least `name`. On the intent side, VLANs are scoped to the device's NetBox **site** (v0.2). |
| `vlans[].name`         | `str`       | VLAN name. Empty string `""` if unset — never `None`.          |

---

## 3. Schema rules — apply to BOTH sides

These rules exist so the diff engine never reports a difference that is really just
a formatting or ordering artifact.

1. **Interface names are canonical and full.** `Ethernet1`, never `Et1`.
   `GigabitEthernet1/0/1`, never `Gi1/0/1`. **Each collector is responsible for
   expanding abbreviations** before returning. The diff engine assumes names already
   match exactly and does no normalization of its own.

2. **Timestamps are ISO 8601, UTC, with a `Z` suffix.** Example:
   `"2026-05-20T14:32:00Z"`. Never local time. Never a naive datetime. In Python,
   produce it with a timezone-aware UTC datetime and `.isoformat()`, or
   `datetime.now(timezone.utc)`.

3. **Lists are sorted before returning.** `ip_addresses` and `tagged_vlans` are
   sorted ascending. Any list-valued field added later is also sorted. This means
   `["10.1.1.5/24", "10.1.1.6/24"]` and `["10.1.1.6/24", "10.1.1.5/24"]` can never
   be reported as drift. `tagged_vlans` is sorted as a list of integers.

4. **Absent values are explicit for string and list fields, never a missing key.**
   - Unset description → `""` (empty string)
   - No IP addresses → `[]` (empty list)
   - No tagged VLANs → `[]` (empty list)
   - This rule governs **string and list** fields, where "empty" and "absent" are
     the same real-world thing and must look identical so the diff engine never
     reports representation noise as drift.
   - **Nullable scalar fields** (e.g. `untagged_vlan`) are typed `X | None` and use
     `None` to mean "genuinely absent". They must still always be present as a key.
   - Every interface dict has all six keys, always.

5. **`enabled` is administrative state.** It answers "is this interface configured
   as `no shutdown`?" — not "is the cable plugged in?" Link/carrier state is a
   separate field added in a later version.

6. **`device` must match across all three places** — the name in NetBox, the name
   on the physical/virtual device, and the value in this object. If they differ,
   intent and reality cannot be paired up. Keep them consistent.

7. **`vlans` dict keys are strings.** The top-level `vlans` dict is keyed by VLAN
   ID as a **string** (`"10"`), not an integer. This data is JSON-serialized
   (Postgres JSON columns, FastAPI responses), and JSON object keys are always
   strings — an int-keyed dict silently becomes string-keyed on a JSON round-trip.
   Keeping keys as strings everywhere makes the in-memory and JSON representations
   identical, so the diff engine never sees false drift from a type mismatch.
   Note the deliberate asymmetry: `tagged_vlans` stays a `list[int]` — lists
   survive JSON unchanged, and integer sorting is correct.

8. **`mode` is one of exactly three values** — `access`, `tagged`, `routed` — and
   is always present. A routed layer-3 interface is **not** "modeless"; `routed`
   *is* its mode. There is no fourth "unknown" value. If a collector cannot
   classify an interface into one of the three, that is a collector bug to surface
   loudly — not a schema value to invent.

9. **The `vlans` block contains every VLAN present on the device, including the
   default VLAN (VLAN 1).** There is no special-casing of reserved or default
   VLAN IDs — neither collectors nor the diff engine filter them. VLAN 1 is a
   normal VLAN: the collector reports it, and intent (NetBox) is expected to
   document it like any other. If a VLAN exists on one side but not the other,
   that is genuine drift and is reported as such. The fix for "VLAN 1 shows as
   undocumented drift" is to document VLAN 1 in NetBox, not to filter it out.

---

## 4. Allowed `platform` values

A fixed, normalized set. Collectors must emit one of these exact strings — not the
vendor's own naming, not NetBox's slug if it differs.

| Value          | Vendor / OS              | Introduced |
|----------------|--------------------------|------------|
| `arista_eos`   | Arista EOS               | v0.1       |
| `nokia_srlinux`| Nokia SR Linux           | v0.2       |
| `frr`          | FRRouting                | v0.2       |
| `cisco_iosxe`  | Cisco IOS-XE             | v1.0+      |
| `juniper_junos`| Juniper Junos            | v1.0+      |

If a new platform is needed, add it here in the same merge request that adds its
collector.

---

## 5. The drift record (output of the diff engine)

The diff engine consumes two device-state objects (intent + reality for the same
device) and returns a `list` of drift records. Each record is one difference.

```python
{
    "device": "core-sw-01",
    "object": "interface:Ethernet1",   # "<type>:<identifier>"
    "field": "ip_addresses",
    "intent": ["10.1.1.5/24"],         # value from NetBox
    "reality": ["10.1.1.9/24"],        # value from the device
    "drift_kind": "value_mismatch",    # see Section 6
    "severity": "warning",             # info | warning | critical
    "detected_at": "2026-05-20T14:32:00Z",
}
```

### Field reference

| Field         | Type   | Meaning                                                          |
|---------------|--------|------------------------------------------------------------------|
| `device`      | `str`  | Device the drift was found on.                                   |
| `object`      | `str`  | `"<type>:<identifier>"`. Types: `interface` (e.g. `interface:Ethernet1`) and `vlan` (e.g. `vlan:20`). |
| `field`       | `str`  | Which field drifted: `description`, `enabled`, `ip_addresses`, `mode`, `untagged_vlan`, `tagged_vlans`, `name`, or the sentinel `_interface` / `_vlan`. |
| `intent`      | varies | The value NetBox says it should be. Type matches the field.      |
| `reality`     | varies | The value the device actually reports.                          |
| `drift_kind`  | `str`  | Category of difference. See Section 6.                           |
| `severity`    | `str`  | `info`, `warning`, or `critical`. See Section 7.                 |
| `detected_at` | `str`  | ISO 8601 UTC. When the diff was computed (not when collected).   |

If intent and reality match perfectly, the diff engine returns an **empty list**.

---

## 6. `drift_kind` values

| Value                | Meaning                                                            |
|----------------------|--------------------------------------------------------------------|
| `value_mismatch`     | Both intent and reality have a value for this field, and they differ. |
| `missing_in_reality` | Intent has it, the device does not. (e.g. NetBox lists an interface or VLAN the device doesn't have, or an IP the device isn't carrying.) |
| `missing_in_intent`  | The device has it, NetBox does not. Undocumented configuration.    |
| `extra`              | Generic catch-all. Avoid using it; prefer a specific kind.         |

**How this maps to interfaces:** if an interface key exists in intent but not in
reality, that is one drift record with `drift_kind = missing_in_reality` and
`field` set to the sentinel `"_interface"`.

**How this maps to VLANs:** the top-level `vlans` block can drift on its own — a
VLAN present on one side but not the other, or a `name` mismatch. A VLAN missing
on one side is one drift record with `object = "vlan:<id>"`, `field` set to the
sentinel `"_vlan"`, and the appropriate `missing_in_*` kind. A `name` mismatch is
`object = "vlan:<id>"`, `field = "name"`, `drift_kind = value_mismatch`.

---

## 7. `severity` guidance

Severity is assigned by the diff engine based on field and kind. Starting rules —
refine as you learn:

| Situation                                            | Severity   |
|------------------------------------------------------|------------|
| `description` mismatch                               | `info`     |
| `enabled` mismatch (intent up, reality down)         | `critical` |
| `enabled` mismatch (intent down, reality up)         | `warning`  |
| `ip_addresses` mismatch                              | `warning`  |
| Interface missing in reality                         | `critical` |
| Interface missing in intent (undocumented)           | `warning`  |
| `mode` mismatch (e.g. access vs tagged)              | `warning`  |
| `untagged_vlan` mismatch                             | `warning`  |
| `tagged_vlans` mismatch                              | `warning`  |
| VLAN present in intent, missing in reality           | `warning`  |
| VLAN present in reality, missing in intent           | `info`     |
| VLAN `name` mismatch                                 | `info`     |

These are defaults. In a later version, severity becomes configurable per site/role.

---

## 8. Worked example (the full loop)

**Intent — from NetBox via `get_intent("core-sw-01")`:**

```python
{
    "device": "core-sw-01",
    "platform": "arista_eos",
    "collected_at": "2026-05-20T14:32:00Z",
    "interfaces": {
        "Ethernet1": {
            "description": "Uplink to dist-01",
            "enabled": True,
            "ip_addresses": [],
            "mode": "routed",
            "untagged_vlan": None,
            "tagged_vlans": [],
        },
        "Ethernet2": {
            "description": "Access port - users",
            "enabled": True,
            "ip_addresses": [],
            "mode": "access",
            "untagged_vlan": 10,
            "tagged_vlans": [],
        },
    },
    "vlans": {
        "10": {"name": "users"},
        "20": {"name": "voice"},
    },
}
```

**Reality — from the device via `get_reality(device)`:**

```python
{
    "device": "core-sw-01",
    "platform": "arista_eos",
    "collected_at": "2026-05-20T14:32:03Z",
    "interfaces": {
        "Ethernet1": {
            "description": "Uplink to dist-01",
            "enabled": True,
            "ip_addresses": [],
            "mode": "routed",
            "untagged_vlan": None,
            "tagged_vlans": [],
        },
        "Ethernet2": {
            "description": "Access port - users",
            "enabled": True,
            "ip_addresses": [],
            "mode": "access",
            "untagged_vlan": 99,                   # drift: should be VLAN 10
            "tagged_vlans": [],
        },
    },
    "vlans": {
        "10": {"name": "users"},
        "20": {"name": "Voice-VLAN"},              # drift: name differs
    },
}
```

**Diff engine output — a list of two drift records:**

```python
[
    {
        "device": "core-sw-01",
        "object": "interface:Ethernet2",
        "field": "untagged_vlan",
        "intent": 10,
        "reality": 99,
        "drift_kind": "value_mismatch",
        "severity": "warning",
        "detected_at": "2026-05-20T14:32:04Z",
    },
    {
        "device": "core-sw-01",
        "object": "vlan:20",
        "field": "name",
        "intent": "voice",
        "reality": "Voice-VLAN",
        "drift_kind": "value_mismatch",
        "severity": "info",
        "detected_at": "2026-05-20T14:32:04Z",
    },
]
```

---

## 9. Planned schema growth (do NOT build yet)

Listed here so both partners can see where it's going and avoid design choices that
would block these. **Only the fields in Section 2 are in scope right now.**

### v0.3 — routing state

Add top-level keys:

```python
"bgp_neighbors": {
    "10.0.0.1": {
        "remote_as": 65001,
        "state": "established",   # established | idle | active | connect | ...
        "enabled": True,
    },
},
"ospf": {
    "adjacencies": {
        "10.0.0.1": {"state": "full", "area": "0.0.0.0"},
    },
},
```

This also expands the drift-record `object` types beyond `interface` and `vlan` —
e.g. `bgp_neighbor:10.0.0.1`, `ospf_adjacency:10.0.0.1`.

### v1.0 — config-level drift

Add a top-level key:

```python
"running_config": "....."   # str, the device's full running config as text
```

Compared against a NetBox-rendered intended config. This introduces config-text
diffing and the semantic-equivalence problem — out of scope until v1.0.

---

## 10. Resolved questions

### v0.1 schema call (2026-05-21)

These were the open questions for v0.1. Settled jointly; the schema was frozen for
v0.1.

1. **Interface-missing convention.** When an interface exists in intent but not
   reality (or vice versa), the drift record's `field` is the sentinel
   `"_interface"`. **Confirmed** — implemented in `differ.py`.

2. **Case sensitivity of `device`.** Device names are **case-sensitive: exact,
   byte-for-byte match, no normalization.** `Core-SW-01` and `core-sw-01` are
   different devices. A casing mismatch surfaces as a loud, obvious failure
   ("no reality for X") rather than being silently folded. The name in NetBox, on
   the device, and in the schema object must be identical; `seed_netbox.py` is
   responsible for keeping them consistent. **Confirmed.**

3. **NetBox naming an interface differently from the device.** Per Section 3
   rule 1, each collector normalizes interface names to canonical full form before
   returning, so any vendor-specific naming is handled inside that vendor's
   collector — not in this schema. **Confirmed.**

4. **`collected_at` vs `detected_at`.** `collected_at` is set by the collector
   when the snapshot is taken; `detected_at` is set by the diff engine when the
   diff is computed. **Confirmed.**

5. **Where test fixtures live.** `tests/fixtures/`, as pairs of intent/reality
   dicts plus the expected drift list. Person B owns them; Person A reviews them.
   **Confirmed.**

### v0.2 schema call (2026-05-23)

The v0.2 VLAN additions. Settled jointly from the v0.2 proposal.

6. **`mode` is `access` / `tagged` / `routed`, always present, never `None`.** The
   three values are exhaustive; `routed` is a real mode, not the absence of one.
   See Rule 8. **Confirmed.**

7. **`untagged_vlan` is `int | None`.** `None` means "no untagged VLAN" (routed
   interfaces, trunks). Rule 4 was reworded to scope its "never `None`" to string
   and list fields only; nullable scalar fields use `None`. **Confirmed.**

8. **Native VLAN not modelled in v0.2.** A trunk's native VLAN is a real concept
   but an edge case; out of scope for v0.2 per the project's #1 risk (scope creep
   on the diff engine). `untagged_vlan` means strictly "the access VLAN of an
   access port." Recorded as a known future gap. **Confirmed.**

9. **`vlans` dict keys are strings; `tagged_vlans` stays `list[int]`.** See
   Rule 7. **Confirmed.**

10. **Routed interfaces carry empty VLAN fields, not missing keys.** A routed
    interface has `mode: "routed"`, `untagged_vlan: None`, `tagged_vlans: []`.
    Keys always present. **Confirmed.**

11. **New `vlan:<id>` drift-record object type.** Top-level VLAN drift uses
    `object = "vlan:<id>"`. See Section 6. **Confirmed.**

### v0.2 follow-up (2026-05-24)

Three operational questions raised after the v0.2 collector work (PR #23),
tracked in the GitHub issue and settled jointly.

12. **Default/reserved VLANs are in scope.** VLAN 1 (and any reserved IDs a
    device reports) are treated as normal VLANs — no filtering in collectors or
    the diff engine. Intent must document them. `seed_netbox.py` seeds VLAN 1.
    Reserved range 1002–1005 is not special-cased; Arista cEOS does not create
    it, and if a future platform does, it is documented in intent like any other
    VLAN. See Rule 9. **Confirmed.**

13. **`vlans` block is site-scoped on the intent side.** `netbox_client.py`
    scopes VLANs to the device's NetBox site. Device-scoped or global VLANs are
    a later concern. **Confirmed.**

14. **`Management0` is `mode: "routed"`.** It has an IP and no switchport, so
    `routed` is correct per Rule 8 — not an oversight. **Confirmed.**

---

## 11. Change log for this document

Keep a running log so both partners can see how the contract evolved.

| Date       | Change                                  | Approved by |
|------------|-----------------------------------------|-------------|
| 2026-05-20 | Initial v0.1 draft.                     | A + B       |
| 2026-05-21 | Section 10 open questions resolved; schema frozen for v0.1. | A + B |
| 2026-05-23 | v0.2 VLAN / layer-2 fields added: interface `mode`, `untagged_vlan`, `tagged_vlans`; top-level `vlans`. Rule 4 reworded; Rules 7 and 8 added; `vlan:<id>` drift object type. | A + B |
| 2026-05-24 | v0.2 follow-up: Rule 9 added (default/reserved VLANs in scope, no filtering); `vlans` site-scoping documented; Section 10 items 12–14. | A + B |

---

*When this document and the code disagree, this document wins — fix the code. When
both partners agree the document is wrong, change the document via merge request,
then fix the code.*