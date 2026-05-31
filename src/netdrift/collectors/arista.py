from datetime import datetime, timezone

from napalm import get_network_driver

from netdrift.collectors.base import register


def _build_ip_list(ip_raw):
    
    ips = []
    for address, detail in ip_raw.get("ipv4", {}).items():
        ips.append(f"{address}/{detail['prefix_length']}")
    return sorted(ips)

def _build_vlans(show_vlan_json):
    """Shape `show vlan` eAPI output into the schema's top-level `vlans` block.

    eAPI returns {"vlans": {"10": {"name": "users", ...}, ...}} — VLAN IDs are
    already JSON string keys, which is what schema Rule 7 requires. We keep the
    string keys and pick out just the name.
    """
    vlans = {}
    for vlan_id, detail in show_vlan_json.get("vlans", {}).items():
        vlans[vlan_id] = {"name": detail.get("name", "")}
    return vlans    

def _expand_interface_name(short_name):
    """Expand an abbreviated EOS interface name to canonical full form.

    `show interfaces switchport` reports names abbreviated ("Et2"); schema
    Rule 1 requires canonical full names ("Ethernet2") so intent and reality
    keys match. The collector owns this expansion.
    """
    if short_name.startswith("Et") and not short_name.startswith("Ethernet"):
        return "Ethernet" + short_name[2:]
    return short_name

def _parse_vlan_range(vlan_spec):
    """Parse an EOS trunk VLAN spec into a sorted list of ints.

    `show interfaces switchport` returns trunk VLANs as a STRING, not a list —
    e.g. "10,20" or "10,20,30-35" (ranges with a hyphen). Expand any ranges,
    return a sorted list[int]. An empty or "ALL"/"NONE" spec returns [].
    """
    if not vlan_spec or not isinstance(vlan_spec, str):
        return []
    if vlan_spec.strip().upper() in ("ALL", "NONE"):
        return []
    vlans = []
    for part in vlan_spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            vlans.extend(range(int(start), int(end) + 1))
        else:
            vlans.append(int(part))
    return sorted(vlans)

def _build_switchport_map(show_switchport_json):
    """Shape `show interfaces switchport` eAPI output into a per-interface lookup.

    Returns {canonical_name: {"mode": ..., "untagged_vlan": ..., "tagged_vlans": ...}}.
    Only switchports appear in this output — routed interfaces are absent, and
    get_reality() treats that absence as mode "routed".
    """
    switchports = {}
    for short_name, info in show_switchport_json.get("switchports", {}).items():
        name = _expand_interface_name(short_name)
        sp = info.get("switchportInfo", {})
        eos_mode = sp.get("mode", "")

        if eos_mode == "access":
            mode = "access"
            untagged_vlan = sp.get("accessVlanId")
            tagged_vlans = []
        elif eos_mode == "trunk":
            mode = "tagged"
            untagged_vlan = None
            tagged_vlans = _parse_vlan_range(sp.get("trunkAllowedVlans", ""))
        else:
            # Unexpected EOS mode — surface loudly per schema Rule 8 rather
            # than inventing a value.
            raise ValueError(
                f"Interface {name}: unrecognized switchport mode '{eos_mode}'"
            )

        switchports[name] = {
            "mode": mode,
            "untagged_vlan": untagged_vlan,
            "tagged_vlans": tagged_vlans,
        }
    return switchports

def _build_bgp_neighbors(napalm_bgp, bgp_summary_json):
    """Shape NAPALM BGP + eAPI `show ip bgp summary | json` into schema form.

    NAPALM's get_bgp_neighbors() gives us the structured intent-like fields —
    remote_as, is_enabled, description — keyed by peer IP under
    {"global": {"peers": {...}}}. It does NOT give the full session state name;
    `is_up` is just a bool.

    The eAPI `show ip bgp summary | json` gives the real `peerState` string
    ("Established", "Idle", "Active", "Connect", "OpenSent", "OpenConfirm").
    The schema (Decision 3) wants the full state lower-cased, so we use the
    eAPI value, not the NAPALM bool.

    Only the `default` VRF is read — v0.3 schema does not model VRFs.
    """
    neighbors = {}
    napalm_peers = napalm_bgp.get("global", {}).get("peers", {})

    default_vrf = bgp_summary_json.get("vrfs", {}).get("default", {})
    summary_peers = default_vrf.get("peers", {})

    for ip, peer in napalm_peers.items():
        summary = summary_peers.get(ip, {})
        peer_state = summary.get("peerState", "")
        neighbors[ip] = {
            "remote_as": peer.get("remote_as"),
            "enabled": peer.get("is_enabled", True),
            "description": peer.get("description", ""),
            "session_state": peer_state.lower(),
        }
    return neighbors


