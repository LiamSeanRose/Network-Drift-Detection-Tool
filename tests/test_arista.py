"""tests/test_arista.py — Arista EOS collector unit tests (v0.2).

The collector (collectors/arista.py) does two things: it talks to a live cEOS
node via NAPALM, and it transforms the NAPALM + eAPI responses into the
normalized schema (docs/schema.md Section 2). These tests cover the transform,
never the network — like test_differ.py / test_storage.py / test_nokia.py, so
they run fast in CI with no lab node up.

The pure helpers are tested directly with canned dicts. get_reality() is tested
with a fake NAPALM connection (FakeNapalmConn) standing in for the EOS driver's
connection object — it answers .get_interfaces(), .get_interfaces_ip() and the
nested .device.run_commands() from canned payloads, opening no socket.
"""

import pytest

from unittest.mock import patch

from netdrift.collectors.arista import (
    _build_bgp_neighbors,
    _build_ip_list,
    _build_ospf_adjacencies,
    _build_switchport_map,
    _build_tunnels,
    _build_vlans,
    _expand_interface_name,
    _parse_vlan_range,
    get_reality,
)


# --- _build_ip_list ----------------------------------------------------------
# NAPALM get_interfaces_ip() value shape: {"ipv4": {"10.0.0.1": {"prefix_length": 30}}}

def test_build_ip_list_formats_cidr():
    ip_raw = {"ipv4": {"10.0.0.1": {"prefix_length": 30}}}
    assert _build_ip_list(ip_raw) == ["10.0.0.1/30"]


def test_build_ip_list_sorts_multiple():
    ip_raw = {"ipv4": {
        "10.0.0.9": {"prefix_length": 24},
        "10.0.0.1": {"prefix_length": 24},
    }}
    assert _build_ip_list(ip_raw) == ["10.0.0.1/24", "10.0.0.9/24"]


def test_build_ip_list_no_ipv4_is_empty():
    # An interface with no IPs — get_interfaces_ip() returns {} for it.
    assert _build_ip_list({}) == []


# --- _build_vlans ------------------------------------------------------------
# eAPI `show vlan` shape: {"vlans": {"10": {"name": "users", ...}, ...}}

def test_build_vlans_keeps_string_keys_and_name():
    show_vlan = {"vlans": {
        "10": {"name": "users", "status": "active"},
        "20": {"name": "voice", "status": "active"},
    }}
    assert _build_vlans(show_vlan) == {
        "10": {"name": "users"},
        "20": {"name": "voice"},
    }


def test_build_vlans_missing_name_is_empty_string():
    # A VLAN with no name key -> "" (schema Rule 4: never None).
    assert _build_vlans({"vlans": {"30": {}}}) == {"30": {"name": ""}}


def test_build_vlans_no_vlans_is_empty():
    assert _build_vlans({}) == {}


# --- _expand_interface_name --------------------------------------------------

def test_expand_abbreviated_name():
    assert _expand_interface_name("Et2") == "Ethernet2"


def test_expand_leaves_full_name_unchanged():
    assert _expand_interface_name("Ethernet2") == "Ethernet2"


def test_expand_leaves_non_ethernet_unchanged():
    # "Management1" starts with neither "Et" pattern — passes through.
    assert _expand_interface_name("Management1") == "Management1"


# --- _parse_vlan_range -------------------------------------------------------

def test_parse_vlan_range_comma_list():
    assert _parse_vlan_range("10,20") == [10, 20]


def test_parse_vlan_range_expands_hyphen_range():
    assert _parse_vlan_range("30-35") == [30, 31, 32, 33, 34, 35]


def test_parse_vlan_range_mixed_list_and_range():
    assert _parse_vlan_range("10,20,30-32") == [10, 20, 30, 31, 32]


def test_parse_vlan_range_sorts_result():
    assert _parse_vlan_range("30,10,20") == [10, 20, 30]


def test_parse_vlan_range_all_is_empty():
    # "ALL" / "NONE" / empty all mean "no specific tagged VLANs" -> [].
    assert _parse_vlan_range("ALL") == []
    assert _parse_vlan_range("NONE") == []
    assert _parse_vlan_range("") == []


# --- _build_switchport_map ---------------------------------------------------
# eAPI `show interfaces switchport` shape:
#   {"switchports": {"Et2": {"switchportInfo": {"mode": "access", ...}}}}

def test_switchport_map_access_port():
    show_sp = {"switchports": {
        "Et2": {"switchportInfo": {"mode": "access", "accessVlanId": 10}},
    }}
    result = _build_switchport_map(show_sp)
    # Name expanded to canonical form; access port mapped.
    assert result == {"Ethernet2": {
        "mode": "access", "untagged_vlan": 10, "tagged_vlans": [],
    }}


