# Drift Record Contract

The drift record is the dict emitted by `differ.diff()` for each detected difference. It
is a second schema contract alongside `docs/schema.md`. **Changes to any field in this
shape require joint sign-off from both partners**, same as `docs/schema.md`.

## Why this needs a contract

The drift-record shape fans out silently across the codebase:

- `differ.diff()` emits it
- `storage/repository.py` `save_drifts()` maps its keys to `DriftEvent` columns
- `api/app.py` serializes it to JSON
- `fingerprint.py` reads `object`, `field`, `drift_kind`
- `sla.py` reads `severity`
- `auto_apply.py` reads `object`, `field`, `drift_kind`, `platform`
- `frontend/src/` renders `d.object`, `d.field`, `d.drift_kind`, `d.severity` directly

A key rename in `differ.py` breaks the dashboard with no type-system catch. This doc
freezes the shape so both partners know what they're signing off on before any change.

## The shape

```python
{
    # Which object drifted. Format: "<type>:<identifier>" or just "<type>" for
    # device-level fields.
    # Examples: "interface:Ethernet1", "vlan:10", "bgp_neighbor:10.0.0.2",
    #           "ospf_adjacency:2.2.2.2", "tunnel:Tunnel0", "config", "device"
    "object": str,

    # Which field on that object drifted.
    # Examples: "enabled", "ip_addresses", "description", "_interface",
    #           "running_config", "software_version", "session_state",
    #           "tunnel_state", "_tunnel"
    "field": str,

    # The class of difference.
    # Allowed values: "value_mismatch", "missing_in_reality", "missing_in_intent"
    "drift_kind": str,

    # How severe.
    # Allowed values: "critical", "warning", "info"
    "severity": str,

    # The intended value (from NetBox). Type varies by field.
    "intent": any,

    # The actual value (from the device). Type varies by field.
    "reality": any,

    # ISO 8601 UTC timestamp with Z suffix. Set by differ._now().
    "detected_at": str,
}
```

## Severity rules (summary — differ.py is authoritative)

| Field | Condition | Severity |
|-------|-----------|----------|
| `enabled` | intent True, reality False | `critical` |
| `enabled` | intent False, reality True | `warning` |
| `ip_addresses`, `mode`, `untagged_vlan`, `tagged_vlans` | any mismatch | `warning` |
| `description` | any mismatch | `info` |
| `_interface` | missing in reality | `critical` |
| `_interface` | missing in intent | `info` |
| `_vlan` | missing in reality | `warning` |
| `_vlan` | missing in intent | `info` |
| `session_state`, `adjacency_state` | any mismatch | `warning` |
| `running_config` | mismatch | `warning` |
| `software_version` | mismatch | `warning` |
| `_tunnel` | missing in reality | `critical` |
| `_tunnel` | missing in intent | `info` |
| `enabled` (tunnel) | intent up, reality down | `critical` |
| `enabled` (tunnel) | intent down, reality up | `warning` |
| `tunnel_state` | intent up, reality not up | `critical` |
| `tunnel_state` | intent down, reality up | `warning` |
| `type`, `source`, `destination` (tunnel) | any mismatch | `warning` |

## Joint sign-off requirement

Before any PR that:
- Adds a new key to the drift-record dict
- Renames an existing key
- Changes the allowed values of `drift_kind` or `severity`
- Changes the format of `object` (the `<type>:<identifier>` convention)

...both partners must comment-acknowledge on the issue with `schema-sign-off` label
before any branch opens. This mirrors the `docs/schema.md` rule exactly.
