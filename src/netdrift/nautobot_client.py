"""
nautobot_client.py — Nautobot source-of-truth client (v0.3)

Drop-in alternative to netbox_client.py for users running Nautobot v1.x
instead of NetBox. Public interface is identical: get_intent(device_name)
returns the normalized schema (docs/schema.md Section 2) so the diff engine
and pipeline see no difference between the two sources.

Nautobot v1.x REST API is structurally similar to NetBox with two key
differences this module handles:
  - Config context per device is device.local_config_context
    (NetBox calls it local_context_data).
  - Env vars are NAUTOBOT_URL and NAUTOBOT_TOKEN (not NETBOX_*).

Select this client by setting SOURCE_OF_TRUTH=nautobot in the environment.
pipeline.py and cli.py read that var and call get_intent from here instead
of netbox_client.py.

NOTE: built and tested against mock objects — a live Nautobot instance has
not yet been used to validate field names. If a field access raises
AttributeError against a real Nautobot, adjust here, not in the schema.

Requires:
    NAUTOBOT_URL    e.g. http://localhost:8080
    NAUTOBOT_TOKEN  a Nautobot API token (read access is enough)
"""

import os
from datetime import datetime, timezone

import pynautobot


# Maps a Nautobot platform slug to the schema's canonical platform string
# (schema.md Section 4). Mirrors netbox_client.PLATFORM_MAP — add slugs here
# as Nautobot deployments use different naming conventions.
PLATFORM_MAP = {
    "arista-eos": "arista_eos",
    "eos": "arista_eos",
    "nokia-srlinux": "nokia_srlinux",
    "srlinux": "nokia_srlinux",
}


def _utc_now_iso():
    """Current time as ISO 8601 UTC with a Z suffix (schema.md Rule 2)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    """Build a pynautobot API handle from environment variables."""
    url = os.environ.get("NAUTOBOT_URL")
    token = os.environ.get("NAUTOBOT_TOKEN")
    if not url or not token:
        raise RuntimeError(
            "NAUTOBOT_URL and NAUTOBOT_TOKEN environment variables must be set."
        )
    return pynautobot.api(url, token=token)


def _normalize_platform(device):
    """Map a Nautobot device's platform slug to a canonical schema string.

    Raises ValueError if the device has no platform set or its slug is unknown.
    Mirrors the same guard in netbox_client.py — an unknown platform must fail
    clearly rather than dispatching to the wrong collector.
    """
    if device.platform is None:
        raise ValueError(
            f"Device '{device.name}' has no platform set in Nautobot. "
            f"Set its platform slug to one of: {', '.join(sorted(PLATFORM_MAP))}."
        )
    slug = device.platform.slug
    if slug not in PLATFORM_MAP:
        raise ValueError(
            f"Device '{device.name}' has unknown platform slug '{slug}'. "
            f"Known slugs: {', '.join(sorted(PLATFORM_MAP))}."
        )
    return PLATFORM_MAP[slug]


def _build_vlans(nb, site_id):
    """Build the schema's top-level `vlans` block from Nautobot.

    Scoped to the device's site. VLAN IDs are integers in Nautobot; schema
    Rule 7 requires string keys, so we str() each vid.
    """
    vlans = {}
    for nb_vlan in nb.ipam.vlans.filter(site_id=site_id):
        vlans[str(nb_vlan.vid)] = {"name": nb_vlan.name or ""}
    return vlans


def _interface_vlan_fields(nb_iface):
    """Map a Nautobot interface's VLAN config to schema v0.2 fields.

    Nautobot's VLAN mode field behaves the same as NetBox's: None means
    the interface has no L2 mode configured, which maps to "routed" per
    schema Rule 8.
    """
    nb_mode = nb_iface.mode

    if nb_mode is None:
        mode = "routed"
    else:
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


def _build_routing_from_context(device):
    """Extract routing intent (BGP + OSPF) from a Nautobot device's config context.

    Nautobot v1.x stores per-device config context in local_config_context
    (NetBox calls the same field local_context_data). The expected JSON shape
    is identical to NetBox's — it mirrors the device-state schema exactly so
    the differ compares like-for-like without translation.

    A device with no local_config_context, or one whose context omits these
    keys, yields empty containers — never None.

    Returns (bgp_neighbors_dict, ospf_dict).
    """
    context = device.local_config_context or {}
    bgp = context.get("bgp_neighbors", {}) or {}
    ospf_raw = context.get("ospf", {}) or {}
    ospf = {"adjacencies": ospf_raw.get("adjacencies", {}) or {}}
    return bgp, ospf


def get_intent(device_name):
    """Return the intended state of `device_name` from Nautobot, in the
    normalized schema (docs/schema.md Section 2).

    Raises ValueError if the device is not found in Nautobot.
    """
    nb = _connect()

    device = nb.dcim.devices.get(name=device_name)
    if device is None:
        raise ValueError(f"Device '{device_name}' not found in Nautobot.")

    interfaces = {}
    for nb_iface in nb.dcim.interfaces.filter(device_id=device.id):
        ip_addresses = sorted(
            ip.address
            for ip in nb.ipam.ip_addresses.filter(interface_id=nb_iface.id)
        )
        vlan_fields = _interface_vlan_fields(nb_iface)
        interfaces[nb_iface.name] = {
            "description": nb_iface.description or "",
            "enabled": bool(nb_iface.enabled),
            "ip_addresses": ip_addresses,
            "mode": vlan_fields["mode"],
            "untagged_vlan": vlan_fields["untagged_vlan"],
            "tagged_vlans": vlan_fields["tagged_vlans"],
        }

    bgp_neighbors, ospf = _build_routing_from_context(device)

    return {
        "device": device.name,
        "platform": _normalize_platform(device),
        "collected_at": _utc_now_iso(),
        "interfaces": interfaces,
        "vlans": _build_vlans(nb, device.site.id),
        "bgp_neighbors": bgp_neighbors,
        "ospf": ospf,
    }
