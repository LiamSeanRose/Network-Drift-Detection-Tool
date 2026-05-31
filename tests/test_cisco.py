"""tests/test_cisco.py — Cisco IOS-XE collector unit tests (v0.3).

Mirrors the structure of test_arista.py: all NAPALM calls are replaced by a
FakeNapalmConn — no live device, no socket. Tests verify the transform logic
(helper functions) and the full get_reality() integration path.

NOTE: CLI text fixtures (BGP summary, OSPF detail) are modelled from real
IOS-XE 16.x / Catalyst 3850 output. If field positions or wording differ on
other IOS-XE versions, adjust the fixtures here — the parser is the contract,
not the device.
"""

from unittest.mock import patch

from netdrift.collectors.cisco import (
    _build_ip_list,
    _build_vlans,
    _expand_ios_ifname,
    _normalize_area,
    _normalize_ospf_state,
    _parse_bgp_summary,
    _parse_ospf_neighbors,
    _parse_switchport_cli,
    _parse_trunk_vlans,
    get_reality,
)


# ---------------------------------------------------------------------------
# FakeNapalmConn — stands in for the NAPALM IOS driver connection object
# ---------------------------------------------------------------------------

# Canned running config the fake's get_config() returns; get_reality copies it
# verbatim into the reality dict's `running_config`.
RUNNING_CONFIG = (
    "hostname cisco-sw-01\n!\ninterface GigabitEthernet1/0/1\n"
    " description Uplink\n!\nend\n"
)


class FakeNapalmConn:
    """Answers all get_reality() calls from canned data. No socket opened."""

    def __init__(self, interfaces, interfaces_ip, bgp_neighbors, vlans, cli_results):
        self._interfaces = interfaces
        self._interfaces_ip = interfaces_ip
        self._bgp_neighbors = bgp_neighbors
        self._vlans = vlans
        self._cli_results = cli_results

    def open(self): pass
    def close(self): pass
    def get_interfaces(self): return self._interfaces
    def get_interfaces_ip(self): return self._interfaces_ip
    def get_bgp_neighbors(self): return self._bgp_neighbors
    def get_vlans(self): return self._vlans
    def cli(self, commands): return self._cli_results
    def get_config(self, retrieve="running"):
        return {"running": RUNNING_CONFIG, "startup": "", "candidate": ""}


# ---------------------------------------------------------------------------
# Canned data — a minimal Catalyst 3850 with one routed uplink (Gi1/0/1)
# and one access port (Gi1/0/2), one BGP peer, one OSPF adjacency.
# ---------------------------------------------------------------------------

INTERFACES = {
    "GigabitEthernet1/0/1": {
        "description": "Uplink to core",
        "is_enabled": True,
        "is_up": True,
    },
    "GigabitEthernet1/0/2": {
        "description": "Access port - users",
        "is_enabled": True,
        "is_up": True,
    },
}

INTERFACES_IP = {
    "GigabitEthernet1/0/1": {"ipv4": {"10.0.0.1": {"prefix_length": 30}}},
    # GigabitEthernet1/0/2 has no IP — absent from get_interfaces_ip() entirely.
}

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

# NAPALM get_vlans() for IOS returns {vlan_id: {"name": ..., "interfaces": []}}.
# Keys may be strings or ints depending on NAPALM version; _build_vlans() str()s them.
VLANS = {
    "10": {"name": "users", "interfaces": ["GigabitEthernet1/0/2"]},
    "20": {"name": "voice", "interfaces": []},
}