def _build_ospf_adjacencies(ospf_neighbor_json):
    """Shape `show ip ospf neighbor | json` output into the schema's adjacencies block.

    EOS returns a nested structure: vrfs -> <vrf> -> instList -> <pid> ->
    ospfNeighborEntries -> [list of neighbors]. Only the `default` VRF is
    read (v0.3 schema does not model VRFs). Multiple OSPF processes (instList
    keys) are merged into a single adjacencies dict — the schema does not model
    OSPF process IDs either; a router-id is a router-id.

    `adjacencyState` from EOS is already lower-cased ("full", "2-way", ...) and
    `areaId` (nested under `details`) is already dotted form ("0.0.0.0") — so
    no transformation is needed beyond key-picking.
    """
    adjacencies = {}
    default_vrf = ospf_neighbor_json.get("vrfs", {}).get("default", {})
    inst_list = default_vrf.get("instList", {})

    for _process_id, instance in inst_list.items():
        for entry in instance.get("ospfNeighborEntries", []):
            router_id = entry.get("routerId")
            if not router_id:
                continue
            adjacencies[router_id] = {
                "area": entry.get("details", {}).get("areaId", ""),
                "interface": entry.get("interfaceName", ""),
                "adjacency_state": entry.get("adjacencyState", "").lower(),
            }
    return adjacencies

@register("arista_eos", netbox_slugs=("arista-eos", "eos"))
def get_reality(device):
    driver = get_network_driver("eos")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        optional_args={"enforce_verification": False},
    )
    conn.open()
    try:
        raw_interfaces = conn.get_interfaces()
        raw_ips = conn.get_interfaces_ip()
        raw_bgp = conn.get_bgp_neighbors()
        # VLAN data isn't a NAPALM getter — reach the pyeapi connection that
        # NAPALM's EOS driver holds and run the show commands via eAPI directly.
        # encoding="json" returns structured output, not text.
        eapi_results = conn.device.run_commands(
            [
                "show vlan",
                "show interfaces switchport",
                "show ip bgp summary",
                "show ip ospf neighbor",
            ],
            encoding="json",
        )
        # v1.0: full running config as text, for config-level drift (schema
        # `running_config`). NAPALM's get_config returns a dict keyed by
        # running/startup/candidate; we keep the running config only.
        running_config = conn.get_config(retrieve="running")["running"]
    finally:
        conn.close()

    (
        show_vlan_json,
        show_switchport_json,
        bgp_summary_json,
        ospf_neighbor_json,
    ) = eapi_results

    switchport_map = _build_switchport_map(show_switchport_json)

    interfaces = {}
    for name, data in raw_interfaces.items():
        # Switchport data exists only for L2 interfaces. An interface absent
        # from the switchport map is a routed (L3) interface — schema Rule 8.
        sp = switchport_map.get(name, {
            "mode": "routed",
            "untagged_vlan": None,
            "tagged_vlans": [],
        })
        interfaces[name] = {
            "description": data["description"],
            "enabled": data["is_enabled"],
            "ip_addresses": _build_ip_list(raw_ips.get(name, {})),
            "mode": sp["mode"],
            "untagged_vlan": sp["untagged_vlan"],
            "tagged_vlans": sp["tagged_vlans"],
        }

    return {
        "device": device["name"],
        "platform": "arista_eos",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interfaces": interfaces,
        "vlans": _build_vlans(show_vlan_json),
        "bgp_neighbors": _build_bgp_neighbors(raw_bgp, bgp_summary_json),
        "ospf": {"adjacencies": _build_ospf_adjacencies(ospf_neighbor_json)},
        "running_config": running_config,
    }

