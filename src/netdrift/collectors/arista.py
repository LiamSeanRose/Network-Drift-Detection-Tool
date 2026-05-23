from datetime import datetime, timezone

from napalm import get_network_driver


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
        # VLAN data isn't a NAPALM getter — reach the pyeapi connection that
        # NAPALM's EOS driver holds and run the show commands via eAPI directly.
        # encoding="json" returns structured output, not text.
        eapi_results = conn.device.run_commands(
            ["show vlan", "show interfaces switchport"], encoding="json"
        )
    finally:
        conn.close()

    show_vlan_json, show_switchport_json = eapi_results

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
    }

