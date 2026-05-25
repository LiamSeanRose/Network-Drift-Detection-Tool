"""collectors/nokia.py — Nokia SR Linux collector.

The "reality" side for Nokia SR Linux devices. Connects to an SR Linux node
and returns its real state in the normalized schema (docs/schema.md Section 2).

Mirrors collectors/arista.py: same public function, same returned shape. The
diff engine consumes this output identically — it does not care which vendor
produced it.

NOTE — partial implementation (v0.2, first pass):
    The v0.1 interface fields (description, enabled, ip_addresses) are fully
    implemented. The v0.2 VLAN fields (mode, untagged_vlan, tagged_vlans, and
    the top-level `vlans` block) are STUBBED to schema-valid defaults — every
    interface reports mode "routed" with no VLANs, and `vlans` is empty.
    SR Linux has no NAPALM VLAN getter (same gap arista.py fills with raw
    eAPI); the SR Linux equivalent is a separate second pass. The stub keeps
    the returned dict schema-complete so the diff engine never sees a missing
    key — it just will not yet detect real VLAN drift on Nokia devices.

Public function:
    get_reality(device: dict) -> dict
"""

from datetime import datetime, timezone

from napalm import get_network_driver


def _build_ip_list(ip_raw):
    """Shape a NAPALM get_interfaces_ip entry into a sorted list of CIDR strings.

    Identical logic to arista.py: pull each IPv4 address and its prefix length,
    format as "address/prefix", sort ascending (schema Rule 3). IPv6 is not in
    the v0.1/v0.2 schema, so it is ignored.
    """
    ips = []
    for address, detail in ip_raw.get("ipv4", {}).items():
        ips.append(f"{address}/{detail['prefix_length']}")
    return sorted(ips)


def _strip_subinterface(name):
    """Strip an SR Linux subinterface suffix, returning the parent interface name.

    SR Linux assigns IP addresses to *subinterfaces* — get_interfaces_ip()
    returns keys like "mgmt0.0" and "ethernet-1/1.0", where ".0" is the
    subinterface index. get_interfaces() uses the bare parent name ("mgmt0",
    "ethernet-1/1"). To attach an IP to its interface we match on the parent,
    so we drop everything from the last "." onward.

    Arista does not need this — EOS reports IPs directly on the interface.
    "ethernet-1/1.0" -> "ethernet-1/1";  "mgmt0.0" -> "mgmt0".
    """
    return name.rsplit(".", 1)[0]


def _build_ip_map(raw_ips):
    """Collapse SR Linux's subinterface-keyed IP data onto parent interfaces.

    raw_ips is keyed by subinterface ("ethernet-1/1.0"). Returns a dict keyed
    by parent interface name ("ethernet-1/1"), each value the sorted CIDR list
    for that interface. If a parent somehow has multiple subinterfaces with
    IPs, their addresses are merged and re-sorted.
    """
    ip_map = {}
    for sub_name, ip_raw in raw_ips.items():
        parent = _strip_subinterface(sub_name)
        addresses = _build_ip_list(ip_raw)
        if parent in ip_map:
            ip_map[parent] = sorted(ip_map[parent] + addresses)
        else:
            ip_map[parent] = addresses
    return ip_map


def get_reality(device):
    """Return the real state of an SR Linux device in the normalized schema.

    `device` is a dict with at least: name, hostname, username, password.
    """
    driver = get_network_driver("srl")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        # insecure=True skips the CA/certificate setup by trusting whatever
        # cert the node presents. Fine for the lab; NOT for production.
        # JSON_IETF is the encoding the SR Linux driver expects for gNMI.
        optional_args={"insecure": True, "encoding": "JSON_IETF"},
    )
    conn.open()
    try:
        raw_interfaces = conn.get_interfaces()
        raw_ips = conn.get_interfaces_ip()
    finally:
        conn.close()

    ip_map = _build_ip_map(raw_ips)

    interfaces = {}
    for name, data in raw_interfaces.items():
        interfaces[name] = {
            # --- v0.1 fields (fully implemented) ---
            "description": data["description"],
            "enabled": data["is_enabled"],
            "ip_addresses": ip_map.get(name, []),
            # --- v0.2 VLAN fields (STUBBED — see module docstring) ---
            # Schema-valid defaults so the dict is schema-complete. Replaced
            # with real SR Linux VLAN data in the second pass.
            "mode": "routed",
            "untagged_vlan": None,
            "tagged_vlans": [],
        }

    return {
        "device": device["name"],
        "platform": "nokia_srlinux",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interfaces": interfaces,
        # STUBBED — empty until the VLAN second pass. Schema Rule 7: keys
        # would be VLAN IDs as strings.
        "vlans": {},
    }