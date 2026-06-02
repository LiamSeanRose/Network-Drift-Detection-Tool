# `docs/schema-v4.75-tunnels-proposal.md` — Tunnel/Overlay Drift (Proposal)

> **Status:** Folded into `docs/schema.md` on branch `feat/v4.75-tunnel-drift`
> (2026-06-02) — **pending Matthew's `schema-sign-off`** before that PR merges.
> **Owner:** Liam (data-in). **Requires:** both partners' sign-off on the schema
> shape (`schema-sign-off`).
> **Gate:** v4.0 is functionally complete (all DoD items checked) but not yet
> tagged; Liam directed the implementation PR to proceed ahead of the tag,
> accepting minor rework risk, consistent with the collectors already merged.

---

## 1. Purpose of this document

v4.75 extends drift detection to overlay tunnels — GRE and VTI to start. It adds
one optional top-level block, `tunnels`, to the device-state object. This follows
the same path the `bgp_neighbors` and `ospf` blocks took in the v0.3 proposal:
agree the normalized shape here, ratify each decision, then fold it into
`docs/schema.md` Section 2 in a separate implementation PR.

The design council (2026-06-01) approved tunnel drift as "clean, in-identity,
fits the existing architecture exactly." Its one hard constraint: a device with
no tunnels must produce zero diff noise. The optional-key design below delivers
that by construction.

---

## 2. Proposed device-state addition

A new **optional** top-level key, `tunnels`. Unlike `bgp_neighbors` (always `{}`)
and `ospf` (always `{"adjacencies": {}}`), `tunnels` may be **absent entirely**.
A collector that does not implement tunnels, and an intent with none declared,
both omit the key. The differ reads it with `.get("tunnels", {})`, so absent on
both sides compares nothing — no diff noise on the 99% of devices with no
overlay.

```python
{
    # ... existing interfaces / vlans / bgp_neighbors / ospf / running_config ...

    # --- v4.75 addition (optional) ---
    "tunnels": {
        # key = canonical tunnel interface name (Rule 1 applies: full, not "Tu0")
        "Tunnel0": {
            "type": "gre",                  # "gre" | "vti"
            "source": "192.0.2.1",          # str — local endpoint IP, "" if unset
            "destination": "198.51.100.1",  # str — remote endpoint IP, "" if unset
            "enabled": True,                # bool — admin state (no shutdown)
            "tunnel_state": "up",           # str, lower-cased — operational, IS drift
        },
    },
}
```

### New field reference

| Field | Type | Meaning |
|-------|------|---------|
| `tunnels` | `dict` (optional) | Overlay tunnels keyed by canonical tunnel interface name. Key absent or `{}` = no tunnel intent and none collected. |
| `tunnels[].type` | `str` | Encapsulation: `gre` or `vti`. A discriminator that lets the block grow to other types later without reshaping. |
| `tunnels[].source` | `str` | Tunnel source (local endpoint) IP. `""` if unset — never `None` (Rule 4). |
| `tunnels[].destination` | `str` | Tunnel destination (remote endpoint) IP. `""` if unset — never `None`. |
| `tunnels[].enabled` | `bool` | Administrative state: configured `no shutdown` vs shut. Mirrors `interfaces[].enabled`. |
| `tunnels[].tunnel_state` | `str` | Operational line-protocol state, lower-cased (`up`, `down`). Operational but in scope for drift, exactly like `session_state` (Rule 10). |

---

## 3. Decisions and rationale

### Decision 1 — `tunnels` is an optional top-level key, not always-present

The council's binding constraint is "no diff noise for devices without tunnels."
Making the key optional (vs the always-present `{}`/`{"adjacencies": {}}` of BGP
and OSPF) means a collector with no tunnel support and an intent with no tunnels
declared are byte-identical: the key is simply not there. The differ's
`.get("tunnels", {})` already has precedent in `running_config` and
`software_version`, which skip when either side is empty.

### Decision 2 — keyed by tunnel interface name, scoped to GRE + VTI in v1

GRE and VTI are interface-based: each is a `Tunnel0` / `gr-0/0/0.0` interface with
a clear admin state and a single line-protocol up/down. That maps cleanly onto the
existing per-object model and onto the council's "tunnel-down-when-intent-up =
critical" signal. Policy-based IPsec SAs (no interface, keyed by peer) and MPLS
LDP sessions (keyed by LDP router-id) are a different shape and are **deferred**
(see Open Questions). Starting interface-based keeps v1 honest, the way v0.3
captured "intent-like BGP fields, not full telemetry."

