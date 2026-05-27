# `docs/schema-v0.3-proposal.md` — v0.3 Schema Additions (Proposal)

> **Status: PROPOSAL — not yet agreed.** This document is Person A's proposed set
> of v0.3 schema additions, written up for the joint schema call. Nothing here is
> part of the contract until both partners review and approve it. On approval, these
> changes fold into `docs/schema.md` (Section 2, the rules, Section 6/7, the worked
> example, and Section 9) and this proposal file can be deleted or archived.
>
> **Scope:** v0.3 only — routing state (BGP neighbors, OSPF adjacencies), as
> outlined in `schema.md` Section 9. No config-text diffing (that is v1.0). Scope
> deliberately held narrow per the project's #1 risk (scope creep on the diff
> engine).

---

## 1. Purpose of this document

`schema.md` Section 9 sketches the v0.3 routing fields but leaves real design
questions open. This proposal resolves each one with a concrete decision and the
reasoning behind it, so the schema call is a *review-and-ratify* meeting rather
than a design-from-scratch one. Section 2 is the proposed final shape; Section 3
records the decisions and rationale; Section 4 lists what was learned from device
reconnaissance; Section 5 is the checklist for the call.

This proposal is grounded in actual device output. On 2026-05-26, Person A built
a minimal BGP + OSPF lab on the two cEOS nodes and captured what NAPALM and the
EOS CLI passthrough actually return. The field choices below follow from that
real data, not guesswork — see Section 4.

---

## 2. Proposed v0.3 device-state object

The v0.1 and v0.2 fields are unchanged. v0.3 adds **two new top-level keys**:
`bgp_neighbors` and `ospf`.

```python
{
    "device": "core-sw-01",
    "platform": "arista_eos",
    "collected_at": "2026-05-26T14:32:00Z",
    "interfaces": { ...unchanged from v0.2... },
    "vlans": { ...unchanged from v0.2... },

    # --- v0.3 addition: BGP neighbors ---
    "bgp_neighbors": {
        # keys are the neighbor IP as a STRING (see Decision 1)
        "10.0.0.2": {
            "remote_as": 65000,        # int
            "enabled": True,           # bool — admin state
            "description": "",         # str, "" when unset (Rule 4)
            "session_state": "established",  # see Decision 3
        },
    },

    # --- v0.3 addition: OSPF ---
    "ospf": {
        "adjacencies": {
            # keys are the OSPF neighbor router-id as a STRING
            "2.2.2.2": {
                "area": "0.0.0.0",     # str — dotted form, always
                "interface": "Ethernet1",  # str — canonical interface name
                "adjacency_state": "full",  # see Decision 4
            },
        },
    },
}
```

### New field reference

| Field                              | Type   | Meaning                                                          |
|------------------------------------|--------|------------------------------------------------------------------|
| `bgp_neighbors`                    | `dict` | Top-level. Keyed by neighbor IP string. `{}` if no BGP.          |
| `bgp_neighbors[].remote_as`        | `int`  | The AS the peer is expected to be in.                            |
| `bgp_neighbors[].enabled`          | `bool` | Admin state of the neighbor (configured up vs shut).             |
| `bgp_neighbors[].description`      | `str`  | Neighbor description. `""` when unset, never `None` (Rule 4).    |
| `bgp_neighbors[].session_state`    | `str`  | Operational session state, lower-cased. See Decision 3.          |
| `ospf`                             | `dict` | Top-level. `{"adjacencies": {}}` if no OSPF.                     |
| `ospf.adjacencies`                 | `dict` | Keyed by neighbor router-id string.                              |
| `ospf.adjacencies[].area`          | `str`  | OSPF area, dotted-decimal form (e.g. `"0.0.0.0"`).               |
| `ospf.adjacencies[].interface`    | `str`  | Canonical interface name the adjacency is on.                    |
| `ospf.adjacencies[].adjacency_state` | `str` | Operational adjacency state, lower-cased. See Decision 4.        |

---

## 3. Decisions and rationale

### Decision 1 — BGP neighbors keyed by IP string

Consistent with `vlans` (Decision 4 of the v0.2 proposal): dict keys are strings,
so a neighbor is `"10.0.0.2"`, not an `ipaddress` object or an int. Keying by IP
(not by a list index) means the differ can pair intent-neighbor to reality-neighbor
the same way it pairs interfaces by name. A neighbor present in one side and absent
in the other is a missing-object drift, handled like `_interface` (Rule 7).

Proposed drift object type: `bgp_neighbor:<ip>` (e.g. `bgp_neighbor:10.0.0.2`).

### Decision 2 — capture only intent-like BGP fields, not telemetry

NAPALM's `get_bgp_neighbors_detail()` returns ~30 fields per neighbor. Almost all
are live telemetry — `uptime`, `input_messages`, `flap_count`, `local_port`, etc.
These change every poll cycle. Diffing them would produce constant noise, not
drift, because drift means "intended config != actual config" and nobody *intends*
a message count.