# Realistic IOS-XE `show interfaces switchport` text. Gi1/0/1 is routed
# (Switchport: Disabled); Gi1/0/2 is an access port on VLAN 10.
SWITCHPORT_CLI_TEXT = (
    "Name: Gi1/0/1\n"
    "Switchport: Disabled\n"
    "Administrative Mode: dynamic desirable\n"
    "Operational Mode: down\n"
    "\n"
    "Name: Gi1/0/2\n"
    "Switchport: Enabled\n"
    "Administrative Mode: static access\n"
    "Operational Mode: static access\n"
    "Administrative Trunking Encapsulation: dot1q\n"
    "Operational Trunking Encapsulation: native\n"
    "Negotiation of Trunking: Off\n"
    "Access Mode VLAN: 10 (users)\n"
    "Trunking Native Mode VLAN: 1 (default)\n"
)

# Realistic IOS-XE `show ip bgp summary` text. Last column is a prefix count
# (digit) when established, or the state name (word) otherwise.
BGP_SUMMARY_TEXT = (
    "BGP router identifier 1.1.1.1, local AS number 65000\n"
    "BGP table version is 3, main routing table version 3\n"
    "\n"
    "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
    "10.0.0.2        4        65000      55      55        3    0    0 00:45:00        2\n"
)

# Realistic IOS-XE `show ip ospf neighbor detail` text.
OSPF_DETAIL_TEXT = (
    " Neighbor 2.2.2.2, interface address 10.0.0.2\n"
    "    In the area 0 via interface GigabitEthernet1/0/1\n"
    "    Neighbor priority is 1, State is FULL, 6 state changes\n"
    "    DR is 10.0.0.1 BDR is 10.0.0.2\n"
    "    Options is 0x12\n"
    "    Dead timer due in 00:00:39\n"
)

CLI_RESULTS = {
    "show ip bgp summary": BGP_SUMMARY_TEXT,
    "show ip ospf neighbor detail": OSPF_DETAIL_TEXT,
    "show interfaces switchport": SWITCHPORT_CLI_TEXT,
}

DEVICE = {
    "name": "cisco-sw-01",
    "hostname": "192.168.5.50",
    "username": "admin",
    "password": "irrelevant-no-socket-opens",
}


def _run_get_reality():
    """Run get_reality() with the NAPALM IOS driver patched for the fake."""
    fake_conn = FakeNapalmConn(
        INTERFACES, INTERFACES_IP, BGP_NEIGHBORS, VLANS, CLI_RESULTS,
    )
    with patch(
        "netdrift.collectors.cisco.get_network_driver",
        return_value=lambda *a, **kw: fake_conn,
    ):
        return get_reality(DEVICE)


# ---------------------------------------------------------------------------
# _build_ip_list
# ---------------------------------------------------------------------------

def test_build_ip_list_formats_cidr():
    ip_raw = {"ipv4": {"192.168.1.1": {"prefix_length": 24}}}
    assert _build_ip_list(ip_raw) == ["192.168.1.1/24"]


def test_build_ip_list_sorts_multiple():
    ip_raw = {"ipv4": {
        "10.0.0.9": {"prefix_length": 30},
        "10.0.0.1": {"prefix_length": 30},
    }}
    assert _build_ip_list(ip_raw) == ["10.0.0.1/30", "10.0.0.9/30"]


def test_build_ip_list_no_ipv4_is_empty():
    assert _build_ip_list({}) == []


# ---------------------------------------------------------------------------
# _build_vlans
# ---------------------------------------------------------------------------

def test_build_vlans_string_keys_preserved():
    raw = {"10": {"name": "users", "interfaces": []}, "20": {"name": "voice", "interfaces": []}}
    assert _build_vlans(raw) == {"10": {"name": "users"}, "20": {"name": "voice"}}


def test_build_vlans_int_keys_converted_to_strings():
    # NAPALM may return int keys — schema Rule 7 requires strings.
    raw = {10: {"name": "users", "interfaces": []}}
    assert _build_vlans(raw) == {"10": {"name": "users"}}


def test_build_vlans_missing_name_is_empty_string():
    # Schema Rule 4: string fields are "" not None.
    assert _build_vlans({"30": {}}) == {"30": {"name": ""}}


def test_build_vlans_empty_input_is_empty():
    assert _build_vlans({}) == {}


