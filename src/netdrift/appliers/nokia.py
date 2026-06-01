"""appliers/nokia.py — Nokia SR Linux applier.

Executes restore_intent and raw_snippet remediations on Nokia SR Linux devices
via pygnmi's gNMIclient and the gNMI Set RPC with 'update' semantics.

Dry-run:  synthesize a diff from a live Get read-back vs intended values.
          SR Linux has no native dry-run — the diff is produced by reading
          current leaf state before the Set is executed.
Apply:    Set(update=[(path, val), ...])

WARNING: Never use Set with 'replace' semantics on a parent container — it
silently deletes siblings not listed in the update and will cause an outage.
Only leaf-level updates are safe.

Supported restore_intent fields:
    interface: description, enabled

Unsupported fields raise NotImplementedError — record a raw_snippet fix instead.
raw_snippet must use 'transport': 'gnmi' with [{path, val}, ...] update tuples.
"""

import contextlib
import json
import logging

from pygnmi.client import gNMIclient

from netdrift.appliers.base import (
    ApplyResult,
    RemediationBlockedError,
    check_blocked,
    register,
)

_log = logging.getLogger(__name__)

GNMI_PORT = 57400

# SR Linux out-of-band management interfaces.  The prefix check catches mgmt0
# and its logical sub-interface mgmt0.0 (used in some gNMI path contexts).
_MGMT_INTERFACES = frozenset({"mgmt0", "mgmt0.0"})


def _block_mgmt_interface(drift: dict) -> None:
    """Raise RemediationBlockedError if the drift targets a management interface."""
    obj = drift.get("object", "")
    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if iface in _MGMT_INTERFACES or iface.startswith("mgmt"):
            raise RemediationBlockedError(
                f"Interface '{iface}' is a management interface; "
                "remediation of management interfaces is prohibited."
            )


def _render_restore_intent(drift: dict) -> list[tuple]:
    """Map a restore_intent drift record to a list of gNMI (path, val) tuples.

    Returns the minimal Set update needed to make reality match intent.
    Raises NotImplementedError for object/field combinations not yet supported.
    """
    obj = drift.get("object", "")
    field = drift.get("field", "")
    intent = drift.get("intent")

    if obj.startswith("interface:"):
        iface = obj.split(":", 1)[1]
        if field == "description":
            return [(f"/interface[name={iface}]/description", intent or "")]
        if field == "enabled":
            val = "enable" if intent else "disable"
            return [(f"/interface[name={iface}]/admin-state", val)]
        raise NotImplementedError(
            f"restore_intent for interface field '{field}' is not yet supported "
            "on nokia_srlinux; record a raw_snippet fix instead."
        )

    raise NotImplementedError(
        f"restore_intent for object '{obj}', field '{field}' is not yet "
        "supported on nokia_srlinux."
    )


def _gnmi_leaf_val(response):
    """Extract the leaf value from a pygnmi Get response, or None."""
    notifications = response.get("notification", [])
    if not notifications:
        return None
    updates = notifications[0].get("update")
    if not updates:
        return None
    return updates[0].get("val")


@contextlib.contextmanager
def _gnmi_conn(device: dict):
    """Open a gNMI connection to the given SR Linux device."""
    with gNMIclient(
        target=(device["hostname"], GNMI_PORT),
        username=device["username"],
        password=device["password"],
        skip_verify=True,
    ) as gc:
        yield gc


def _synthesize_diff(gc, updates: list[tuple]) -> str:
    """Synthesize a human-readable diff of current vs intended state.

    Reads each leaf path via Get and compares to the intended value. Paths that
    already match the intent are omitted — only changes appear in the diff.
    Read failures are logged and treated as unknown current state.
    """
    lines = []
    for path, intended_val in updates:
        try:
            current_val = _gnmi_leaf_val(gc.get(path=[path], datatype="all"))
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Could not read current value at %r for dry-run diff: %s", path, exc
            )
            current_val = None

        if current_val != intended_val:
            lines.append(f"--- {path}: {current_val!r}")
            lines.append(f"+++ {path}: {intended_val!r}")

    return "\n".join(lines)


def _apply_via_gnmi(gc, updates: list[tuple], *, dry_run: bool) -> ApplyResult:
    """Execute (or dry-run) a gNMI Set and return an ApplyResult."""
    rendered = json.dumps([{"path": p, "val": v} for p, v in updates])
    dry_run_diff = _synthesize_diff(gc, updates)

    if dry_run:
        return ApplyResult(
            transport="gnmi",
            rendered_commands=rendered,
            dry_run_diff=dry_run_diff,
            applied=False,
        )

    gc.set(update=updates)
    return ApplyResult(
        transport="gnmi",
        rendered_commands=rendered,
        dry_run_diff=dry_run_diff,
        applied=True,
    )


@register("nokia_srlinux")
def apply(
    remediation: dict,
    drift: dict,
    device: dict,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """Apply (or dry-run) a known-issue remediation on a Nokia SR Linux device."""
    check_blocked(drift, device)
    _block_mgmt_interface(drift)

    kind = remediation.get("kind")

    if kind == "restore_intent":
        updates = _render_restore_intent(drift)
    elif kind == "raw_snippet":
        platform_entry = remediation.get("by_platform", {}).get("nokia_srlinux")
        if not platform_entry:
            raise ValueError(
                "raw_snippet remediation has no 'nokia_srlinux' entry in by_platform."
            )
        updates = [(u["path"], u["val"]) for u in platform_entry.get("updates", [])]
    elif kind is None:
        raise ValueError(
            "Remediation kind is null — this known_issue has no executable fix."
        )
    else:
        raise ValueError(f"Unknown remediation kind: {kind!r}")

    with _gnmi_conn(device) as gc:
        return _apply_via_gnmi(gc, updates, dry_run=dry_run)
