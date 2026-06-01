"""tests/test_junos_collector.py — Juniper JunOS collector unit tests.

Tests the transform logic in collectors/junos.py without opening any socket.
FakeNapalmJunosConn stands in for NAPALM's JunOS driver, returning canned
payloads. Follows the same pattern as test_arista.py.

The device modelled here:
  ge-0/0/0 — routed uplink with /30 IP, BGP peer + OSPF adjacency to 10.0.0.2
  ge-0/0/1 — routed interface, no IP (management-adjacent, not mgmt itself)
  lo0      — loopback with /32 IP (read via lo0.0 in get_interfaces_ip)
  VLANs 10 and 20 (returned by get_vlans)
"""

from unittest.mock import patch

from netdrift.collectors.junos import (
    _build_bgp_neighbors,
    _build_ip_list,
    _build_vlans,
    _looks_like_ip,
    _parse_bgp_summary,
    _parse_ospf_neighbors,
    get_reality,
)


# ---------------------------------------------------------------------------
# _looks_like_ip
# ---------------------------------------------------------------------------

def test_looks_like_ip_valid():
    assert _looks_like_ip("10.0.0.1") is True
    assert _looks_like_ip("192.168.1.1") is True


def test_looks_like_ip_invalid():
    assert _looks_like_ip("ge-0/0/0.0") is False
    assert _looks_like_ip("Address") is False
    assert _looks_like_ip("10.0.0") is False


# ---------------------------------------------------------------------------
# _build_ip_list
# ---------------------------------------------------------------------------

def test_build_ip_list_single():
    ip_raw = {"ipv4": {"10.0.0.1": {"prefix_length": 30}}}
    assert _build_ip_list(ip_raw) == ["10.0.0.1/30"]


def test_build_ip_list_sorted():
    ip_raw = {"ipv4": {
        "10.0.0.9": {"prefix_length": 24},
        "10.0.0.1": {"prefix_length": 24},
    }}
    assert _build_ip_list(ip_raw) == ["10.0.0.1/24", "10.0.0.9/24"]


def test_build_ip_list_empty():
    assert _build_ip_list({}) == []


# ---------------------------------------------------------------------------
# _build_vlans
# ---------------------------------------------------------------------------

def test_build_vlans_basic():
    napalm_vlans = {
        "10": {"name": "users", "interfaces": ["ge-0/0/0.0"]},
        "20": {"name": "voice", "interfaces": []},
    }
    assert _build_vlans(napalm_vlans) == {
        "10": {"name": "users"},
        "20": {"name": "voice"},
    }


def test_build_vlans_missing_name():
    assert _build_vlans({"30": {}}) == {"30": {"name": ""}}


def test_build_vlans_empty():
    assert _build_vlans({}) == {}


def test_build_vlans_coerces_int_keys_to_str():
    # NAPALM may return int VLAN IDs on some JunOS versions.
    assert _build_vlans({10: {"name": "users"}}) == {"10": {"name": "users"}}


# ---------------------------------------------------------------------------
# _parse_bgp_summary
# ---------------------------------------------------------------------------

BGP_SUMMARY_ESTABLISHED = """\
Groups: 1 Peers: 1 Down peers: 0
Table          Tot Paths  Act Paths Suppressed    History Damp State    Pending
inet.0               2          2          0          0          0          0

Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
10.0.0.2             65000       1234       1234       0       0    2d 3:00:00 2/2/2/0
"""

BGP_SUMMARY_IDLE = """\
Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
10.0.0.3             65001          0          0       0       1    00:00:01 Idle
"""

BGP_SUMMARY_ACTIVE = """\
Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
10.0.0.4             65002          0          0       0       0    00:00:05 Active
"""


def test_parse_bgp_summary_established():
    states = _parse_bgp_summary(BGP_SUMMARY_ESTABLISHED)
    assert states["10.0.0.2"] == "established"


def test_parse_bgp_summary_idle():
    states = _parse_bgp_summary(BGP_SUMMARY_IDLE)
    assert states["10.0.0.3"] == "idle"


def test_parse_bgp_summary_active():
    states = _parse_bgp_summary(BGP_SUMMARY_ACTIVE)
    assert states["10.0.0.4"] == "active"


def test_parse_bgp_summary_empty():
    assert _parse_bgp_summary("") == {}


def test_parse_bgp_summary_skips_header():
    assert "Peer" not in _parse_bgp_summary(BGP_SUMMARY_ESTABLISHED)