def test_switchport_map_trunk_port():
    show_sp = {"switchports": {
        "Et3": {"switchportInfo": {
            "mode": "trunk", "trunkAllowedVlans": "10,20",
        }},
    }}
    result = _build_switchport_map(show_sp)
    assert result == {"Ethernet3": {
        "mode": "tagged", "untagged_vlan": None, "tagged_vlans": [10, 20],
    }}


def test_switchport_map_unknown_mode_raises():
    # schema Rule 8: an unclassifiable mode is a loud collector error, not a
    # made-up schema value.
    show_sp = {"switchports": {
        "Et4": {"switchportInfo": {"mode": "dot1q-tunnel"}},
    }}
    with pytest.raises(ValueError):
        _build_switchport_map(show_sp)


# --- get_reality (NAPALM connection mocked) ----------------------------------

# Canned running config the fake's get_config() returns; get_reality copies it
# verbatim into the reality dict's `running_config`.
RUNNING_CONFIG = (
    "hostname core-sw-01\n!\ninterface Ethernet1\n"
    "   description Uplink to core\n!\nend\n"
)


class FakeNapalmDevice:
    """Stands in for conn.device — the pyeapi connection NAPALM's EOS driver
    holds. arista.py calls .device.run_commands([...], encoding="json").

    The tunnel block (v4.75) is fetched in a separate, defensive run_commands
    call, so this fake dispatches `["show interfaces tunnel"]` to its own canned
    result and everything else to the main batch result."""

    def __init__(self, run_commands_result, tunnel_result=None):
        self._result = run_commands_result
        self._tunnel_result = (
            tunnel_result if tunnel_result is not None else [{"interfaces": {}}]
        )

    def run_commands(self, commands, encoding=None):
        if commands == ["show interfaces tunnel"]:
            return self._tunnel_result
        return self._result


class FakeNapalmConn:
    """Stands in for the NAPALM EOS connection object.

    Answers the four calls get_reality() makes — get_interfaces(),
    get_interfaces_ip(), get_bgp_neighbors(), and device.run_commands() —
    from canned payloads. open() / close() are no-ops; no socket is ever opened.
    """

    def __init__(self, interfaces, interfaces_ip, bgp_neighbors, run_commands_result,
                 os_version="4.28.0F", tunnel_result=None):
        self._interfaces = interfaces
        self._interfaces_ip = interfaces_ip
        self._bgp_neighbors = bgp_neighbors
        self._os_version = os_version
        self.device = FakeNapalmDevice(run_commands_result, tunnel_result)

    def open(self):
        pass

    def close(self):
        pass

    def get_facts(self):
        return {"os_version": self._os_version, "hostname": "core-sw-01",
                "vendor": "Arista", "model": "cEOSLab", "uptime": 0,
                "serial_number": "", "fqdn": "", "interface_list": []}

    def get_interfaces(self):
        return self._interfaces

    def get_interfaces_ip(self):
        return self._interfaces_ip

    def get_bgp_neighbors(self):
        return self._bgp_neighbors

    def get_config(self, retrieve="running"):
        # NAPALM's get_config returns running/startup/candidate; get_reality
        # keeps the running config only.
        return {"running": RUNNING_CONFIG, "startup": "", "candidate": ""}


# A consistent device: Ethernet1 is a routed uplink with an IP and an OSPF
# adjacency / BGP peer to 10.0.0.2; Ethernet2 is an access port on VLAN 10.
# VLANs 10 and 20 exist.
INTERFACES = {
    "Ethernet1": {
        "description": "Uplink to core",
        "is_enabled": True,
        "is_up": True,
    },
    "Ethernet2": {
        "description": "Access port - users",
        "is_enabled": True,
        "is_up": True,
    },
}

INTERFACES_IP = {
    "Ethernet1": {"ipv4": {"10.0.0.1": {"prefix_length": 30}}},
    # Ethernet2 has no IP — absent from get_interfaces_ip() output entirely.
}

# NAPALM get_bgp_neighbors() value shape:
# {"global": {"peers": {"<ip>": {remote_as, is_enabled, description, ...}}}}
BGP_NEIGHBORS = {
    "global": {
        "peers": {
            "10.0.0.2": {
                "remote_as": 65000,
                "is_enabled": True,
                "description": "iBGP to core-sw-02",
            },
        },
    },
}

