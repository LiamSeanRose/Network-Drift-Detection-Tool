"""collectors/cisco.py — Cisco IOS-XE collector (v0.3).

Uses the NAPALM `ios` driver (via Netmiko/SSH) for interfaces, IPs, and base
BGP data. NAPALM's `get_vlans()` and `get_interfaces_switchport()` cover the
L2 state via TextFSM-parsed `show` output. For the two things NAPALM can't
give us in full — BGP session state and OSPF adjacencies — we call
`conn.cli()` and parse the text directly.

Validated against Cisco Catalyst 3850 (IOS-XE) at lab node `cisco-sw-01`
(192.168.5.50). User must have privilege 15 or supply a `secret` key in
devices.yml for enable-mode escalation.
"""

import re
from datetime import datetime, timezone

from napalm import get_network_driver


def _build_ip_list(ip_raw):
    """Shape NAPALM get_interfaces_ip() value into sorted CIDR list."""
    ips = []
    for address, detail in ip_raw.get("ipv4", {}).items():
        ips.append(f"{address}/{detail['prefix_length']}")
    return sorted(ips)


def _build_vlans(napalm_vlans):
    """Shape NAPALM get_vlans() output into the schema's vlans block.

    NAPALM IOS returns {vlan_id: {"name": ..., "interfaces": [...]}}.
    Schema Rule 7 requires string keys; str() every key to be safe.
    """
    return {str(vid): {"name": info.get("name", "")} for vid, info in napalm_vlans.items()}


def _parse_trunk_vlans(vlan_spec):
    """Parse an IOS trunk VLAN spec string into a sorted list of ints.

    IOS reports trunk VLANs as a string, e.g. "10,20" or "10,30-33".
    Same expansion logic as arista._parse_vlan_range.
    """
    if not vlan_spec or not isinstance(vlan_spec, str):
        return []
    if vlan_spec.strip().upper() in ("ALL", "NONE"):
        return []
    vlans = []
    for part in vlan_spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            vlans.extend(range(int(start), int(end) + 1))
        else:
            vlans.append(int(part))
    return sorted(vlans)


_IOS_IFNAME_PREFIXES = [
    ("GigabitEthernet", "Gi"),
    ("TenGigabitEthernet", "Te"),
    ("HundredGigabitEthernet", "Hu"),
    ("FortyGigabitEthernet", "Fo"),
    ("TwentyFiveGigabitEthernet", "Twe"),
    ("TwoGigabitEthernet", "Tw"),
    ("FiveGigabitEthernet", "Fi"),
    ("FastEthernet", "Fa"),
    ("Ethernet", "Et"),
    ("Loopback", "Lo"),
    ("Port-channel", "Po"),
    ("Vlan", "Vl"),
    ("Management", "Mg"),
    ("Serial", "Se"),
    ("Tunnel", "Tu"),
]


def _expand_ios_ifname(abbrev):
    """Expand an IOS abbreviated interface name to canonical form.

    IOS CLI output (e.g. show interfaces switchport) uses abbreviated names
    like Gi1/0/1; the schema and NAPALM get_interfaces() use full names.
    """
    for full, prefix in _IOS_IFNAME_PREFIXES:
        if abbrev.startswith(prefix) and not abbrev.startswith(full):
            return full + abbrev[len(prefix):]
    return abbrev


def _parse_switchport_cli(text):
    """Parse `show interfaces switchport` CLI text into a per-interface lookup.

    Returns {canonical_name: {"mode": ..., "untagged_vlan": ..., "tagged_vlans": ...}}.
    Routed interfaces (Switchport: Disabled) are excluded; their absence from
    the returned dict signals "routed" to get_reality().
    """
    switchports = {}
    cur = {}

    def _commit():
        name = cur.get("name")
        if not name or not cur.get("enabled") or not cur.get("mode_raw"):
            return
        mode_raw = cur["mode_raw"]
        if "access" in mode_raw:
            switchports[name] = {
                "mode": "access",
                "untagged_vlan": cur.get("access_vlan"),
                "tagged_vlans": [],
            }
        elif "trunk" in mode_raw:
            switchports[name] = {
                "mode": "tagged",
                "untagged_vlan": None,
                "tagged_vlans": _parse_trunk_vlans(cur.get("trunk_vlans", "")),
            }

    for line in text.splitlines():
        line = line.strip()
        m = re.match(r'^Name:\s+(\S+)', line)
        if m:
            _commit()
            cur = {"name": _expand_ios_ifname(m.group(1))}
            continue
        if re.match(r'^Switchport:\s+Enabled', line):
            cur["enabled"] = True
            continue
        m = re.match(r'^Administrative Mode:\s+(.+)', line)
        if m:
            cur["mode_raw"] = m.group(1).strip()
            continue
        m = re.match(r'^Access Mode VLAN:\s+(\d+)', line)
        if m:
            cur["access_vlan"] = int(m.group(1))
            continue
        m = re.match(r'^Trunking VLANs Enabled:\s+(.+)', line)
        if m:
            cur["trunk_vlans"] = m.group(1).strip()
            continue

    _commit()
    return switchports


