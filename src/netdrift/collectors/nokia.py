"""collectors/nokia.py — Nokia SR Linux collector.

The "reality" side for Nokia SR Linux devices. Connects to an SR Linux node
and returns its real state in the normalized schema (docs/schema.md Section 2).

Returns the same shape as collectors/arista.py — the diff engine consumes the
output identically and does not care which vendor produced it.

WHY pygnmi AND NOT NAPALM:
    SR Linux is gNMI-native. NAPALM's community srl driver was tried first
    but proved unusable: get_vlans() is an unimplemented stub, its raw gNMI
    path crashes inside the driver's own response parser, and it cannot even
    be imported alongside pygnmi (both ship their own compiled gNMI protobuf
    definitions and protobuf's global registry rejects the duplicate). This
    collector therefore talks gNMI directly via pygnmi.

SR LINUX VLAN MODEL — BEST-EFFORT MAPPING (known approximation):
    SR Linux has no Arista-style access/trunk port modes. It uses
    subinterfaces with VLAN encapsulation bound into a mac-vrf
    network-instance. Mapped onto the schema best-effort:
      - bridged subinterface with single-tagged encap -> mode "access",
        untagged_vlan = that VLAN id.
      - interface with no bridged subinterface -> mode "routed".
      - the top-level vlans block is derived; a VLAN's name is the mac-vrf
        instance the subinterface is bound to (SR Linux has no VLAN name).
    KNOWN GAPS (out of scope for v0.2): trunk/tagged mode not produced
    (tagged_vlans always []); only the first single-tagged subinterface is
    used; mac-vrf-name-as-VLAN-name is an approximation.

Public function:
    get_reality(device: dict) -> dict
"""

from datetime import datetime, timezone

from pygnmi.client import gNMIclient

GNMI_PORT = 57400


def _gnmi_first_val(response):
    """Return the first update's value from a pygnmi get() response, or None."""
    notifications = response.get("notification", [])
    if not notifications:
        return None
    updates = notifications[0].get("update")
    if not updates:
        return None
    return updates[0].get("val")


def _build_ip_list(subif):
    """Sorted list of CIDR IPv4 strings for one subinterface dict."""
    ipv4 = subif.get("ipv4", {})
    addresses = ipv4.get("address", [])
    return sorted(a["ip-prefix"] for a in addresses if "ip-prefix" in a)


def _vlan_id_from_subinterface(subif):
    """Return the single-tagged VLAN id from a subinterface dict, or None."""
    vlan = subif.get("srl_nokia-interfaces-vlans:vlan", {})
    single = vlan.get("encap", {}).get("single-tagged", {})
    return single.get("vlan-id")


def _is_bridged(subif):
    """True if a subinterface is type 'bridged' (an L2 subinterface)."""
    return str(subif.get("type", "")).endswith("bridged")


def _build_macvrf_map(gc):
    """Build {subinterface_name: mac-vrf_instance_name} from network-instances."""
    subif_to_macvrf = {}
    val = _gnmi_first_val(gc.get(path=["/network-instance"], datatype="all"))
    instances = val.get("srl_nokia-network-instance:network-instance", []) if val else []
    for ni in instances:
        if not str(ni.get("type", "")).endswith("mac-vrf"):
            continue
        ni_name = ni.get("name", "")
        for bound in ni.get("interface", []):
            subif_name = bound.get("name", "")
            if subif_name:
                subif_to_macvrf[subif_name] = ni_name
    return subif_to_macvrf


def _parse_interface(iface):
    """Map one gNMI interface dict to schema fields. Returns (dict, vlan_id)."""
    description = iface.get("description", "")
    enabled = iface.get("admin-state", "") == "enable"

    ip_addresses = []
    mode = "routed"
    untagged_vlan = None
    vlan_id_seen = None

    for subif in iface.get("subinterface", []):
        ip_addresses.extend(_build_ip_list(subif))
        if vlan_id_seen is None and _is_bridged(subif):
            vlan_id = _vlan_id_from_subinterface(subif)
            if vlan_id is not None:
                mode = "access"
                untagged_vlan = vlan_id
                vlan_id_seen = vlan_id

    iface_dict = {
        "description": description,
        "enabled": enabled,
        "ip_addresses": sorted(ip_addresses),
        "mode": mode,
        "untagged_vlan": untagged_vlan,
        "tagged_vlans": [],
    }
    return iface_dict, vlan_id_seen


def get_reality(device):
    """Return the real state of an SR Linux device in the normalized schema."""
    host = (device["hostname"], GNMI_PORT)

    with gNMIclient(
        target=host,
        username=device["username"],
        password=device["password"],
        skip_verify=True,
    ) as gc:
        iface_val = _gnmi_first_val(gc.get(path=["/interface"], datatype="all"))
        iface_list = iface_val.get("srl_nokia-interfaces:interface", []) if iface_val else []
        subif_to_macvrf = _build_macvrf_map(gc)

    interfaces = {}
    vlans = {}
    for iface in iface_list:
        name = iface.get("name")
        if not name:
            continue
        iface_dict, vlan_id = _parse_interface(iface)
        interfaces[name] = iface_dict

        if vlan_id is not None:
            vlan_name = ""
            for subif in iface.get("subinterface", []):
                if _is_bridged(subif):
                    vlan_name = subif_to_macvrf.get(subif.get("name", ""), "")
                    break
            vlans[str(vlan_id)] = {"name": vlan_name}

    return {
        "device": device["name"],
        "platform": "nokia_srlinux",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interfaces": interfaces,
        "vlans": vlans,
    }