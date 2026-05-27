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

SR LINUX ROUTING — BEST-EFFORT MAPPING (v0.3, Decision 5):
    Routing state is read from /network-instance[name=default]/protocols/bgp
    and .../protocols/ospf. Only the default VRF is read — schema does not
    model VRFs. SR Linux reports session-state and adjacency-state already in
    schema-compliant lower-case form, so no transformation is needed beyond
    key-picking. Note: SR Linux's native YANG calls the protocol `ospf`, not
    `ospfv2` (OpenConfig naming) — using the wrong leaf raises a gNMI invalid
    path error rather than an empty result, so both builders catch
    gNMIException and treat it as "no data" to keep one bad path from
    breaking the whole collection run.
    KNOWN GAPS: only the default network-instance is read; multi-AF BGP
    collapses to a single neighbor entry (consistent with the schema); OSPFv3
    not handled (schema is ipv4-only in v0.3); if no routing is configured,
    both top-level keys return empty containers rather than raising.

Public function:
    get_reality(device: dict) -> dict
"""

from datetime import datetime, timezone

from pygnmi.client import gNMIclient, gNMIException

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


def _safe_gnmi_get(gc, path):
    """Wrap a gnmi get() that may raise on an invalid path.

    pygnmi raises gNMIException for paths the device's YANG model doesn't
    expose (e.g. asking for OSPF on a device with no OSPF schema loaded). For
    the routing builders we treat that as "no data" — one missing protocol
    must not break the whole collection run. Returns the unwrapped val, or
    None on either empty response or invalid path.
    """
    try:
        response = gc.get(path=[path], datatype="all")
    except gNMIException:
        return None
    return _gnmi_first_val(response)


def _build_bgp_neighbors(gc):
    """Read BGP neighbors from the default network-instance, return schema form.

    Path: /network-instance[name=default]/protocols/bgp/neighbor

    SR Linux reports session-state already lower-cased ("established", "idle",
    etc.), so it maps onto the schema's session_state field as-is. Admin state
    "enable" is True, anything else (including absent) is False.

    Empty container ({}) is returned when BGP is not configured.
    """
    neighbors = {}
    val = _safe_gnmi_get(
        gc, "/network-instance[name=default]/protocols/bgp/neighbor",
    )
    if not val:
        return neighbors

    # SR Linux returns either a list of neighbor dicts under the namespaced key,
    # or — when fetched at exactly this path — sometimes the list directly.
    raw = val.get("srl_nokia-bgp:neighbor", val) if isinstance(val, dict) else val
    if not isinstance(raw, list):
        return neighbors

    for entry in raw:
        ip = entry.get("peer-address")
        if not ip:
            continue
        neighbors[ip] = {
            "remote_as": entry.get("peer-as"),
            "enabled": entry.get("admin-state", "") == "enable",
            "description": entry.get("description", "") or "",
            "session_state": str(entry.get("session-state", "")).lower(),
        }
    return neighbors


def _build_ospf_adjacencies(gc):
    """Read OSPF neighbors from the default network-instance, return schema form.

    Path: /network-instance[name=default]/protocols/ospf/instance

    SR Linux's native YANG calls the protocol `ospf` (not `ospfv2` — that's
    OpenConfig naming). It nests neighbors as instance -> area -> interface ->
    neighbor. The schema does not model OSPF process IDs (instance names) —
    all adjacencies from all instances merge into one dict, keyed by neighbor
    router-id.

    adjacency-state is already lower-cased on SR Linux ("full", "2-way", ...).
    Area IDs are normalized to dotted-decimal form (Rule 10): a bare integer
    "0" becomes "0.0.0.0".

    Empty container ({}) when OSPF is not configured.
    """
    adjacencies = {}
    val = _safe_gnmi_get(
        gc, "/network-instance[name=default]/protocols/ospf/instance",
    )
    if not val:
        return adjacencies

    instances = val.get("srl_nokia-ospf:instance", val) if isinstance(val, dict) else val
    if not isinstance(instances, list):
        return adjacencies

    for instance in instances:
        for area in instance.get("area", []):
            area_id = _normalize_area(area.get("area-id", ""))
            for iface in area.get("interface", []):
                iface_name = iface.get("interface-name", "")
                for neigh in iface.get("neighbor", []):
                    router_id = neigh.get("neighbor-router-id")
                    if not router_id:
                        continue
                    adjacencies[router_id] = {
                        "area": area_id,
                        "interface": iface_name,
                        "adjacency_state": str(
                            neigh.get("adjacency-state", "")
                        ).lower(),
                    }
    return adjacencies


def _normalize_area(area_id):
    """Convert any OSPF area id form to dotted-decimal (schema Rule 10).

    SR Linux may return an area as the int 0, the string "0", or already-dotted
    "0.0.0.0". Schema wants dotted-decimal in every case so intent and reality
    compare like-for-like.
    """
    if area_id == "" or area_id is None:
        return ""
    s = str(area_id)
    if "." in s:
        return s
    try:
        n = int(s)
    except ValueError:
        return s
    return f"{(n >> 24) & 0xFF}.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"


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
        bgp_neighbors = _build_bgp_neighbors(gc)
        ospf_adjacencies = _build_ospf_adjacencies(gc)

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
        "bgp_neighbors": bgp_neighbors,
        "ospf": {"adjacencies": ospf_adjacencies},
    }
