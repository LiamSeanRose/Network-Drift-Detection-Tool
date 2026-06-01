"""appliers/arista.py — Arista EOS applier.

Executes restore_intent and raw_snippet remediations on Arista EOS devices via
NAPALM's EOS driver and the merge-candidate flow.

Dry-run:  load_merge_candidate → compare_config → discard_config
Apply:    load_merge_candidate → compare_config → commit_config

EOS config sessions make discard a clean, side-effect-free rollback — the
strongest rollback story of the three supported NOSes.

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

# Management interfaces that must never be auto-remediated — touching these
# risks losing the connection the tool itself uses.  The prefix check catches
# Management0, Management1, chassis variants (Management0/0, Management1/1),
# and sub-interfaces (Management0.0).
def _block_mgmt_interface(drift: dict) -> None:
    """Raise RemediationBlockedError if the drift targets a management interface."""
    obj = drift.get("object", "")
    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if iface.startswith("Management"):
            raise RemediationBlockedError(
                f"Interface '{iface}' is a management interface; "
                "remediation of management interfaces is prohibited."
            )


def _render_restore_intent(drift: dict) -> str:
    """Render a restore_intent drift record into an EOS config stanza.

    Returns the minimal config text needed to make reality match intent.
    Raises NotImplementedError for object/field combinations not yet supported.
    """
    obj = drift.get("object", "")
    field = drift.get("field", "")
    intent = drift.get("intent")

    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if field == "description":
            return f"interface {iface}\n   description {intent or ''}"
        if field == "enabled":
            cmd = "no shutdown" if intent else "shutdown"
            return f"interface {iface}\n   {cmd}"
        if field == "untagged_vlan":
            return f"interface {iface}\n   switchport access vlan {intent}"
        if field == "tagged_vlans":
            vlan_list = ",".join(str(v) for v in (intent or []))
            return f"interface {iface}\n   switchport trunk allowed vlan {vlan_list}"
        raise NotImplementedError(
            f"restore_intent for interface field '{field}' is not yet supported "
            "on arista_eos; record a raw_snippet fix instead."
        )

    if obj.startswith("vlan:"):
        vlan_id = obj.split(":", 1)[1]
        if field == "name":
            return f"vlan {vlan_id}\n   name {intent or ''}"
        raise NotImplementedError(
            f"restore_intent for vlan field '{field}' is not yet supported "
            "on arista_eos; record a raw_snippet fix instead."
        )

    raise NotImplementedError(
        f"restore_intent for object '{obj}', field '{field}' is not yet "
        "supported on arista_eos."
    )


def _napalm_conn(device: dict):
    """Return an open NAPALM EOS connection for the given device dict."""
    driver = get_network_driver("eos")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        optional_args={"enforce_verification": False, "timeout": 30},
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


@register("arista_eos")
def apply(
    remediation: dict,
    drift: dict,
    device: dict,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """Apply (or dry-run) a known-issue remediation on an Arista EOS device."""
    check_blocked(drift, device)
    _block_mgmt_interface(drift)

    kind = remediation.get("kind")

    if kind == "restore_intent":
        config_text = _render_restore_intent(drift)
    elif kind == "raw_snippet":
        platform_entry = remediation.get("by_platform", {}).get("arista_eos")
        if not platform_entry:
            raise ValueError(
                "raw_snippet remediation has no 'arista_eos' entry in by_platform."
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