# ---------------------------------------------------------------------------
# _build_bgp_neighbors
# ---------------------------------------------------------------------------

NAPALM_BGP = {
    "global": {
        "peers": {
            "10.0.0.2": {
                "remote_as": 65000,
                "is_enabled": True,
                "is_up": True,
                "description": "iBGP to core-sw-02",
            },
        },
    },
}


def test_build_bgp_neighbors_full():
    result = _build_bgp_neighbors(NAPALM_BGP, BGP_SUMMARY_ESTABLISHED)
    assert result["10.0.0.2"] == {
        "remote_as": 65000,
        "enabled": True,
        "description": "iBGP to core-sw-02",
        "session_state": "established",
    }


def test_build_bgp_neighbors_falls_back_to_is_up_true():
    result = _build_bgp_neighbors(NAPALM_BGP, "")  # empty summary
    assert result["10.0.0.2"]["session_state"] == "established"


def test_build_bgp_neighbors_falls_back_to_is_up_false():
    bgp_down = {
        "global": {"peers": {"10.0.0.2": {
            "remote_as": 65000, "is_enabled": True, "is_up": False, "description": "",
        }}}
    }
    result = _build_bgp_neighbors(bgp_down, "")
    assert result["10.0.0.2"]["session_state"] == "idle"


def test_build_bgp_neighbors_empty():
    assert _build_bgp_neighbors({}, "") == {}


# ---------------------------------------------------------------------------
# _parse_ospf_neighbors
# ---------------------------------------------------------------------------

OSPF_OUTPUT = """\
Address          Interface              State     ID               Pri  Dead
10.0.0.2         ge-0/0/0.0             Full      2.2.2.2          128    35
  Area 0.0.0.0, opt 0x52, DR 0.0.0.0, BDR 0.0.0.0
10.0.0.6         ge-0/0/1.0             Full      3.3.3.3          128    34
  Area 0.0.0.1, opt 0x52, DR 10.0.0.6, BDR 0.0.0.0
"""

OSPF_OUTPUT_NO_DETAIL = """\
Address          Interface              State     ID               Pri  Dead
10.0.0.2         ge-0/0/0.0             Full      2.2.2.2          128    35
"""


def test_parse_ospf_basic():
    result = _parse_ospf_neighbors(OSPF_OUTPUT)
    assert result["2.2.2.2"] == {
        "area": "0.0.0.0",
        "interface": "ge-0/0/0",
        "adjacency_state": "full",
    }


def test_parse_ospf_multiple_neighbors():
    result = _parse_ospf_neighbors(OSPF_OUTPUT)
    assert "2.2.2.2" in result
    assert "3.3.3.3" in result
    assert result["3.3.3.3"]["area"] == "0.0.0.1"


def test_parse_ospf_strips_logical_unit_from_interface():
    result = _parse_ospf_neighbors(OSPF_OUTPUT)
    assert result["2.2.2.2"]["interface"] == "ge-0/0/0"


def test_parse_ospf_state_lowercased():
    result = _parse_ospf_neighbors(OSPF_OUTPUT)
    assert result["2.2.2.2"]["adjacency_state"] == "full"


def test_parse_ospf_no_detail_line_defaults_area():
    # When there's no "Area" detail line, area defaults to "0.0.0.0".
    result = _parse_ospf_neighbors(OSPF_OUTPUT_NO_DETAIL)
    assert result["2.2.2.2"]["area"] == "0.0.0.0"


def test_parse_ospf_empty():
    assert _parse_ospf_neighbors("") == {}


def test_parse_ospf_skips_header():
    assert "Address" not in _parse_ospf_neighbors(OSPF_OUTPUT)


# ---------------------------------------------------------------------------
# get_reality — full integration via FakeNapalmJunosConn
# ---------------------------------------------------------------------------