# ---------------------------------------------------------------------------
# _parse_trunk_vlans
# ---------------------------------------------------------------------------

def test_parse_trunk_vlans_comma_list():
    assert _parse_trunk_vlans("10,20") == [10, 20]


def test_parse_trunk_vlans_expands_range():
    assert _parse_trunk_vlans("30-33") == [30, 31, 32, 33]


def test_parse_trunk_vlans_mixed_list_and_range():
    assert _parse_trunk_vlans("10,30-32") == [10, 30, 31, 32]


def test_parse_trunk_vlans_sorts_result():
    assert _parse_trunk_vlans("30,10,20") == [10, 20, 30]


def test_parse_trunk_vlans_none_keyword_is_empty():
    assert _parse_trunk_vlans("NONE") == []


def test_parse_trunk_vlans_empty_string_is_empty():
    assert _parse_trunk_vlans("") == []


# ---------------------------------------------------------------------------
# _expand_ios_ifname
# ---------------------------------------------------------------------------

def test_expand_ios_ifname_gigabit():
    assert _expand_ios_ifname("Gi1/0/1") == "GigabitEthernet1/0/1"


def test_expand_ios_ifname_ten_gigabit():
    assert _expand_ios_ifname("Te1/1/1") == "TenGigabitEthernet1/1/1"


def test_expand_ios_ifname_fast_ethernet():
    assert _expand_ios_ifname("Fa0/1") == "FastEthernet0/1"


def test_expand_ios_ifname_already_full_is_unchanged():
    assert _expand_ios_ifname("GigabitEthernet1/0/1") == "GigabitEthernet1/0/1"


def test_expand_ios_ifname_unknown_prefix_is_unchanged():
    assert _expand_ios_ifname("Xz1/0/1") == "Xz1/0/1"


# ---------------------------------------------------------------------------
# _parse_switchport_cli
# ---------------------------------------------------------------------------

def test_parse_switchport_cli_access_port():
    text = (
        "Name: Gi1/0/2\n"
        "Switchport: Enabled\n"
        "Administrative Mode: static access\n"
        "Access Mode VLAN: 10 (users)\n"
    )
    assert _parse_switchport_cli(text) == {"GigabitEthernet1/0/2": {
        "mode": "access", "untagged_vlan": 10, "tagged_vlans": [],
    }}


def test_parse_switchport_cli_trunk_port():
    text = (
        "Name: Gi1/0/3\n"
        "Switchport: Enabled\n"
        "Administrative Mode: trunk\n"
        "Trunking VLANs Enabled: 10,20\n"
    )
    assert _parse_switchport_cli(text) == {"GigabitEthernet1/0/3": {
        "mode": "tagged", "untagged_vlan": None, "tagged_vlans": [10, 20],
    }}


def test_parse_switchport_cli_routed_port_excluded():
    text = (
        "Name: Gi1/0/1\n"
        "Switchport: Disabled\n"
        "Administrative Mode: dynamic desirable\n"
    )
    assert _parse_switchport_cli(text) == {}


def test_parse_switchport_cli_multiple_interfaces():
    text = (
        "Name: Gi1/0/1\n"
        "Switchport: Disabled\n"
        "\n"
        "Name: Gi1/0/2\n"
        "Switchport: Enabled\n"
        "Administrative Mode: static access\n"
        "Access Mode VLAN: 10 (users)\n"
    )
    result = _parse_switchport_cli(text)
    assert set(result.keys()) == {"GigabitEthernet1/0/2"}
    assert result["GigabitEthernet1/0/2"]["mode"] == "access"


def test_parse_switchport_cli_empty_input_is_empty():
    assert _parse_switchport_cli("") == {}


# ---------------------------------------------------------------------------
# _normalize_ospf_state
# ---------------------------------------------------------------------------

def test_normalize_ospf_state_full():
    assert _normalize_ospf_state("FULL") == "full"


