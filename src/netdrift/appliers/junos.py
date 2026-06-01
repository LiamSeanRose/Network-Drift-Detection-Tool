"""appliers/junos.py — Juniper JunOS applier.

Executes restore_intent and raw_snippet remediations on Juniper JunOS devices
via NAPALM's JunOS driver and the merge-candidate flow.

Dry-run:  load_merge_candidate → compare_config → discard_config
Apply:    load_merge_candidate → compare_config → commit_config

JunOS uses a native candidate configuration model with atomic commits, making
the merge-candidate flow the most reliable of the three supported platforms.
`commit confirmed` (auto-rollback) support is deferred to v4.5.

Supported restore_intent fields:
    interface: description, enabled, untagged_vlan, tagged_vlans
    vlan: name

Unsupported fields raise NotImplementedError — use a raw_snippet for those.
"""

from napalm import get_network_driver

from netdrift.appliers.base import (
    ApplyResult,
    RemediationBlockedError,
    check_blocked,
    register,
)

# JunOS out-of-band management interfaces.
# fxp0 — dedicated management port on MX/EX/SRX (Routing Engine OOB).
# em0  — management interface on some EX/QFX platforms.
# Prefix guards catch sub-interfaces (fxp0.0, em0.0) and future variants.
_MGMT_INTERFACES = frozenset({"fxp0", "em0", "fxp0.0", "em0.0"})


def _block_mgmt_interface(drift: dict) -> None:
    """Raise RemediationBlockedError if the drift targets a management interface."""
    obj = drift.get("object", "")
    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if iface in _MGMT_INTERFACES or iface.startswith("fxp") or iface.startswith("em0"):
            raise RemediationBlockedError(
                f"Interface '{iface}' is a management interface; "
                "remediation of management interfaces is prohibited."
            )


def _render_restore_intent(drift: dict) -> str:
    """Render a restore_intent drift record into a JunOS config stanza.

    JunOS uses set-style configuration. The stanzas here are in curly-brace
    (hierarchy) format, which is what NAPALM's load_merge_candidate expects
    when the candidate is a partial config fragment.
    """
    obj = drift.get("object", "")
    field = drift.get("field", "")
    intent = drift.get("intent")

    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if field == "description":
            desc = intent or ""
            return f'interfaces {{\n    {iface} {{\n        description "{desc}";\n    }}\n}}'
        if field == "enabled":
            if intent:
                # Remove the 'disable' statement — no equivalent of 'no shutdown'.
                return f"interfaces {{\n    {iface} {{\n        delete: disable;\n    }}\n}}"
            else:
                return f"interfaces {{\n    {iface} {{\n        disable;\n    }}\n}}"
        if field == "untagged_vlan":
            return (
                f"interfaces {{\n    {iface} {{\n        unit 0 {{\n"
                f"            family ethernet-switching {{\n"
                f"                vlan {{ members {intent}; }}\n"
                f"            }}\n        }}\n    }}\n}}"
            )
        if field == "tagged_vlans":
            members = " ".join(str(v) for v in (intent or []))
            return (
                f"interfaces {{\n    {iface} {{\n        unit 0 {{\n"
                f"            family ethernet-switching {{\n"
                f"                vlan {{ members [ {members} ]; }}\n"
                f"            }}\n        }}\n    }}\n}}"
            )
        raise NotImplementedError(
            f"restore_intent for interface field '{field}' is not yet supported "
            "on juniper_junos; record a raw_snippet fix instead."
        )

    if obj.startswith("vlan:"):
        vlan_id = obj.split(":", 1)[1]
        if field == "name":
            return f'vlans {{\n    {intent or ""} {{\n        vlan-id {vlan_id};\n    }}\n}}'
        raise NotImplementedError(
            f"restore_intent for vlan field '{field}' is not yet supported "
            "on juniper_junos; record a raw_snippet fix instead."
        )

    raise NotImplementedError(
        f"restore_intent for object '{obj}', field '{field}' is not yet "
        "supported on juniper_junos."
    )


def _napalm_conn(device: dict):
    """Return an open NAPALM JunOS connection for the given device dict."""
    driver = get_network_driver("junos")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        optional_args={"timeout": 30},
    )
    conn.open()
    return conn


def _apply_via_napalm(conn, config_text: str, *, dry_run: bool) -> ApplyResult:
    """Execute the NAPALM merge-candidate flow and return an ApplyResult."""
    conn.load_merge_candidate(config=config_text)
    diff = conn.compare_config()
    if dry_run:
        conn.discard_config()
        return ApplyResult(
            transport="cli",
            rendered_commands=config_text,
            dry_run_diff=diff,
            applied=False,
        )
    conn.commit_config()
    return ApplyResult(
        transport="cli",
        rendered_commands=config_text,
        dry_run_diff=diff,
        applied=True,
    )


@register("juniper_junos")
def apply(
    remediation: dict,
    drift: dict,
    device: dict,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """Apply (or dry-run) a known-issue remediation on a Juniper JunOS device."""
    check_blocked(drift, device)
    _block_mgmt_interface(drift)

    kind = remediation.get("kind")

    if kind == "restore_intent":
        config_text = _render_restore_intent(drift)
    elif kind == "raw_snippet":
        platform_entry = remediation.get("by_platform", {}).get("juniper_junos")
        if not platform_entry:
            raise ValueError(
                "raw_snippet remediation has no 'juniper_junos' entry in by_platform."
            )
        config_text = platform_entry["body"]
    elif kind is None:
        raise ValueError(
            "Remediation kind is null — this known_issue has no executable fix."
        )
    else:
        raise ValueError(f"Unknown remediation kind: {kind!r}")

    conn = _napalm_conn(device)
    try:
        return _apply_via_napalm(conn, config_text, dry_run=dry_run)
    finally:
        conn.close()