### Decision 3 — `tunnel_state` is the one operational field, by analogy to `session_state`

A configured tunnel that is down is the single most useful tunnel-drift signal,
just as a down BGP session is for routing. So `tunnel_state` is compared as drift,
lower-cased by the collector for like-for-like comparison (Rule 10). Everything
else in the block (`type`, `source`, `destination`, `enabled`) is configuration.

### Decision 4 — `source` and `destination` are plain strings, `""` when unset

A wrong tunnel destination silently misroutes the overlay, so both endpoints are
first-class compared fields. They follow Rule 4: empty string for absent, never
`None`, always present as keys.

### Decision 5 — intent comes from NetBox `local_context_data`

NetBox has no native tunnel model, so tunnel intent is read from each device's
`local_context_data`, the same mechanism `netbox_client` already uses for BGP and
OSPF routing intent. No new NetBox object type is required.

### Decision 6 — no NAPALM getter exists; this is per-vendor collection

NAPALM has no tunnel getter. Each collector parses its own source (council notes):
Arista `show interfaces tunnel` via eAPI; Cisco IOS-XE `show interfaces tunnel`
via NAPALM CLI; Juniper via NETCONF/CLI; Nokia openconfig paths via pygnmi. This
is collector work (Liam), invisible to the schema contract — listed here only so
the schema-call accounts for the collection cost.

---

## 4. Proposed severity defaults

Mirrors the council's guidance ("tunnel-down when intent says up → critical;
config mismatch → warning") and the existing Section 7 conventions.

| Situation | Severity |
|-----------|----------|
| Tunnel missing in reality (intent has it) | `critical` |
| Tunnel missing in intent (undocumented) | `info` |
| `tunnel_state` mismatch (intent `up`, reality not `up`) | `critical` |
| `tunnel_state` mismatch (intent down, reality up) | `warning` |
| `destination` mismatch | `warning` |
| `source` mismatch | `warning` |
| `type` mismatch (e.g. gre vs vti) | `warning` |
| `enabled` mismatch (intent up, reality down) | `critical` |
| `enabled` mismatch (intent down, reality up) | `warning` |

Whole-tunnel missing/undocumented records use a sentinel field `_tunnel`, the same
convention the differ already uses for `_interface`, `_vlan`, `_bgp_neighbor`, and
`_ospf_adjacency`.

---

## 5. Open questions for the schema call

1. **Scope of v1 — GRE + VTI only.** Liam signed off: v1 is GRE + VTI only.
   Policy-based IPsec SAs and MPLS LDP sessions are deferred — they do not fit the
   interface-keyed shape and are more telemetry than config. Matthew to confirm.
2. **Key form — tunnel interface name.** Liam signed off: key by canonical tunnel
   interface name. If IPsec/MPLS are ever folded in, they get a `<type>:<peer>`
   key under their own convention rather than reshaping this one. Matthew to
   confirm.
3. **`type` vocabulary.** `gre` and `vti` for v1. Confirm we want the discriminator
   present now even though only two values exist, for forward-compat.
4. **`tunnel_state` vocabulary.** `up` / `down` only for v1. Sufficient for GRE/VTI?
5. **Pattern bundle.** The council wants ≥4 tunnel patterns in `patterns/` from day
   one. Those depend on the final field names ratified here.

---

## 6. Ownership (from the v4.75 ownership table)

| Work stream | Owner |
|---|---|
| `tunnels` schema block design (this doc) | Joint sign-off |
| `collectors/{arista,cisco,junos,nokia}.py` tunnel reality | Liam |
| `netbox_client.py` tunnel intent from `local_context_data` | Liam |
| `differ.py` tunnel diff rules | Matthew |
| `docs/schema.md` Section 2 fold-in | Joint sign-off |
| Initial tunnel patterns in `patterns/` | Liam (vendor) + Matthew (diff rules) |

The schema shape (Sections 2–4 above) is the only part needing joint sign-off
before work starts. Once ratified and v4.0 is tagged, the collector work is
Liam's lane and the differ rules are Matthew's, in the now-standard feature split.