def _normalize_ospf_state(ios_state):
    """Map an IOS OSPF state string to the schema's lower-cased form.

    IOS uses "FULL", "2WAY", "INIT", etc. The schema uses "full", "2-way",
    "init". "2WAY" is the only value that isn't a simple lowercase.
    """
    state = ios_state.strip().upper()
    if state == "2WAY":
        return "2-way"
    return state.lower()


def _normalize_area(area_str):
    """Convert an IOS OSPF area to dotted-decimal (schema requirement).

    IOS may report area as an integer ("0") or already dotted ("0.0.0.0").
    """
    area = area_str.strip()
    if "." in area:
        return area
    n = int(area)
    return f"{(n >> 24) & 0xff}.{(n >> 16) & 0xff}.{(n >> 8) & 0xff}.{n & 0xff}"


def _parse_bgp_summary(napalm_bgp, bgp_summary_text):
    """Build schema BGP neighbors from NAPALM get_bgp_neighbors() + CLI text.

    NAPALM's get_bgp_neighbors() gives remote_as, is_enabled, description.
    It does NOT give the full session state string — only the bool `is_up`.
    `show ip bgp summary` gives the actual state:
      - A digit in the State/PfxRcd column means Established (prefix count).
      - A word means that word is the state (Active, Idle, Connect, ...).

    Only the global (default) VRF is read; the v0.3 schema does not model VRFs.
    """
    napalm_peers = napalm_bgp.get("global", {}).get("peers", {})
    if not napalm_peers:
        return {}

    states = {}
    for line in bgp_summary_text.splitlines():
        parts = line.split()
        if len(parts) >= 9 and re.match(r'^\d+\.\d+\.\d+\.\d+$', parts[0]):
            last = parts[-1]
            states[parts[0]] = "established" if last.isdigit() else last.lower()

    neighbors = {}
    for ip, peer in napalm_peers.items():
        neighbors[ip] = {
            "remote_as": peer.get("remote_as"),
            "enabled": peer.get("is_enabled", True),
            "description": peer.get("description", ""),
            "session_state": states.get(ip, "unknown"),
        }
    return neighbors


def _parse_ospf_neighbors(ospf_detail_text):
    """Parse `show ip ospf neighbor detail` text into the schema adjacencies block.

    IOS detail output is a sequence of per-neighbor blocks:
        Neighbor 2.2.2.2, interface address 10.0.0.2
            In the area 0 via interface GigabitEthernet1/0/1
            Neighbor priority is 1, State is FULL, ...

    Each block yields one adjacency keyed by router-id.
    """
    adjacencies = {}
    current_id = None
    block = {}

    for line in ospf_detail_text.splitlines():
        m = re.match(r'\s*Neighbor\s+(\d+\.\d+\.\d+\.\d+),', line)
        if m:
            current_id = m.group(1)
            block = {}
            continue

        if current_id is None:
            continue

        m = re.match(r'\s+In the area (\S+) via interface (\S+)', line)
        if m:
            block["area"] = _normalize_area(m.group(1))
            block["interface"] = m.group(2)
            continue

        m = re.match(r'\s+Neighbor priority.*State is (\w+)', line)
        if m:
            block["adjacency_state"] = _normalize_ospf_state(m.group(1))
            if "area" in block and "interface" in block:
                adjacencies[current_id] = {
                    "area": block["area"],
                    "interface": block["interface"],
                    "adjacency_state": block["adjacency_state"],
                }
            current_id = None

    return adjacencies


def get_reality(device):
    driver = get_network_driver("ios")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        optional_args={"secret": device.get("secret", "")},
    )
    conn.open()
    try:
        raw_interfaces = conn.get_interfaces()
        raw_ips = conn.get_interfaces_ip()
        raw_bgp = conn.get_bgp_neighbors()
        raw_vlans = conn.get_vlans()
        cli_output = conn.cli([
            "show ip bgp summary",
            "show ip ospf neighbor detail",
            "show interfaces switchport",
        ])
    finally:
        conn.close()

    switchport_map = _parse_switchport_cli(cli_output.get("show interfaces switchport", ""))

    interfaces = {}
    for name, data in raw_interfaces.items():
        sp = switchport_map.get(name, {
            "mode": "routed",
            "untagged_vlan": None,
            "tagged_vlans": [],
        })
        interfaces[name] = {
            "description": data.get("description", ""),
            "enabled": data["is_enabled"],
            "ip_addresses": _build_ip_list(raw_ips.get(name, {})),
            "mode": sp["mode"],
            "untagged_vlan": sp["untagged_vlan"],
            "tagged_vlans": sp["tagged_vlans"],
        }

    return {
        "device": device["name"],
        "platform": "cisco_iosxe",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interfaces": interfaces,
        "vlans": _build_vlans(raw_vlans),
        "bgp_neighbors": _parse_bgp_summary(
            raw_bgp, cli_output.get("show ip bgp summary", "")
        ),
        "ospf": {
            "adjacencies": _parse_ospf_neighbors(
                cli_output.get("show ip ospf neighbor detail", "")
            ),
        },
    }