# run_commands(["show vlan", "show interfaces switchport",
#               "show ip bgp summary", "show ip ospf neighbor"]) returns a
# list in the same order as the commands.
RUN_COMMANDS_RESULT = [
    {"vlans": {
        "10": {"name": "users"},
        "20": {"name": "voice"},
    }},
    {"switchports": {
        "Et2": {"switchportInfo": {"mode": "access", "accessVlanId": 10}},
    }},
    # show ip bgp summary | json — peerState is what the eAPI returns,
    # capitalized. The collector lower-cases it.
    {"vrfs": {
        "default": {
            "peers": {
                "10.0.0.2": {"peerState": "Established"},
            },
        },
    }},
    # show ip ospf neighbor | json — adjacencyState already lower-case from
    # EOS; areaId is dotted form, nested under details.
    {"vrfs": {
        "default": {
            "instList": {
                "1": {
                    "ospfNeighborEntries": [
                        {
                            "routerId": "2.2.2.2",
                            "interfaceName": "Ethernet1",
                            "adjacencyState": "full",
                            "details": {"areaId": "0.0.0.0"},
                        },
                    ],
                },
            },
        },
    }},
]

DEVICE = {
    "name": "core-sw-01",
    "hostname": "172.20.20.11",
    "username": "admin",
    "password": "irrelevant-no-socket-opens",
}


def _run_get_reality(tunnel_result=None):
    """Run get_reality() with the NAPALM driver patched out for the fake."""
    fake_conn = FakeNapalmConn(
        INTERFACES, INTERFACES_IP, BGP_NEIGHBORS, RUN_COMMANDS_RESULT,
        tunnel_result=tunnel_result,
    )
    # arista.py does `driver = get_network_driver("eos")` then `driver(...)`.
    # Patch get_network_driver so it returns a factory that yields our fake
    # connection regardless of the arguments passed.
    with patch(
        "netdrift.collectors.arista.get_network_driver",
        return_value=lambda *a, **kw: fake_conn,
    ):
        return get_reality(DEVICE)


def test_get_reality_top_level_shape():
    result = _run_get_reality()
    assert result["device"] == "core-sw-01"
    assert result["platform"] == "arista_eos"
    assert set(result.keys()) == {
        "device", "platform", "collected_at", "interfaces", "vlans",
        "bgp_neighbors", "ospf", "tunnels", "running_config", "software_version",
    }


def test_get_reality_no_tunnels_is_empty_block():
    # With no tunnel result injected, the fake returns {"interfaces": {}} and the
    # collector emits an empty tunnels block (present key, no diff noise).
    assert _run_get_reality()["tunnels"] == {}


def test_get_reality_includes_running_config():
    result = _run_get_reality()
    assert result["running_config"] == RUNNING_CONFIG


def test_get_reality_software_version():
    result = _run_get_reality()
    assert result["software_version"] == "4.28.0F"


def test_get_reality_collected_at_is_utc_iso():
    # schema Rule 2: ISO 8601 UTC with a Z suffix.
    assert _run_get_reality()["collected_at"].endswith("Z")


def test_get_reality_builds_both_interfaces():
    assert set(_run_get_reality()["interfaces"]) == {"Ethernet1", "Ethernet2"}


def test_get_reality_routed_interface_has_ip_and_no_vlan():
    eth1 = _run_get_reality()["interfaces"]["Ethernet1"]
    assert eth1["ip_addresses"] == ["10.0.0.1/30"]
    # Ethernet1 is absent from the switchport map -> routed fallback.
    assert eth1["mode"] == "routed"
    assert eth1["untagged_vlan"] is None


def test_get_reality_access_interface_mapped():
    eth2 = _run_get_reality()["interfaces"]["Ethernet2"]
    assert eth2["mode"] == "access"
    assert eth2["untagged_vlan"] == 10
    # No IP on this interface -> empty list, not a missing key.
    assert eth2["ip_addresses"] == []


def test_get_reality_builds_vlans_block():
    assert _run_get_reality()["vlans"] == {
        "10": {"name": "users"},
        "20": {"name": "voice"},
    }
# --- _build_bgp_neighbors ----------------------------------------------------
# NAPALM get_bgp_neighbors() value shape: {"global": {"peers": {"<ip>": {...}}}}
# eAPI `show ip bgp summary | json` shape: {"vrfs": {"default": {"peers": {...}}}}

