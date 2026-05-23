"""
netbox_client.py — Ticket 4, v0.1

The "intent" side of the drift tool. Reads a device's documented state from
NetBox and returns it in the normalized schema (see docs/schema.md Section 2).

The diff engine consumes this shape directly, so it must match the schema
exactly. NetBox's own data model does NOT match the schema 1:1 — this module
is the translation layer that maps NetBox -> normalized schema.

Public function:
    get_intent(device_name: str) -> dict

Requires two environment variables:
    NETBOX_URL    e.g. http://localhost:8000
    NETBOX_TOKEN  a NetBox API token (read access is enough)
"""

import os
from datetime import datetime, timezone

import pynetbox


# --- Platform mapping --------------------------------------------------------
# schema.md Section 4 defines a fixed set of canonical platform strings.
# NetBox stores platform as its own object with a slug that may differ (or be
# unset). We map NetBox's slug to the canonical schema value here. For v0.1
# there is one vendor; this table grows as vendors are added.

PLATFORM_MAP = {
    "arista-eos": "arista_eos",
    "eos": "arista_eos",
}

# v0.1 is single-vendor. Until netbox_client learns to read NetBox's platform
# field reliably, we assume Arista. Revisit when a second vendor is added.
DEFAULT_PLATFORM = "arista_eos"


def _utc_now_iso():
    """Current time as ISO 8601 UTC with a Z suffix (schema.md Rule 2)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    """Build a pynetbox API handle from environment variables."""
    url = os.environ.get("NETBOX_URL")
    token = os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        raise RuntimeError(
            "NETBOX_URL and NETBOX_TOKEN environment variables must be set."
        )
    return pynetbox.api(url, token=token)


def _normalize_platform(device):
    """
    Map a NetBox device's platform to a canonical schema platform string.
    Falls back to DEFAULT_PLATFORM if NetBox has no platform set.
    """
    if device.platform is None:
        return DEFAULT_PLATFORM
    slug = device.platform.slug
    return PLATFORM_MAP.get(slug, DEFAULT_PLATFORM)

def _build_vlans(nb, site_id):
    """Build the schema's top-level `vlans` block from NetBox.

    Scoped to the device's site — VLANs in this lab belong to a site
    (see seed_netbox.py). NetBox VLAN IDs are integers; schema Rule 7
    requires the `vlans` dict to be keyed by VLAN ID as a STRING, so
    we str() each vid.
    """
    vlans = {}
    for nb_vlan in nb.ipam.vlans.filter(site_id=site_id):
        vlans[str(nb_vlan.vid)] = {"name": nb_vlan.name or ""}
    return vlans

def _interface_vlan_fields(nb_iface):
    """Map a NetBox interface's VLAN config to the schema's v0.2 fields.

    Returns a dict with mode / untagged_vlan / tagged_vlans.

    NetBox stores VLANs as objects and has no `routed` mode — a routed
    interface simply has `mode` unset. The schema requires an explicit
    `routed`, so an empty NetBox mode maps to "routed" here (schema Rule 8).
    pynetbox returns VLANs as objects; the schema wants integer VLAN IDs,
    so we pull `.vid` off each.
    """
    nb_mode = nb_iface.mode  # NetBox: "Access" / "Tagged" / None

    if nb_mode is None:
        mode = "routed"
    else:
        # nb_iface.mode is a pynetbox value; str() then lowercase it.
        mode = str(nb_mode).lower()

    if nb_iface.untagged_vlan is not None:
        untagged_vlan = nb_iface.untagged_vlan.vid
    else:
        untagged_vlan = None

    tagged_vlans = sorted(v.vid for v in nb_iface.tagged_vlans)

    return {
        "mode": mode,
        "untagged_vlan": untagged_vlan,
        "tagged_vlans": tagged_vlans,
    }

def get_intent(device_name):
    """
    Return the intended state of `device_name` from NetBox, in the normalized
    schema (docs/schema.md Section 2).

    Raises ValueError if the device is not found in NetBox.
    """
    nb = _connect()

    device = nb.dcim.devices.get(name=device_name)
    if device is None:
        raise ValueError(f"Device '{device_name}' not found in NetBox.")

    interfaces = {}
    # .filter returns every interface on this device.
    for nb_iface in nb.dcim.interfaces.filter(device_id=device.id):
        # IPs assigned to this interface, as a sorted list of CIDR strings.
        ip_addresses = sorted(
            ip.address
            for ip in nb.ipam.ip_addresses.filter(interface_id=nb_iface.id)
        )

        vlan_fields = _interface_vlan_fields(nb_iface)
        interfaces[nb_iface.name] = {
            # NetBox may return None for an unset description; schema Rule 4
            # requires "" instead.
            "description": nb_iface.description or "",
            "enabled": bool(nb_iface.enabled),
            "ip_addresses": ip_addresses,
            "mode": vlan_fields["mode"],
            "untagged_vlan": vlan_fields["untagged_vlan"],
            "tagged_vlans": vlan_fields["tagged_vlans"],
        }

    return {
        "device": device.name,
        "platform": _normalize_platform(device),
        "collected_at": _utc_now_iso(),
        "interfaces": interfaces,
        "vlans": _build_vlans(nb, device.site.id),
    }

if __name__ == "__main__":
    # Quick manual smoke test:  python -m netdrift.netbox_client core-sw-01
    import json
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python -m netdrift.netbox_client <device-name>")
    print(json.dumps(get_intent(sys.argv[1]), indent=2))