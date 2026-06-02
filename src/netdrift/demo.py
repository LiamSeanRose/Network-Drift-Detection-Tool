"""demo.py — bundled offline demo dataset for `driftcheck demo`.

Lets a brand-new user see real drift output with one command and zero setup: no
NetBox, no live device, no Postgres. The intent/reality pairs below are
hand-built fictional snapshots of a small two-device network. They flow through
the real `differ.diff()`, so what `driftcheck demo` prints is genuine differ
output — the same code path a live check uses — not a canned string.

The dataset is built to show a realistic spread: a should-be-up interface that
is down (critical), VLAN documentation drift (warning/info), a flapping BGP
session, an OSPF area mismatch, a whole interface missing, and tunnel drift
(critical down + a wrong destination), exercising every top-level block
including the v4.75 `tunnels` feature.
"""

from netdrift import differ


def _iface(description="", enabled=True, ip_addresses=None, mode="routed",
           untagged_vlan=None, tagged_vlans=None):
    """A schema-complete interface dict (schema.md Section 2)."""
    return {
        "description": description,
        "enabled": enabled,
        "ip_addresses": ip_addresses or [],
        "mode": mode,
        "untagged_vlan": untagged_vlan,
        "tagged_vlans": tagged_vlans or [],
    }


def _tunnel(type="gre", source="", destination="", enabled=True, tunnel_state="up"):
    """A schema-complete tunnel dict (v4.75)."""
    return {
        "type": type,
        "source": source,
        "destination": destination,
        "enabled": enabled,
        "tunnel_state": tunnel_state,
    }


def _state(platform, interfaces=None, vlans=None, bgp_neighbors=None,
           ospf=None, tunnels=None):
    """A schema-complete device-state object. `collected_at` is omitted — the
    differ does not read it, and a fixed timestamp would only mislead."""
    state = {
        "platform": platform,
        "interfaces": interfaces or {},
        "vlans": vlans or {},
        "bgp_neighbors": bgp_neighbors or {},
        "ospf": ospf or {"adjacencies": {}},
        "running_config": "",
        "software_version": "",
    }
    if tunnels is not None:
        state["tunnels"] = tunnels
    return state


# --- Device 1: core-sw-01 (Arista EOS) --------------------------------------
# A core switch with a downed uplink, VLAN drift, a flapping iBGP session, and a
# GRE tunnel that has gone down.

_CORE_INTENT = _state(
    "arista_eos",
    interfaces={
        "Ethernet1": _iface(description="Uplink to dist-01", enabled=True,
                            ip_addresses=["10.0.1.1/31"]),
        "Ethernet2": _iface(description="Access - users", enabled=True,
                            mode="access", untagged_vlan=10),
    },
    vlans={"10": {"name": "users"}, "20": {"name": "voice"}},
    bgp_neighbors={
        "10.0.1.5": {"remote_as": 65000, "enabled": True,
                      "description": "iBGP to core-sw-02",
                      "session_state": "established"},
    },
    tunnels={
        "Tunnel0": _tunnel(type="gre", source="10.0.1.1",
                           destination="203.0.113.10", enabled=True,
                           tunnel_state="up"),
    },
)

_CORE_REALITY = _state(
    "arista_eos",
    interfaces={
        # Uplink is administratively down but intent wants it up -> critical.
        "Ethernet1": _iface(description="Uplink to dist-01", enabled=False,
                            ip_addresses=["10.0.1.1/31"]),
        # Access VLAN changed out from under the docs -> warning.
        "Ethernet2": _iface(description="Access - users", enabled=True,
                            mode="access", untagged_vlan=99),
    },
    # VLAN 20 documented but absent (warning); VLAN 30 present but undocumented (info).
    vlans={"10": {"name": "users"}, "30": {"name": "mgmt"}},
    bgp_neighbors={
        # Session is flapping: intent established, reality active -> warning.
        "10.0.1.5": {"remote_as": 65000, "enabled": True,
                      "description": "iBGP to core-sw-02",
                      "session_state": "active"},
    },
    tunnels={
        # Tunnel line protocol is down while intent expects up -> critical.
        "Tunnel0": _tunnel(type="gre", source="10.0.1.1",
                           destination="203.0.113.10", enabled=True,
                           tunnel_state="down"),
    },
)


# --- Device 2: edge-rtr-01 (Cisco IOS-XE) -----------------------------------
# An edge router with a missing interface, an OSPF area mismatch, and a tunnel
# pointed at the wrong destination.

_EDGE_INTENT = _state(
    "cisco_iosxe",
    interfaces={
        "GigabitEthernet0/0": _iface(description="WAN", enabled=True,
                                     ip_addresses=["198.51.100.2/30"]),
        "GigabitEthernet0/1": _iface(description="LAN", enabled=True,
                                     ip_addresses=["10.0.2.1/24"]),
    },
    ospf={"adjacencies": {
        "2.2.2.2": {"area": "0.0.0.0", "interface": "GigabitEthernet0/0",
                     "adjacency_state": "full"},
    }},
    tunnels={
        "Tunnel10": _tunnel(type="vti", source="198.51.100.2",
                            destination="198.51.100.20", enabled=True,
                            tunnel_state="up"),
    },
)

_EDGE_REALITY = _state(
    "cisco_iosxe",
    interfaces={
        "GigabitEthernet0/0": _iface(description="WAN", enabled=True,
                                     ip_addresses=["198.51.100.2/30"]),
        # GigabitEthernet0/1 is documented but missing from the device -> critical.
    },
    ospf={"adjacencies": {
        # Adjacency formed in the wrong area -> warning.
        "2.2.2.2": {"area": "0.0.0.1", "interface": "GigabitEthernet0/0",
                     "adjacency_state": "full"},
    }},
    tunnels={
        # Tunnel destination differs from intent -> warning (silent misroute).
        "Tunnel10": _tunnel(type="vti", source="198.51.100.2",
                            destination="198.51.100.99", enabled=True,
                            tunnel_state="up"),
    },
)


# Each entry is (device_name, intent, reality). Order is the print order.
DEMO_DEVICES = [
    ("core-sw-01", _CORE_INTENT, _CORE_REALITY),
    ("edge-rtr-01", _EDGE_INTENT, _EDGE_REALITY),
]


def demo_pairs():
    """Return the bundled demo dataset as a list of (name, intent, reality)."""
    return DEMO_DEVICES


def demo_drift_records():
    """Return every demo drift record, stamped with `device` and `platform`.

    Runs each pair through the real `differ.diff()`, then stamps the two fields
    the pipeline normally adds (device from the pair name, platform from the
    intent), so the result is ready to hand straight to `save_drifts()`.
    """
    records = []
    for name, intent, reality in DEMO_DEVICES:
        drifts = differ.diff(intent, reality)
        for record in drifts:
            record["device"] = name
            record["platform"] = intent["platform"]
        records.extend(drifts)
    return records