def test_normalize_ospf_state_2way():
    # IOS uses "2WAY"; schema uses "2-way" (matching the EOS adjacencyState value).
    assert _normalize_ospf_state("2WAY") == "2-way"


def test_normalize_ospf_state_init():
    assert _normalize_ospf_state("INIT") == "init"


def test_normalize_ospf_state_down():
    assert _normalize_ospf_state("DOWN") == "down"


# ---------------------------------------------------------------------------
# _normalize_area
# ---------------------------------------------------------------------------

def test_normalize_area_integer_zero():
    assert _normalize_area("0") == "0.0.0.0"


def test_normalize_area_integer_one():
    assert _normalize_area("1") == "0.0.0.1"


def test_normalize_area_already_dotted():
    assert _normalize_area("0.0.0.0") == "0.0.0.0"


def test_normalize_area_dotted_nonzero():
    assert _normalize_area("0.0.0.1") == "0.0.0.1"


# ---------------------------------------------------------------------------
# _parse_bgp_summary
# ---------------------------------------------------------------------------

def test_parse_bgp_summary_established_from_prefix_count():
    text = (
        "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
        "10.0.0.2        4        65000      55      55        3    0    0 00:45:00        2\n"
    )
    napalm_bgp = {"global": {"peers": {
        "10.0.0.2": {"remote_as": 65000, "is_enabled": True, "description": ""},
    }}}
    result = _parse_bgp_summary(napalm_bgp, text)
    assert result["10.0.0.2"]["session_state"] == "established"


def test_parse_bgp_summary_active_state():
    text = (
        "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
        "10.0.0.2        4        65000       0       0        0    0    0 00:10:23    Active\n"
    )
    napalm_bgp = {"global": {"peers": {
        "10.0.0.2": {"remote_as": 65000, "is_enabled": True, "description": ""},
    }}}
    result = _parse_bgp_summary(napalm_bgp, text)
    assert result["10.0.0.2"]["session_state"] == "active"


def test_parse_bgp_summary_idle_state():
    text = (
        "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
        "10.0.0.2        4        65000       0       0        0    0    0 never       Idle\n"
    )
    napalm_bgp = {"global": {"peers": {
        "10.0.0.2": {"remote_as": 65000, "is_enabled": True, "description": ""},
    }}}
    result = _parse_bgp_summary(napalm_bgp, text)
    assert result["10.0.0.2"]["session_state"] == "idle"


def test_parse_bgp_summary_preserves_remote_as_and_description():
    text = (
        "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
        "10.0.0.2        4        65000      10      10        1    0    0 00:05:00        1\n"
    )
    napalm_bgp = {"global": {"peers": {
        "10.0.0.2": {"remote_as": 65000, "is_enabled": True, "description": "iBGP peer"},
    }}}
    result = _parse_bgp_summary(napalm_bgp, text)
    assert result["10.0.0.2"]["remote_as"] == 65000
    assert result["10.0.0.2"]["description"] == "iBGP peer"
    assert result["10.0.0.2"]["enabled"] is True


def test_parse_bgp_summary_no_peers_is_empty():
    assert _parse_bgp_summary({"global": {"peers": {}}}, "") == {}


def test_parse_bgp_summary_missing_description_is_empty_string():
    text = (
        "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
        "10.0.0.2        4        65000      10      10        1    0    0 00:01:00        0\n"
    )
    napalm_bgp = {"global": {"peers": {
        "10.0.0.2": {"remote_as": 65000, "is_enabled": True},
    }}}
    assert _parse_bgp_summary(napalm_bgp, text)["10.0.0.2"]["description"] == ""


# ---------------------------------------------------------------------------
# _parse_ospf_neighbors
# ---------------------------------------------------------------------------

