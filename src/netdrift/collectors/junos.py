"""collectors/junos.py — Juniper JunOS collector.

Reads reality from Juniper EX/QFX/MX/SRX devices via NAPALM's `junos` driver
(ncclient + NETCONF under the hood). Supplements NAPALM with two CLI calls:

  `show bgp summary`    — for the per-peer session state string (NAPALM only
                          gives is_up:bool, same limitation as on Arista).
  `show ospf neighbor`  — no NAPALM getter for OSPF; the text output is parsed.

Interface mode is always "routed" in v3.75. JunOS L2/switchport mode detection
requires correlating VLAN membership with interface configuration, which is not
reliably available via NAPALM across ELS vs pre-ELS JunOS platforms. This is
a known gap deferred to v4.5.

VLAN data comes from NAPALM get_vlans(). On non-ELS platforms the getter may
return an empty dict — that is handled gracefully (no drift on vlans block).
"""

from datetime import datetime, timezone

from napalm import get_network_driver

from netdrift.collectors.base import register


def _build_ip_list(ip_raw: dict) -> list[str]:
    """Shape a NAPALM get_interfaces_ip() entry into a sorted CIDR list."""
    ips = []
    for address, detail in ip_raw.get("ipv4", {}).items():
        ips.append(f"{address}/{detail['prefix_length']}")
    return sorted(ips)


def _build_vlans(napalm_vlans: dict) -> dict:
    """Shape NAPALM get_vlans() output into the schema's top-level vlans block.

    NAPALM returns {"10": {"name": "users", "interfaces": [...]}}. The schema
    wants {"10": {"name": "users"}} — VLAN IDs as string keys, name only.
    An empty dict (non-ELS platforms) is a valid result, not an error.
    """
    vlans = {}
    for vlan_id, detail in napalm_vlans.items():
        vlans[str(vlan_id)] = {"name": detail.get("name", "")}
    return vlans


def _looks_like_ip(s: str) -> bool:
    """Return True if the string looks like a dotted-decimal IPv4 address."""
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _parse_bgp_summary(text: str) -> dict:
    """Parse `show bgp summary` to extract per-peer session state strings.

    When a session is Established and has received routes, JunOS shows the
    route counts instead of the state string: "2/2/2/0". We map that back to
    "established". Non-established sessions display the state name directly
    ("Idle", "Active", "Connect", etc.).

    Returns {peer_ip: lower-cased state string}.
    """
    states = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not _looks_like_ip(parts[0]):
            continue
        ip = parts[0]
        # Last column: "State|#Active/Received/Accepted/Damped" or count summary.
        raw_state = parts[-1].split("|")[0]
        # A count summary like "2/2/2/0" means the session is established.
        if raw_state.replace("/", "").isdigit():
            states[ip] = "established"
        else:
            states[ip] = raw_state.lower()
    return states


def _build_bgp_neighbors(napalm_bgp: dict, bgp_summary_text: str) -> dict:
    """Shape NAPALM get_bgp_neighbors() + show bgp summary into schema form.

    NAPALM provides remote_as, is_enabled, description. It only gives is_up
    (bool) for session state — the actual state string ("idle", "active", …)
    comes from the CLI summary, same reason Arista supplements NAPALM with eAPI.
    """
    summary_states = _parse_bgp_summary(bgp_summary_text)
    neighbors = {}
    peers = napalm_bgp.get("global", {}).get("peers", {})
    for ip, peer in peers.items():
        neighbors[ip] = {
            "remote_as": peer.get("remote_as"),
            "enabled": peer.get("is_enabled", True),
            "description": peer.get("description", ""),
            # Fall back to is_up→"established"/"idle" if the CLI parse missed it.
            "session_state": summary_states.get(
                ip,
                "established" if peer.get("is_up") else "idle",
            ),
        }
    return neighbors


def _parse_ospf_neighbors(text: str) -> dict:
    """Parse `show ospf neighbor` text output into the schema adjacencies block.

    JunOS column layout (header + one main line + one detail line per neighbor):

        Address          Interface              State     ID               Pri  Dead
        10.0.0.2         ge-0/0/0.0             Full      2.2.2.2          128    35
          Area 0.0.0.0, opt 0x52, DR 0.0.0.0, BDR 0.0.0.0

    The area is on the indented detail line that immediately follows each peer.
    Interface names from OSPF output use logical unit form (ge-0/0/0.0); the
    schema uses physical names — strip the trailing ".0".
    """
    adjacencies = {}
    last_router_id = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Address"):
            continue

        # Indented detail line: "  Area 0.0.0.0, opt ..."
        if stripped.startswith("Area") and last_router_id:
            area_parts = stripped.split(",")[0].split()
            if len(area_parts) >= 2:
                adjacencies[last_router_id]["area"] = area_parts[1]
            last_router_id = None
            continue

        # Main neighbor line: Address  Interface  State  RouterID  Pri  Dead
        parts = stripped.split()
        if len(parts) >= 4 and _looks_like_ip(parts[0]):
            router_id = parts[3]
            iface = parts[1]
            # Strip the logical unit suffix (.0) to get the physical interface key.
            if "." in iface:
                iface = iface.rsplit(".", 1)[0]
            adjacencies[router_id] = {
                "area": "0.0.0.0",  # overwritten by the following detail line
                "interface": iface,
                "adjacency_state": parts[2].lower(),
            }
            last_router_id = router_id

    return adjacencies


@register("juniper_junos", netbox_slugs=("juniper-junos", "junos", "juniper"))
def get_reality(device: dict) -> dict:
    """Collect reality for a Juniper JunOS device via NAPALM."""
    driver = get_network_driver("junos")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        optional_args={"timeout": 30},
    )
    conn.open()
    try:
        raw_interfaces = conn.get_interfaces()
        raw_ips = conn.get_interfaces_ip()
        raw_bgp = conn.get_bgp_neighbors()
        raw_vlans = conn.get_vlans()
        cli_output = conn.cli(["show bgp summary", "show ospf neighbor"])
        running_config = conn.get_config(retrieve="running")["running"]
    finally:
        conn.close()

    bgp_summary_text = cli_output.get("show bgp summary", "")
    ospf_text = cli_output.get("show ospf neighbor", "")

    # IPs are keyed by logical unit (ge-0/0/0.0); interfaces by physical name
    # (ge-0/0/0). Try the .0 unit first, fall back to the bare name.
    def _ips_for(name: str) -> list[str]:
        return _build_ip_list(
            raw_ips.get(f"{name}.0") or raw_ips.get(name) or {}
        )

    interfaces = {}
    for name, data in raw_interfaces.items():
        interfaces[name] = {
            "description": data.get("description", ""),
            "enabled": data.get("is_enabled", True),
            "ip_addresses": _ips_for(name),
            # v3.75: all interfaces reported as routed. L2 mode detection across
            # ELS/non-ELS JunOS requires VLAN membership correlation not reliably
            # available via NAPALM — deferred to v4.5.
            "mode": "routed",
            "untagged_vlan": None,
            "tagged_vlans": [],
        }

    return {
        "device": device["name"],
        "platform": "juniper_junos",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interfaces": interfaces,
        "vlans": _build_vlans(raw_vlans),
        "bgp_neighbors": _build_bgp_neighbors(raw_bgp, bgp_summary_text),
        "ospf": {"adjacencies": _parse_ospf_neighbors(ospf_text)},
        "running_config": running_config,
    }
