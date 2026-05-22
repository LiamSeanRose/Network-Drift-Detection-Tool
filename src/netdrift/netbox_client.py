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

        interfaces[nb_iface.name] = {
            # NetBox may return None for an unset description; schema Rule 4
            # requires "" instead.
            "description": nb_iface.description or "",
            "enabled": bool(nb_iface.enabled),
            "ip_addresses": ip_addresses,
        }

    return {
        "device": device.name,
        "platform": _normalize_platform(device),
        "collected_at": _utc_now_iso(),
        "interfaces": interfaces,
    }


if __name__ == "__main__":
    # Quick manual smoke test:  python -m netdrift.netbox_client core-sw-01
    import json
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python -m netdrift.netbox_client <device-name>")
    print(json.dumps(get_intent(sys.argv[1]), indent=2))