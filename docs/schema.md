# `docs/schema.md` — The Normalized Schema

> **This file is the contract between the collector side (Person A) and the diff
> engine side (Person B).** Both `get_intent()` (NetBox) and `get_reality()`
> (device) must return data in *exactly* the shape defined here. The diff engine
> assumes the data already matches this shape and does not care where it came from.
>
> **Rule: any change to this file is a merge request that BOTH partners review and
> approve.** Do not change the schema unilaterally.
>
> **Status:** Draft for v0.1. Agree on it together before writing collector or
> differ code.

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

## 2. The device-state object (v0.1)

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
        },
        "Ethernet2": {
            "description": "",
            "enabled": False,
            "ip_addresses": [],
        },
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

3. **Lists are sorted before returning.** `ip_addresses` is sorted ascending. Any
   list-valued field added later (e.g. `tagged_vlans`) is also sorted. This means
   `["10.1.1.5/24", "10.1.1.6/24"]` and `["10.1.1.6/24", "10.1.1.5/24"]` can never
   be reported as drift.

4. **Absent values are explicit, never missing and never `None`.**
   - Unset description → `""` (empty string)
   - No IP addresses → `[]` (empty list)
   - Every interface dict has all three keys, always.

5. **`enabled` is administrative state.** It answers "is this interface configured
   as `no shutdown`?" — not "is the cable plugged in?" Link/carrier state is a
   separate field added in a later version.

6. **`device` must match across all three places** — the name in NetBox, the name
   on the physical/virtual device, and the value in this object. If they differ,
   intent and reality cannot be paired up. Keep them consistent.

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
| `object`      | `str`  | `"<type>:<identifier>"`. v0.1 only type is `interface`. e.g. `interface:Ethernet1`. |
| `field`       | `str`  | Which field drifted: `description`, `enabled`, or `ip_addresses`.|
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
| `missing_in_reality` | Intent has it, the device does not. (e.g. NetBox lists an interface the device doesn't have, or an IP the device isn't carrying.) |
| `missing_in_intent`  | The device has it, NetBox does not. Undocumented configuration.    |
| `extra`              | Generic catch-all. Avoid using it; prefer a specific kind.         |

**How this maps to interfaces:** if an interface key exists in intent but not in
reality, that is one drift record with `drift_kind = missing_in_reality` and
`field` set to a sentinel like `"_interface"` (Person B to confirm the exact
convention when building `differ.py` — document the decision back here).

---

## 7. `severity` guidance (v0.1)

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

These are defaults. In a later version, severity becomes configurable per site/role.

---

## 8. Worked example (the full v0.1 loop)

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
        },
        "Ethernet2": {
            "description": "Mgmt",
            "enabled": True,
            "ip_addresses": ["10.1.1.5/24"],
        },
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
            "enabled": False,                      # drift: should be enabled
            "ip_addresses": [],
        },
        "Ethernet2": {
            "description": "MGMT - DO NOT TOUCH",  # drift: description differs
            "enabled": True,
            "ip_addresses": ["10.1.1.5/24"],
        },
    },
}
```

**Diff engine output — a list of two drift records:**

```python
[
    {
        "device": "core-sw-01",
        "object": "interface:Ethernet1",
        "field": "enabled",
        "intent": True,
        "reality": False,
        "drift_kind": "value_mismatch",
        "severity": "critical",
        "detected_at": "2026-05-20T14:32:04Z",
    },
    {
        "device": "core-sw-01",
        "object": "interface:Ethernet2",
        "field": "description",
        "intent": "Mgmt",
        "reality": "MGMT - DO NOT TOUCH",
        "drift_kind": "value_mismatch",
        "severity": "info",
        "detected_at": "2026-05-20T14:32:04Z",
    },
]
```

---

## 9. Planned schema growth (do NOT build yet)

Listed here so both partners can see where it's going and avoid design choices that
would block these. **Only the v0.1 fields in Section 2 are in scope right now.**

### v0.2 — VLANs / layer 2

Add to each interface:

```python
"mode": "tagged",              # "access" | "tagged" | "routed" | None
"untagged_vlan": 99,           # int | None
"tagged_vlans": [10, 20, 30],  # list[int], sorted
```

Add a top-level key:

```python
"vlans": {
    10: {"name": "users"},
    20: {"name": "voice"},
}
```

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

This also expands the drift-record `object` types beyond `interface` — e.g.
`bgp_neighbor:10.0.0.1`, `ospf_adjacency:10.0.0.1`.

### v1.0 — config-level drift

Add a top-level key:

```python
"running_config": "....."   # str, the device's full running config as text
```

Compared against a NetBox-rendered intended config. This introduces config-text
diffing and the semantic-equivalence problem — out of scope until v1.0.

---

## 10. Open questions to resolve on the schema call

Decide these together and record the answers in this file before coding starts:

1. **Interface-missing convention.** When an interface exists in intent but not
   reality (or vice versa), what exactly goes in the drift record's `field`? Proposed:
   `"_interface"`. Confirm and document.
2. **Case sensitivity of `device`.** Are device names case-sensitive? Proposed: yes,
   exact match, no normalization. Confirm.
3. **What if NetBox has an interface the device's platform names differently?** For
   v0.1 with one vendor this can't happen; note it as a known future concern.
4. **`collected_at` vs `detected_at`.** Confirmed: `collected_at` is when the
   snapshot was taken (set by the collector); `detected_at` is when the diff was
   computed (set by the diff engine). They will differ by seconds.
5. **Where do hand-written test fixtures live?** Proposed: `tests/fixtures/`, as pairs
   of intent/reality dicts plus the expected drift list. Person B owns these but
   Person A should review them so collectors target the right shape.

---

## 11. Change log for this document

Keep a running log so both partners can see how the contract evolved.

| Date       | Change                                  | Approved by |
|------------|-----------------------------------------|-------------|
| (fill in)  | Initial v0.1 draft.                     | A + B       |

---

*When this document and the code disagree, this document wins — fix the code. When
both partners agree the document is wrong, change the document via merge request,
then fix the code.*