The fields a human actually configures and documents — and therefore the only ones
worth drift detection — are: which neighbors exist, `remote_as`, `enabled`,
`description`. Those four (plus `session_state`, see Decision 3) are the schema.
Everything else NAPALM offers is deliberately dropped.

### Decision 3 — `session_state` is included, as the one operational field

`session_state` (BGP) is operational, not intent — but "my configured peer is
DOWN" is the single most useful routing-drift signal there is, so it earns its
place. Open question for the call: should a state mismatch be a real drift record,
or surfaced differently from a config mismatch? Proposed: treat it as drift with
`severity: warning` (see Section 6 below), lower than a `remote_as` mismatch.

Values are lower-cased: `established`, `idle`, `active`, `connect`, `opensent`,
`openconfirm`. The collector lower-cases whatever the device reports.

### Decision 4 — OSPF mirrors BGP, keyed by router-id

OSPF adjacencies keyed by neighbor router-id string. `adjacency_state` lower-cased:
`full`, `2-way`, `init`, `exstart`, `exchange`, `loading`, `down`. Proposed drift
object type: `ospf_adjacency:<router-id>`.

`area` is always the dotted-decimal form (`"0.0.0.0"`), never the bare-int form
(`0`) — EOS accepts both as input but the collector normalizes to dotted, so
intent and reality always compare like-for-like.

### Decision 5 — Nokia SR Linux routing is best-effort, like its VLANs

Per the existing `nokia.py` precedent (SR Linux VLAN mapping is documented as
best-effort), v0.3 SR Linux routing collection will be best-effort and documented
in the collector docstring. The schema shape is vendor-neutral; per-vendor mapping
gaps are a collector concern, not a schema concern.

---

## 4. What device reconnaissance showed (2026-05-26)

Captured from the live cEOS lab to ground the field choices above.

**BGP — `napalm get_bgp_neighbors()` returns a clean structured dict:**
nested `global -> peers -> <ip> -> {local_as, remote_as, remote_id, is_up,
is_enabled, description, uptime, address_family{...}}`. This maps directly onto
the proposed schema — `remote_as`, `is_enabled`, `description` are all present.
`get_bgp_neighbors_detail()` returns far more, but as established in Decision 2 it
is mostly telemetry.

**Decision for the collector:** build the v0.3 BGP collector on
`get_bgp_neighbors()` (the summary getter), not the detail getter.

**OSPF — NAPALM has NO core getter.** There is no `get_ospf_neighbors()` in
NAPALM's getter set. OSPF state has to come from the EOS eAPI / CLI passthrough
(`conn.cli(["show ip ospf neighbor"])`) and be parsed by the collector itself.

**Decision for the collector:** the v0.3 OSPF collector parses
`show ip ospf neighbor` output. This is a known extra cost for OSPF that BGP does
not have, and is flagged here so it is not a surprise during implementation. The
SR Linux side uses gNMI OSPF paths (pygnmi), consistent with the existing Nokia
VLAN collector.

---

## 5. Open questions for the schema call

1. **`session_state` / `adjacency_state` — drift or not?** Decision 3 proposes
   treating an operational state mismatch as drift at `warning` severity. Agree,
   or handle session/adjacency state as a separate "health" signal outside the
   drift record?
2. **BGP address-family scope.** The proposal collapses to a single neighbor
   entry. Do we need per-AF state (ipv4 vs ipv6 vs evpn) in v0.3, or is that
   deferred? Proposed: defer — v0.3 lab is ipv4-only.
3. **OSPF — adjacencies only, or also interface/area config?** The proposal
   captures adjacencies (neighbor relationships). Should v0.3 also capture
   per-interface OSPF config (cost, area membership, passive)? Proposed: defer
   interface-level OSPF; adjacencies first.
4. **How is routing intent stored in NetBox?** NetBox has no native BGP/OSPF
   model. Options: a plugin (e.g. netbox-bgp), config contexts, or custom fields.
   This is a `netbox_client.py` / `seed_netbox.py` question that blocks the
   intent side of v0.3 and needs its own decision.
5. **Drift object types.** Confirm `bgp_neighbor:<ip>` and
   `ospf_adjacency:<router-id>` as the new `object` values for the differ.

---

## 6. Proposed severity defaults

Extends `schema.md` Section 7. Defaults only; configurable later.

| Drift                                                 | Severity   |
|-------------------------------------------------------|------------|
| BGP neighbor present in intent, missing in reality    | `warning`  |
| BGP neighbor present in reality, missing in intent    | `info`     |
| BGP `remote_as` mismatch                              | `warning`  |
| BGP `enabled` mismatch                                | `warning`  |
| BGP `session_state` mismatch                          | `warning`  |
| BGP `description` mismatch                            | `info`     |
| OSPF adjacency present in intent, missing in reality  | `warning`  |
| OSPF adjacency present in reality, missing in intent  | `info`     |
| OSPF `area` mismatch                                  | `warning`  |
| OSPF `adjacency_state` mismatch                       | `warning`  |

---

*This is a proposal. When approved by both partners, it folds into `schema.md` and
the change log there gets a new row.*