class FakeNapalmJunosConn:
    """Stand-in for NAPALM's JunOS driver connection object."""

    def __init__(self):
        self._interfaces = {
            "ge-0/0/0": {"description": "Uplink to core", "is_enabled": True, "is_up": True},
            "ge-0/0/1": {"description": "", "is_enabled": True, "is_up": False},
            "lo0": {"description": "Loopback", "is_enabled": True, "is_up": True},
        }
        self._interfaces_ip = {
            "ge-0/0/0.0": {"ipv4": {"10.0.0.1": {"prefix_length": 30}}},
            # ge-0/0/1 has no IP
            "lo0.0": {"ipv4": {"1.1.1.1": {"prefix_length": 32}}},
        }
        self._bgp_neighbors = {
            "global": {"peers": {
                "10.0.0.2": {
                    "remote_as": 65000,
                    "is_enabled": True,
                    "is_up": True,
                    "description": "iBGP to core-sw-02",
                },
            }},
        }
        self._vlans = {
            "10": {"name": "users", "interfaces": []},
            "20": {"name": "voice", "interfaces": []},
        }
        self._cli_outputs = {
            "show bgp summary": BGP_SUMMARY_ESTABLISHED,
            "show ospf neighbor": OSPF_OUTPUT,
        }
        self._running_config = "set system host-name junos-sw-01"

    def open(self):
        pass

    def close(self):
        pass

    def get_interfaces(self):
        return self._interfaces

    def get_interfaces_ip(self):
        return self._interfaces_ip

    def get_bgp_neighbors(self):
        return self._bgp_neighbors

    def get_vlans(self):
        return self._vlans

    def cli(self, commands):
        return {cmd: self._cli_outputs.get(cmd, "") for cmd in commands}

    def get_config(self, retrieve="running"):
        return {"running": self._running_config, "startup": "", "candidate": ""}


DEVICE = {
    "name": "junos-sw-01",
    "hostname": "172.20.20.31",
    "username": "admin",
    "password": "JunosPw1!",
}


def _fake_conn_factory(fake):
    """Return a fake NAPALM driver callable that yields `fake` on instantiation."""
    class FakeDriver:
        def __init__(self, hostname, username, password, optional_args=None):
            pass
        def open(self):
            pass
        def close(self):
            pass
        def get_interfaces(self):
            return fake.get_interfaces()
        def get_interfaces_ip(self):
            return fake.get_interfaces_ip()
        def get_bgp_neighbors(self):
            return fake.get_bgp_neighbors()
        def get_vlans(self):
            return fake.get_vlans()
        def cli(self, commands):
            return fake.cli(commands)
        def get_config(self, retrieve="running"):
            return fake.get_config(retrieve=retrieve)
    return FakeDriver


def test_get_reality_platform():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert result["platform"] == "juniper_junos"
    assert result["device"] == "junos-sw-01"


def test_get_reality_interfaces_present():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert "ge-0/0/0" in result["interfaces"]
    assert "ge-0/0/1" in result["interfaces"]
    assert "lo0" in result["interfaces"]


def test_get_reality_ip_lookup_via_logical_unit():
    # ge-0/0/0's IP lives under ge-0/0/0.0 in get_interfaces_ip().
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert result["interfaces"]["ge-0/0/0"]["ip_addresses"] == ["10.0.0.1/30"]


def test_get_reality_loopback_ip_via_lo0_unit():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert result["interfaces"]["lo0"]["ip_addresses"] == ["1.1.1.1/32"]


def test_get_reality_interface_no_ip_is_empty_list():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert result["interfaces"]["ge-0/0/1"]["ip_addresses"] == []


def test_get_reality_all_interfaces_mode_routed():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    for iface_data in result["interfaces"].values():
        assert iface_data["mode"] == "routed"
        assert iface_data["untagged_vlan"] is None
        assert iface_data["tagged_vlans"] == []


def test_get_reality_vlans():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert result["vlans"] == {"10": {"name": "users"}, "20": {"name": "voice"}}


def test_get_reality_bgp_neighbor():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    neighbor = result["bgp_neighbors"]["10.0.0.2"]
    assert neighbor["remote_as"] == 65000
    assert neighbor["session_state"] == "established"
    assert neighbor["enabled"] is True


def test_get_reality_ospf_adjacency():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    adj = result["ospf"]["adjacencies"]["2.2.2.2"]
    assert adj["area"] == "0.0.0.0"
    assert adj["interface"] == "ge-0/0/0"
    assert adj["adjacency_state"] == "full"


def test_get_reality_running_config_present():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert "host-name" in result["running_config"]


def test_get_reality_collected_at_utc_z():
    fake = FakeNapalmJunosConn()
    with patch("netdrift.collectors.junos.get_network_driver",
               return_value=_fake_conn_factory(fake)):
        result = get_reality(DEVICE)
    assert result["collected_at"].endswith("Z")
