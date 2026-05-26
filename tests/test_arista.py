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
    _build_ip_list,
    _build_switchport_map,
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

class FakeNapalmDevice:
    """Stands in for conn.device — the pyeapi connection NAPALM's EOS driver
    holds. arista.py calls .device.run_commands([...], encoding="json")."""

    def __init__(self, run_commands_result):
        self._result = run_commands_result

    def run_commands(self, commands, encoding=None):
        return self._result


class FakeNapalmConn:
    """Stands in for the NAPALM EOS connection object.

    Answers the three calls get_reality() makes — get_interfaces(),
    get_interfaces_ip(), and device.run_commands() — from canned payloads.
    open() / close() are no-ops; no socket is ever opened.
    """

    def __init__(self, interfaces, interfaces_ip, run_commands_result):
        self._interfaces = interfaces
        self._interfaces_ip = interfaces_ip
        self.device = FakeNapalmDevice(run_commands_result)

    def open(self):
        pass

    def close(self):
        pass

    def get_interfaces(self):
        return self._interfaces

    def get_interfaces_ip(self):
        return self._interfaces_ip


# A consistent device: Ethernet1 is a routed uplink with an IP; Ethernet2 is
# an access port on VLAN 10. VLANs 10 and 20 exist.
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

# run_commands(["show vlan", "show interfaces switchport"]) returns a list in
# the same order as the commands.
RUN_COMMANDS_RESULT = [
    {"vlans": {
        "10": {"name": "users"},
        "20": {"name": "voice"},
    }},
    {"switchports": {
        "Et2": {"switchportInfo": {"mode": "access", "accessVlanId": 10}},
    }},
]

DEVICE = {
    "name": "core-sw-01",
    "hostname": "172.20.20.11",
    "username": "admin",
    "password": "irrelevant-no-socket-opens",
}


def _run_get_reality():
    """Run get_reality() with the NAPALM driver patched out for the fake."""
    fake_conn = FakeNapalmConn(INTERFACES, INTERFACES_IP, RUN_COMMANDS_RESULT)
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
    }


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