def test_parse_ospf_neighbors_full_adjacency():
    text = (
        " Neighbor 2.2.2.2, interface address 10.0.0.2\n"
        "    In the area 0 via interface GigabitEthernet1/0/1\n"
        "    Neighbor priority is 1, State is FULL, 6 state changes\n"
    )
    assert _parse_ospf_neighbors(text) == {
        "2.2.2.2": {
            "area": "0.0.0.0",
            "interface": "GigabitEthernet1/0/1",
            "adjacency_state": "full",
        },
    }


def test_parse_ospf_neighbors_2way_state():
    text = (
        " Neighbor 3.3.3.3, interface address 10.0.1.2\n"
        "    In the area 1 via interface GigabitEthernet1/0/2\n"
        "    Neighbor priority is 1, State is 2WAY, 2 state changes\n"
    )
    result = _parse_ospf_neighbors(text)
    assert result["3.3.3.3"]["adjacency_state"] == "2-way"


def test_parse_ospf_neighbors_area_integer_normalized():
    text = (
        " Neighbor 4.4.4.4, interface address 10.0.2.2\n"
        "    In the area 0 via interface GigabitEthernet1/0/3\n"
        "    Neighbor priority is 1, State is FULL, 1 state changes\n"
    )
    assert _parse_ospf_neighbors(text)["4.4.4.4"]["area"] == "0.0.0.0"


def test_parse_ospf_neighbors_multiple_adjacencies():
    text = (
        " Neighbor 2.2.2.2, interface address 10.0.0.2\n"
        "    In the area 0 via interface GigabitEthernet1/0/1\n"
        "    Neighbor priority is 1, State is FULL, 6 state changes\n"
        " Neighbor 3.3.3.3, interface address 10.0.1.2\n"
        "    In the area 0 via interface GigabitEthernet1/0/2\n"
        "    Neighbor priority is 1, State is FULL, 3 state changes\n"
    )
    result = _parse_ospf_neighbors(text)
    assert set(result.keys()) == {"2.2.2.2", "3.3.3.3"}


def test_parse_ospf_neighbors_no_ospf_is_empty():
    assert _parse_ospf_neighbors("") == {}


# ---------------------------------------------------------------------------
# get_reality — full integration with FakeNapalmConn
# ---------------------------------------------------------------------------

def test_get_reality_top_level_shape():
    result = _run_get_reality()
    assert result["device"] == "cisco-sw-01"
    assert result["platform"] == "cisco_iosxe"
    assert set(result.keys()) == {
        "device", "platform", "collected_at", "interfaces", "vlans",
        "bgp_neighbors", "ospf", "running_config",
    }


def test_get_reality_includes_running_config():
    result = _run_get_reality()
    assert result["running_config"] == RUNNING_CONFIG


def test_get_reality_collected_at_is_utc_iso():
    assert _run_get_reality()["collected_at"].endswith("Z")


def test_get_reality_builds_both_interfaces():
    result = _run_get_reality()
    assert set(result["interfaces"]) == {"GigabitEthernet1/0/1", "GigabitEthernet1/0/2"}


def test_get_reality_routed_interface_has_ip_and_no_vlan():
    eth1 = _run_get_reality()["interfaces"]["GigabitEthernet1/0/1"]
    assert eth1["ip_addresses"] == ["10.0.0.1/30"]
    assert eth1["mode"] == "routed"
    assert eth1["untagged_vlan"] is None
    assert eth1["tagged_vlans"] == []


def test_get_reality_access_interface_mapped():
    eth2 = _run_get_reality()["interfaces"]["GigabitEthernet1/0/2"]
    assert eth2["mode"] == "access"
    assert eth2["untagged_vlan"] == 10
    assert eth2["ip_addresses"] == []


def test_get_reality_builds_vlans_block():
    assert _run_get_reality()["vlans"] == {
        "10": {"name": "users"},
        "20": {"name": "voice"},
    }


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
                "interface": "GigabitEthernet1/0/1",
                "adjacency_state": "full",
            },
        },
    }
