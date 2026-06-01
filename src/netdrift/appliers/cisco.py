"""appliers/cisco.py — Cisco IOS-XE applier.

Executes restore_intent and raw_snippet remediations on Cisco IOS-XE devices via
NAPALM's IOS driver and the merge-candidate flow.

Dry-run:  load_merge_candidate → compare_config → discard_config
Apply:    load_merge_candidate → compare_config → commit_config → verify

IOS-XE rollback (configure replace / archive) is weaker than EOS config sessions.
After commit_config, the applier reloads the same candidate and calls compare_config
a second time. A non-empty post-diff means IOS did not fully converge; the applier
logs a warning and attempts conn.rollback() before returning.

Supported restore_intent fields:
    interface: description, enabled, untagged_vlan, tagged_vlans
    vlan: name

Unsupported fields raise NotImplementedError — use a raw_snippet for those.
"""

import logging

from napalm import get_network_driver

from netdrift.appliers.base import (
    ApplyResult,
    RemediationBlockedError,
    check_blocked,
    register,
)

_log = logging.getLogger(__name__)

# Management interfaces that must never be auto-remediated.
# GigabitEthernet0 / GigabitEthernet0/0 — ISR/ASR dedicated OOB management port.
# GigabitEthernet0/0/0 — ISR 4000 series OOB management port.
# NOTE: On CSR 1000v and C8000v, GigabitEthernet0 is a *data* interface, not
# management. If your deployment uses these platforms, set `mgmt_interface` in
# devices.yml for that device and handle the override at the call site. This is
# a known limitation flagged for a future per-device override mechanism.
# Management* prefix — catches Management0, Management1, chassis variants
# (Management0/0, Management1/1) and sub-interfaces (Management0.0).
_MGMT_INTERFACES = frozenset({
    "GigabitEthernet0",
    "GigabitEthernet0/0",
    "GigabitEthernet0/0/0",
    "Management0",
    "Management1",
})


def _block_mgmt_interface(drift: dict) -> None:
    """Raise RemediationBlockedError if the drift targets a management interface."""
    obj = drift.get("object", "")
    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if iface in _MGMT_INTERFACES or iface.startswith("Management"):
            raise RemediationBlockedError(
                f"Interface '{iface}' is a management interface; "
                "remediation of management interfaces is prohibited."
            )


def _render_restore_intent(drift: dict) -> str:
    """Render a restore_intent drift record into an IOS-XE config stanza.

    Returns the minimal config text needed to make reality match intent.
    Raises NotImplementedError for object/field combinations not yet supported.
    """
    obj = drift.get("object", "")
    field = drift.get("field", "")
    intent = drift.get("intent")

    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if field == "description":
            return f"interface {iface}\n description {intent or ''}"
        if field == "enabled":
            cmd = "no shutdown" if intent else "shutdown"
            return f"interface {iface}\n {cmd}"
        if field == "untagged_vlan":
            return f"interface {iface}\n switchport access vlan {intent}"
        if field == "tagged_vlans":
            vlan_list = ",".join(str(v) for v in (intent or []))
            return f"interface {iface}\n switchport trunk allowed vlan {vlan_list}"
        raise NotImplementedError(
            f"restore_intent for interface field '{field}' is not yet supported "
            "on cisco_iosxe; record a raw_snippet fix instead."
        )

    if obj.startswith("vlan:"):
        vlan_id = obj.split(":", 1)[1]
        if field == "name":
            return f"vlan {vlan_id}\n name {intent or ''}"
        raise NotImplementedError(
            f"restore_intent for vlan field '{field}' is not yet supported "
            "on cisco_iosxe; record a raw_snippet fix instead."
        )

    raise NotImplementedError(
        f"restore_intent for object '{obj}', field '{field}' is not yet "
        "supported on cisco_iosxe."
    )


def _napalm_conn(device: dict):
    """Return an open NAPALM IOS connection for the given device dict."""
    driver = get_network_driver("ios")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        optional_args={"secret": device.get("secret", ""), "timeout": 30},
    )
    conn.open()
    return conn


def _apply_via_napalm(conn, config_text: str, *, dry_run: bool) -> ApplyResult:
    """Execute the NAPALM merge-candidate flow and return an ApplyResult.

    For live applies, a post-commit verification step re-loads the same candidate
    and checks whether IOS fully converged. A non-empty diff triggers a warning
    and a rollback attempt.
    """
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

    # Verify the commit converged. IOS-XE has no atomic config-session rollback;
    # a non-empty post-diff means the device partially applied the change.
    conn.load_merge_candidate(config=config_text)
    post_diff = conn.compare_config()
    conn.discard_config()
    if post_diff.strip():
        _log.warning(
            "Post-commit diff is non-empty on cisco_iosxe — changes may not have "
            "fully applied; attempting rollback. post_diff=%r",
            post_diff,
        )
        try:
            conn.rollback()
        except Exception as exc:  # noqa: BLE001
            _log.warning("rollback() failed after post-commit mismatch: %s", exc)

    return ApplyResult(
        transport="cli",
        rendered_commands=config_text,
        dry_run_diff=diff,
        applied=True,
    )


@register("cisco_iosxe")
def apply(
    remediation: dict,
    drift: dict,
    device: dict,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """Apply (or dry-run) a known-issue remediation on a Cisco IOS-XE device."""
    check_blocked(drift, device)
    _block_mgmt_interface(drift)

    kind = remediation.get("kind")

    if kind == "restore_intent":
        config_text = _render_restore_intent(drift)
    elif kind == "raw_snippet":
        platform_entry = remediation.get("by_platform", {}).get("cisco_iosxe")
        if not platform_entry:
            raise ValueError(
                "raw_snippet remediation has no 'cisco_iosxe' entry in by_platform."
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