def test_build_bgp_neighbors_merges_napalm_and_eapi():
    napalm_bgp = {"global": {"peers": {
        "10.0.0.2": {
            "remote_as": 65000,
            "is_enabled": True,
            "description": "iBGP",
        },
    }}}
    summary = {"vrfs": {"default": {"peers": {
        "10.0.0.2": {"peerState": "Established"},
    }}}}
    assert _build_bgp_neighbors(napalm_bgp, summary) == {
        "10.0.0.2": {
            "remote_as": 65000,
            "enabled": True,
            "description": "iBGP",
            # schema Rule 10: state lower-cased.
            "session_state": "established",
        },
    }


def test_build_bgp_neighbors_lowercases_all_states():
    # All six EOS state names — collector must lower-case every one to match
    # the schema's allowed values.
    napalm_bgp = {"global": {"peers": {
        f"10.0.0.{i + 2}": {"remote_as": 65000, "is_enabled": True, "description": ""}
        for i in range(6)
    }}}
    states = ["Established", "Idle", "Active", "Connect", "OpenSent", "OpenConfirm"]
    summary = {"vrfs": {"default": {"peers": {
        f"10.0.0.{i + 2}": {"peerState": state}
        for i, state in enumerate(states)
    }}}}
    result = _build_bgp_neighbors(napalm_bgp, summary)
    assert [result[f"10.0.0.{i + 2}"]["session_state"] for i in range(6)] == [
        "established", "idle", "active", "connect", "opensent", "openconfirm",
    ]


def test_build_bgp_neighbors_no_peers_is_empty():
    # No BGP configured -> empty dict, never None (schema Rule 4 spirit).
    assert _build_bgp_neighbors({"global": {"peers": {}}}, {"vrfs": {}}) == {}


def test_build_bgp_neighbors_missing_description_is_empty_string():
    # NAPALM may omit description entirely — schema Rule 4: "" not None.
    napalm_bgp = {"global": {"peers": {
        "10.0.0.2": {"remote_as": 65000, "is_enabled": True},
    }}}
    summary = {"vrfs": {"default": {"peers": {
        "10.0.0.2": {"peerState": "Established"},
    }}}}
    assert _build_bgp_neighbors(napalm_bgp, summary)["10.0.0.2"]["description"] == ""


# --- _build_ospf_adjacencies -------------------------------------------------
# eAPI `show ip ospf neighbor | json` shape:
#   {"vrfs": {"default": {"instList": {"1": {"ospfNeighborEntries": [...]}}}}}

def test_build_ospf_adjacencies_basic():
    ospf_json = {"vrfs": {"default": {"instList": {"1": {
        "ospfNeighborEntries": [
            {
                "routerId": "2.2.2.2",
                "interfaceName": "Ethernet1",
                "adjacencyState": "full",
                "details": {"areaId": "0.0.0.0"},
            },
        ],
    }}}}}
    assert _build_ospf_adjacencies(ospf_json) == {
        "2.2.2.2": {
            "area": "0.0.0.0",
            "interface": "Ethernet1",
            "adjacency_state": "full",
        },
    }


def test_build_ospf_adjacencies_merges_multiple_processes():
    # EOS supports multiple OSPF processes; schema does not model process ID,
    # so adjacencies from instList "1" and "2" merge into one dict.
    ospf_json = {"vrfs": {"default": {"instList": {
        "1": {"ospfNeighborEntries": [
            {
                "routerId": "2.2.2.2", "interfaceName": "Ethernet1",
                "adjacencyState": "full", "details": {"areaId": "0.0.0.0"},
            },
        ]},
        "2": {"ospfNeighborEntries": [
            {
                "routerId": "3.3.3.3", "interfaceName": "Ethernet2",
                "adjacencyState": "full", "details": {"areaId": "0.0.0.1"},
            },
        ]},
    }}}}
    result = _build_ospf_adjacencies(ospf_json)
    assert set(result.keys()) == {"2.2.2.2", "3.3.3.3"}


def test_build_ospf_adjacencies_no_ospf_is_empty():
    assert _build_ospf_adjacencies({"vrfs": {}}) == {}


def test_build_ospf_adjacencies_skips_entries_without_router_id():
    # A malformed entry without a routerId would crash a naive dict-key
    # assignment; the builder should silently skip it.
    ospf_json = {"vrfs": {"default": {"instList": {"1": {
        "ospfNeighborEntries": [
            {"interfaceName": "Ethernet1", "adjacencyState": "full",
             "details": {"areaId": "0.0.0.0"}},
        ],
    }}}}}
    assert _build_ospf_adjacencies(ospf_json) == {}


# --- get_reality routing assertions ------------------------------------------

def test_get_reality_builds_bgp_neighbors_block():
    result = _run_get_reality()
    assert result["bgp_neighbors"] == {
        "10.0.0.2": {
            "remote_as": 65000,
            "enabled": True,
            "description": "iBGP to core-sw-02",
            "session_state": "established",
        },
    }


