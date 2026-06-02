# Community patterns

Each file here describes a recurring drift — what it means, why it happens, and
how to fix it. `driftcheck import-patterns patterns/` loads them into the
`known_issues` table, so a fresh install surfaces a stored cause and fix the
first time a drift appears, before you have built up any history of your own.

A pattern matches drift by **fingerprint** (`object_type|field|drift_kind`),
which the loader computes with the same function the differ uses. A pattern only
ever matches drift the differ actually produces.

## File format

```yaml
name: "Interface administratively down when intent says up"
object_type: interface          # interface | vlan | bgp_neighbor | ospf_adjacency
field: enabled                   # a schema field, or a sentinel (see below)
drift_kinds:                     # value_mismatch | missing_in_intent | missing_in_reality
  - value_mismatch
vendors: []                      # [] = all vendors; else canonical platform slugs
cause: "Why this drift happens."
fix: "What to do about it."
remediation:                     # optional; omit for diagnosis-only patterns
  kind: restore_intent
```

- **`field`** is a normalized schema field (`enabled`, `description`,
  `ip_addresses`, `mode`, `untagged_vlan`, `tagged_vlans`, `name`, `remote_as`,
  `session_state`, `area`, `interface`, `adjacency_state`), or a **sentinel**
  for a whole object present on only one side: `_interface`, `_vlan`,
  `_bgp_neighbor`, `_ospf_adjacency`.
- **`drift_kinds`** lists one or more kinds. A pattern with N kinds imports as N
  `known_issues` rows, one per fingerprint.
- **`vendors`** is advisory metadata. The fingerprint is vendor-agnostic, so a
  pattern matches on every platform regardless; the list documents which vendors
  a `restore_intent` fix is known to work on.

## Remediation

| `remediation`        | Meaning |
|----------------------|---------|
| omitted              | Diagnosis-only — a cause and fix in words, no executable payload. |
| `kind: restore_intent` | Eligible for auto-apply. The applier re-applies intent live. |
| `kind: raw_snippet`  | Suggest-only config, keyed `by_platform`. Never auto-applied. |

`restore_intent` only renders for fields an applier supports:
`interface` `description`/`enabled` on all four vendors, and
`interface` `untagged_vlan`/`tagged_vlans` plus `vlan` `name` on Arista, Cisco,
and Juniper. Operational-symptom fields (`session_state`, `adjacency_state`) are
never configurable and must stay diagnosis-only.

Imports always land with auto-apply **off**. An operator enables it per issue
after the confirm-threshold gate — the importer never turns it on.

## Adding a pattern

1. Drop a new `<slug>.yaml` in this directory.
2. Run `pytest tests/test_bundled_patterns.py` — it validates every file and
   rejects a fingerprint that collides with an existing pattern or that the
   differ cannot produce.
3. `driftcheck import-patterns patterns/` to load it. Re-running is safe; the
   import is idempotent.
