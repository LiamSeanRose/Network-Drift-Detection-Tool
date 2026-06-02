from netdrift import differ
from netdrift.diagnose import diagnose


def _drift(object_str, field, drift_kind):
    return {"object": object_str, "field": field, "drift_kind": drift_kind}


def test_unknown_combination_returns_empty():
    assert diagnose(_drift("interface:Eth1", "nonexistent", "value_mismatch")) == []


def test_interface_missing_in_reality():
    causes = diagnose(_drift("interface:Ethernet1", "_interface", "missing_in_reality"))
    assert len(causes) > 0 and all(isinstance(c, str) for c in causes)


def test_interface_enabled_mismatch():
    assert len(diagnose(_drift("interface:Ethernet1", "enabled", "value_mismatch"))) > 0


def test_interface_ip_mismatch():
    assert len(diagnose(_drift("interface:Ethernet1", "ip_addresses", "value_mismatch"))) > 0


def test_vlan_missing_in_intent():
    assert len(diagnose(_drift("vlan:100", "_vlan", "missing_in_intent"))) > 0


def test_bgp_session_state_mismatch():
    assert len(diagnose(_drift("bgp_neighbor:10.0.0.1", "session_state", "value_mismatch"))) > 0


def test_bgp_remote_as_mismatch():
    assert len(diagnose(_drift("bgp_neighbor:10.0.0.1", "remote_as", "value_mismatch"))) > 0


def test_ospf_missing_in_reality():
    assert len(diagnose(_drift("ospf_adjacency:1.1.1.1", "_ospf_adjacency", "missing_in_reality"))) > 0


def test_ospf_adjacency_state_mismatch():
    assert len(diagnose(_drift("ospf_adjacency:1.1.1.1", "adjacency_state", "value_mismatch"))) > 0


def test_config_drift():
    assert len(diagnose(_drift("config", "running_config", "value_mismatch"))) > 0


def test_interface_mtu_mismatch():
    assert len(diagnose(_drift("interface:Ethernet1", "mtu", "value_mismatch"))) > 0


def test_bgp_password_mismatch():
    assert len(diagnose(_drift("bgp_neighbor:10.0.0.1", "password", "value_mismatch"))) > 0


def test_ospf_network_type_mismatch():
    assert len(diagnose(_drift("ospf_adjacency:1.1.1.1", "network_type", "value_mismatch"))) > 0


# --- software_version + tunnels (v4.75) -------------------------------------

def test_software_version_mismatch():
    assert len(diagnose(_drift("device", "software_version", "value_mismatch"))) > 0


def test_tunnel_missing_in_reality():
    assert len(diagnose(_drift("tunnel:Tunnel0", "_tunnel", "missing_in_reality"))) > 0


def test_tunnel_undocumented():
    assert len(diagnose(_drift("tunnel:Tunnel0", "_tunnel", "missing_in_intent"))) > 0


def test_tunnel_state_mismatch():
    assert len(diagnose(_drift("tunnel:Tunnel0", "tunnel_state", "value_mismatch"))) > 0


def test_tunnel_destination_mismatch():
    assert len(diagnose(_drift("tunnel:Tunnel0", "destination", "value_mismatch"))) > 0


# --- coverage guard: every drift the differ emits has a cause hint ----------

def _iface(enabled=True, description="", ip_addresses=None, mode="routed",
           untagged_vlan=None, tagged_vlans=None):
    return {"description": description, "enabled": enabled,
            "ip_addresses": ip_addresses or [], "mode": mode,
            "untagged_vlan": untagged_vlan, "tagged_vlans": tagged_vlans or []}


def _tunnel(type="gre", source="192.0.2.1", destination="198.51.100.1",
            enabled=True, tunnel_state="up"):
    return {"type": type, "source": source, "destination": destination,
            "enabled": enabled, "tunnel_state": tunnel_state}


def _state(**overrides):
    base = {
        "device": "core-sw-01", "platform": "arista_eos",
        "collected_at": "2026-06-02T00:00:00Z",
        "interfaces": {}, "vlans": {}, "bgp_neighbors": {},
        "ospf": {"adjacencies": {}}, "running_config": "", "software_version": "",
    }
    base.update(overrides)
    return base


def test_every_differ_drift_has_a_cause_hint():
    """diagnose.py must cover every fingerprint differ.diff() can emit. Crafts an
    intent/reality pair that triggers all of them (interface/vlan/bgp/ospf/config/
    device/tunnel) and asserts no record comes back with an empty hint list. This
    fails loudly if a new drift type is added to the differ without a matching
    cause entry — the gap that left tunnel and software_version uncovered."""
    intent = _state(
        interfaces={
            "Ethernet1": _iface(enabled=True, description="uplink",
                                ip_addresses=["10.0.0.1/31"], mode="routed",
                                untagged_vlan=10, tagged_vlans=[10, 20]),
            "Ethernet2": _iface(),  # missing_in_reality
        },
        vlans={"10": {"name": "users"}, "20": {"name": "voice"}},
        bgp_neighbors={
            "10.0.0.1": {"remote_as": 65001, "enabled": True,
                          "description": "peer-a", "session_state": "established"},
            "10.0.0.2": {"remote_as": 65002, "enabled": True,
                          "description": "peer-b", "session_state": "established"},
        },
        ospf={"adjacencies": {
            "2.2.2.2": {"area": "0.0.0.0", "interface": "Ethernet1",
                         "adjacency_state": "full"},
            "3.3.3.3": {"area": "0.0.0.0", "interface": "Ethernet2",
                         "adjacency_state": "full"},
        }},
        running_config="hostname a\n", software_version="4.30.1F",
        tunnels={"Tunnel0": _tunnel(), "Tunnel1": _tunnel()},
    )
    reality = _state(
        interfaces={
            "Ethernet1": _iface(enabled=False, description="changed",
                                ip_addresses=["10.0.0.3/31"], mode="access",
                                untagged_vlan=99, tagged_vlans=[10]),
            "Ethernet3": _iface(),  # missing_in_intent
        },
        vlans={"10": {"name": "USERS"}, "30": {"name": "mgmt"}},
        bgp_neighbors={
            "10.0.0.1": {"remote_as": 65009, "enabled": False,
                          "description": "renamed", "session_state": "active"},
            "10.0.0.9": {"remote_as": 65003, "enabled": True,
                          "description": "peer-c", "session_state": "established"},
        },
        ospf={"adjacencies": {
            "2.2.2.2": {"area": "0.0.0.1", "interface": "Ethernet9",
                         "adjacency_state": "init"},
            "9.9.9.9": {"area": "0.0.0.0", "interface": "Ethernet3",
                         "adjacency_state": "full"},
        }},
        running_config="hostname b\n", software_version="4.31.2F",
        tunnels={
            "Tunnel0": _tunnel(type="vti", source="192.0.2.9",
                               destination="198.51.100.9", enabled=False,
                               tunnel_state="down"),
            "Tunnel2": _tunnel(),  # missing_in_intent
        },
    )

    records = differ.diff(intent, reality)
    uncovered = [(d["object"].split(":")[0], d["field"], d["drift_kind"])
                 for d in records if not diagnose(d)]
    assert not uncovered, f"diagnose.py has no cause hint for: {sorted(set(uncovered))}"