def test_get_reality_builds_ospf_block():
    result = _run_get_reality()
    assert result["ospf"] == {
        "adjacencies": {
            "2.2.2.2": {
                "area": "0.0.0.0",
                "interface": "Ethernet1",
                "adjacency_state": "full",
            },
        },
    }


# --- _build_tunnels (v4.75) --------------------------------------------------
# UNVALIDATED eAPI `show interfaces tunnel` shape — keys modelled, not yet
# captured from a live cEOS tunnel. Marked unvalidated_fixture via conftest.
#   {"interfaces": {"Tunnel0": {"tunnelMode": "gre", "sourceAddress": ...,
#    "destinationAddress": ..., "interfaceStatus": ..., "lineProtocolStatus": ...}}}

def test_build_tunnels_gre_up():
    show = {"interfaces": {"Tunnel0": {
        "tunnelMode": "gre",
        "sourceAddress": "192.0.2.1",
        "destinationAddress": "198.51.100.1",
        "interfaceStatus": "connected",
        "lineProtocolStatus": "up",
    }}}
    assert _build_tunnels(show) == {"Tunnel0": {
        "type": "gre",
        "source": "192.0.2.1",
        "destination": "198.51.100.1",
        "enabled": True,
        "tunnel_state": "up",
    }}


def test_build_tunnels_vti_type_preserved():
    show = {"interfaces": {"Tunnel1": {
        "tunnelMode": "vti", "sourceAddress": "10.0.0.1",
        "destinationAddress": "10.0.0.2", "interfaceStatus": "connected",
        "lineProtocolStatus": "up",
    }}}
    assert _build_tunnels(show)["Tunnel1"]["type"] == "vti"


def test_build_tunnels_admin_down_is_disabled():
    # EOS reports an admin-shut interface as interfaceStatus "disabled".
    show = {"interfaces": {"Tunnel0": {
        "tunnelMode": "gre", "sourceAddress": "192.0.2.1",
        "destinationAddress": "198.51.100.1", "interfaceStatus": "disabled",
        "lineProtocolStatus": "down",
    }}}
    result = _build_tunnels(show)["Tunnel0"]
    assert result["enabled"] is False
    assert result["tunnel_state"] == "down"


def test_build_tunnels_oper_down_but_admin_up():
    show = {"interfaces": {"Tunnel0": {
        "tunnelMode": "gre", "sourceAddress": "192.0.2.1",
        "destinationAddress": "198.51.100.1", "interfaceStatus": "notconnect",
        "lineProtocolStatus": "down",
    }}}
    result = _build_tunnels(show)["Tunnel0"]
    assert result["enabled"] is True
    assert result["tunnel_state"] == "down"


def test_build_tunnels_skips_out_of_scope_mode():
    # v4.75 is GRE + VTI only; an ipsec/mpls tunnel is skipped, not mistyped.
    show = {"interfaces": {
        "Tunnel0": {"tunnelMode": "gre", "sourceAddress": "1.1.1.1",
                    "destinationAddress": "2.2.2.2", "interfaceStatus": "connected",
                    "lineProtocolStatus": "up"},
        "Tunnel9": {"tunnelMode": "ipsec", "interfaceStatus": "connected",
                    "lineProtocolStatus": "up"},
    }}
    result = _build_tunnels(show)
    assert set(result) == {"Tunnel0"}


def test_build_tunnels_missing_endpoints_are_empty_strings():
    # schema Rule 4: absent string fields are "", never None.
    show = {"interfaces": {"Tunnel0": {
        "tunnelMode": "gre", "interfaceStatus": "connected",
        "lineProtocolStatus": "up",
    }}}
    result = _build_tunnels(show)["Tunnel0"]
    assert result["source"] == ""
    assert result["destination"] == ""


def test_build_tunnels_no_tunnels_is_empty():
    assert _build_tunnels({"interfaces": {}}) == {}
    assert _build_tunnels({}) == {}


def test_get_reality_builds_tunnels_block():
    tunnel_result = [{"interfaces": {"Tunnel0": {
        "tunnelMode": "gre", "sourceAddress": "192.0.2.1",
        "destinationAddress": "198.51.100.1", "interfaceStatus": "connected",
        "lineProtocolStatus": "up",
    }}}]
    result = _run_get_reality(tunnel_result=tunnel_result)
    assert result["tunnels"] == {"Tunnel0": {
        "type": "gre",
        "source": "192.0.2.1",
        "destination": "198.51.100.1",
        "enabled": True,
        "tunnel_state": "up",
    }}
