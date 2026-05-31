"""tests/test_nokia.py — Nokia SR Linux collector unit tests (v0.3).

The collector (collectors/nokia.py) does two things: it talks gNMI to a live
SR Linux node, and it transforms the raw gNMI response into the normalized
schema (docs/schema.md Section 2). These tests cover the transform, never the
network — exactly like test_differ.py and test_storage.py, so they run fast
in CI with no lab node up.

The pure helpers are tested directly with canned gNMI dicts. get_reality() is
tested with a fake gNMI client (FakeGNMIClient) standing in for pygnmi's
gNMIclient — it is a context manager whose .get() returns a canned response
chosen by the requested path. That lets the whole get_reality() loop run with
no socket opened.

CAVEAT (v0.3, Path A): the BGP/OSPF gNMI response shapes used in these
fixtures are modelled on the SR Linux YANG spec, not captured from a live
node running routing. The collector is verified end-to-end on the
"no routing configured" path (which is the current lab state). The populated
fixtures here exercise the parser logic but their precise field structure
may need a tweak when routing is later configured on the Nokia and live
data is captured.
"""

from unittest.mock import patch

from netdrift.collectors.nokia import (
    _build_bgp_neighbors,
    _build_ip_list,
    _build_macvrf_map,
    _build_ospf_adjacencies,
    _gnmi_first_val,
    _is_bridged,
    _normalize_area,
    _parse_interface,
    _vlan_id_from_subinterface,
    get_reality,
)


# --- gNMI response shapes ----------------------------------------------------
# pygnmi's get() returns {"notification": [{"update": [{"val": <payload>}]}]}.
# These helpers build that envelope so tests can focus on the payload.

def gnmi_response(payload):
    """Wrap a payload in the pygnmi get() notification/update/val envelope."""
    return {"notification": [{"update": [{"val": payload}]}]}


# A subinterface SR Linux reports as a bridged, single-tagged L2 subinterface
# on VLAN 10 — the shape an access port produces.
BRIDGED_SUBIF_VLAN10 = {
    "name": "ethernet-1/1.0",
    "type": "srl_nokia-interfaces:bridged",
    "srl_nokia-interfaces-vlans:vlan": {
        "encap": {"single-tagged": {"vlan-id": 10}}
    },
}

# A routed subinterface carrying an IP — no VLAN encap.
ROUTED_SUBIF_WITH_IP = {
    "name": "mgmt0.0",
    "type": "srl_nokia-interfaces:routed",
    "ipv4": {"address": [{"ip-prefix": "172.20.20.21/24"}]},
}


# --- _gnmi_first_val ---------------------------------------------------------

def test_gnmi_first_val_extracts_payload():
    assert _gnmi_first_val(gnmi_response({"hello": "world"})) == {"hello": "world"}


def test_gnmi_first_val_empty_notification_is_none():
    assert _gnmi_first_val({"notification": []}) is None


def test_gnmi_first_val_missing_update_is_none():
    assert _gnmi_first_val({"notification": [{}]}) is None


# --- _build_ip_list ----------------------------------------------------------

def test_build_ip_list_returns_sorted_cidrs():
    subif = {"ipv4": {"address": [
        {"ip-prefix": "10.0.0.2/24"},
        {"ip-prefix": "10.0.0.1/24"},
    ]}}
    assert _build_ip_list(subif) == ["10.0.0.1/24", "10.0.0.2/24"]


def test_build_ip_list_no_ipv4_is_empty():
    # A subinterface with no ipv4 key at all -> empty list, not an error.
    assert _build_ip_list({}) == []


def test_build_ip_list_skips_address_without_prefix():
    # An address dict missing "ip-prefix" is skipped, not crashed on.
    subif = {"ipv4": {"address": [{"ip-prefix": "10.0.0.1/24"}, {}]}}
    assert _build_ip_list(subif) == ["10.0.0.1/24"]


# --- _vlan_id_from_subinterface ----------------------------------------------

def test_vlan_id_from_single_tagged_subif():
    assert _vlan_id_from_subinterface(BRIDGED_SUBIF_VLAN10) == 10


def test_vlan_id_absent_when_no_vlan_encap():
    # A routed subinterface has no vlan key -> None.
    assert _vlan_id_from_subinterface(ROUTED_SUBIF_WITH_IP) is None


# --- _is_bridged -------------------------------------------------------------

def test_is_bridged_true_for_bridged_type():
    assert _is_bridged(BRIDGED_SUBIF_VLAN10) is True


def test_is_bridged_false_for_routed_type():
    assert _is_bridged(ROUTED_SUBIF_WITH_IP) is False


def test_is_bridged_false_when_type_missing():
    assert _is_bridged({}) is False


# --- _normalize_area ---------------------------------------------------------

def test_normalize_area_int_zero_becomes_dotted():
    # SR Linux may return area as an int 0 — schema Rule 10 wants dotted.
    assert _normalize_area(0) == "0.0.0.0"


def test_normalize_area_string_int_becomes_dotted():
    # Or as the string "0".
    assert _normalize_area("0") == "0.0.0.0"


def test_normalize_area_already_dotted_passes_through():
    assert _normalize_area("0.0.0.1") == "0.0.0.1"


def test_normalize_area_empty_is_empty():
    assert _normalize_area("") == ""
    assert _normalize_area(None) == ""


# --- _build_macvrf_map -------------------------------------------------------

def test_build_macvrf_map_pairs_subif_to_instance():
    # A mac-vrf instance named "mac-vrf-10" with one bound subinterface.
    ni_payload = {"srl_nokia-network-instance:network-instance": [
        {
            "name": "mac-vrf-10",
            "type": "srl_nokia-network-instance:mac-vrf",
            "interface": [{"name": "ethernet-1/1.0"}],
        },
    ]}
    fake = FakeGNMIClient(network_instance=ni_payload)
    assert _build_macvrf_map(fake) == {"ethernet-1/1.0": "mac-vrf-10"}


def test_build_macvrf_map_ignores_non_macvrf_instances():
    # A default (ip-vrf) network-instance must not appear in the map.
    ni_payload = {"srl_nokia-network-instance:network-instance": [
        {
            "name": "default",
            "type": "srl_nokia-network-instance:ip-vrf",
            "interface": [{"name": "mgmt0.0"}],
        },
    ]}
    fake = FakeGNMIClient(network_instance=ni_payload)
    assert _build_macvrf_map(fake) == {}


# --- _parse_interface --------------------------------------------------------

def test_parse_interface_bridged_subif_is_access_mode():
    iface = {
        "name": "ethernet-1/1",
        "description": "Access port",
        "admin-state": "enable",
        "subinterface": [BRIDGED_SUBIF_VLAN10],
    }
    result, vlan_id = _parse_interface(iface)
    assert result["mode"] == "access"
    assert result["untagged_vlan"] == 10
    assert result["tagged_vlans"] == []
    assert result["enabled"] is True
    assert vlan_id == 10


def test_parse_interface_no_bridged_subif_is_routed():
    iface = {
        "name": "mgmt0",
        "admin-state": "enable",
        "subinterface": [ROUTED_SUBIF_WITH_IP],
    }
    result, vlan_id = _parse_interface(iface)
    assert result["mode"] == "routed"
    assert result["untagged_vlan"] is None
    assert result["ip_addresses"] == ["172.20.20.21/24"]
    assert vlan_id is None


def test_parse_interface_admin_state_down_is_disabled():
    # Anything other than "enable" -> enabled is False.
    iface = {"name": "ethernet-1/2", "admin-state": "disable", "subinterface": []}
    result, _ = _parse_interface(iface)
    assert result["enabled"] is False


def test_parse_interface_missing_description_is_empty_string():
    # schema Rule 4: unset description is "", never None.
    iface = {"name": "ethernet-1/3", "admin-state": "enable", "subinterface": []}
    result, _ = _parse_interface(iface)
    assert result["description"] == ""


# --- _build_bgp_neighbors ----------------------------------------------------

def test_build_bgp_neighbors_empty_when_unconfigured():
    # No BGP configured -> gNMI returns empty notification -> empty dict.
    fake = FakeGNMIClient()
    assert _build_bgp_neighbors(fake) == {}


def test_build_bgp_neighbors_parses_neighbor_list():
    bgp_payload = {"srl_nokia-bgp:neighbor": [
        {
            "peer-address": "10.0.0.1",
            "peer-as": 65000,
            "admin-state": "enable",
            "description": "iBGP to core-sw-01",
            "session-state": "established",
        },
    ]}
    fake = FakeGNMIClient(bgp=bgp_payload)
    assert _build_bgp_neighbors(fake) == {
        "10.0.0.1": {
            "remote_as": 65000,
            "enabled": True,
            "description": "iBGP to core-sw-01",
            "session_state": "established",
        },
    }


def test_build_bgp_neighbors_lowercases_state():
    # Defensive — even though SR Linux already lower-cases, the collector
    # should not assume it. A non-lower input must still come out lower.
    bgp_payload = {"srl_nokia-bgp:neighbor": [
        {
            "peer-address": "10.0.0.1",
            "peer-as": 65000,
            "admin-state": "enable",
            "session-state": "Established",
        },
    ]}
    assert _build_bgp_neighbors(
        FakeGNMIClient(bgp=bgp_payload)
    )["10.0.0.1"]["session_state"] == "established"


def test_build_bgp_neighbors_missing_description_is_empty_string():
    # schema Rule 4: "" not None, even when SR Linux omits the key entirely.
    bgp_payload = {"srl_nokia-bgp:neighbor": [
        {
            "peer-address": "10.0.0.1",
            "peer-as": 65000,
            "admin-state": "enable",
            "session-state": "established",
        },
    ]}
    assert _build_bgp_neighbors(
        FakeGNMIClient(bgp=bgp_payload)
    )["10.0.0.1"]["description"] == ""


def test_build_bgp_neighbors_admin_state_disable_is_false():
    bgp_payload = {"srl_nokia-bgp:neighbor": [
        {
            "peer-address": "10.0.0.1",
            "peer-as": 65000,
            "admin-state": "disable",
            "session-state": "idle",
        },
    ]}
    assert _build_bgp_neighbors(
        FakeGNMIClient(bgp=bgp_payload)
    )["10.0.0.1"]["enabled"] is False


# --- _build_ospf_adjacencies -------------------------------------------------

def test_build_ospf_adjacencies_empty_when_unconfigured():
    fake = FakeGNMIClient()
    assert _build_ospf_adjacencies(fake) == {}


def test_build_ospf_adjacencies_parses_neighbor():
    ospf_payload = {"srl_nokia-ospf:instance": [
        {
            "name": "default",
            "area": [
                {
                    "area-id": "0.0.0.0",
                    "interface": [
                        {
                            "interface-name": "ethernet-1/1.0",
                            "neighbor": [
                                {
                                    "neighbor-router-id": "1.1.1.1",
                                    "adjacency-state": "full",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    ]}
    fake = FakeGNMIClient(ospf=ospf_payload)
    assert _build_ospf_adjacencies(fake) == {
        "1.1.1.1": {
            "area": "0.0.0.0",
            "interface": "ethernet-1/1.0",
            "adjacency_state": "full",
        },
    }


def test_build_ospf_adjacencies_normalizes_int_area():
    # SR Linux may emit area-id as an int — collector must normalize to dotted.
    ospf_payload = {"srl_nokia-ospf:instance": [
        {
            "name": "default",
            "area": [
                {
                    "area-id": 0,
                    "interface": [
                        {
                            "interface-name": "ethernet-1/1.0",
                            "neighbor": [
                                {
                                    "neighbor-router-id": "1.1.1.1",
                                    "adjacency-state": "full",
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    ]}
    assert _build_ospf_adjacencies(
        FakeGNMIClient(ospf=ospf_payload)
    )["1.1.1.1"]["area"] == "0.0.0.0"


def test_build_ospf_adjacencies_merges_multiple_instances():
    # schema does not model OSPF process IDs — adjacencies from multiple
    # instances merge into one dict keyed by router-id.
    ospf_payload = {"srl_nokia-ospf:instance": [
        {
            "name": "default", "area": [{
                "area-id": "0.0.0.0", "interface": [{
                    "interface-name": "ethernet-1/1.0", "neighbor": [
                        {"neighbor-router-id": "1.1.1.1", "adjacency-state": "full"},
                    ],
                }],
            }],
        },
        {
            "name": "second", "area": [{
                "area-id": "0.0.0.1", "interface": [{
                    "interface-name": "ethernet-1/2.0", "neighbor": [
                        {"neighbor-router-id": "3.3.3.3", "adjacency-state": "full"},
                    ],
                }],
            }],
        },
    ]}
    result = _build_ospf_adjacencies(FakeGNMIClient(ospf=ospf_payload))
    assert set(result.keys()) == {"1.1.1.1", "3.3.3.3"}


def test_build_ospf_adjacencies_skips_neighbor_without_router_id():
    # A malformed entry must be skipped, not crash the parse.
    ospf_payload = {"srl_nokia-ospf:instance": [
        {
            "name": "default", "area": [{
                "area-id": "0.0.0.0", "interface": [{
                    "interface-name": "ethernet-1/1.0", "neighbor": [
                        {"adjacency-state": "full"},
                    ],
                }],
            }],
        },
    ]}
    assert _build_ospf_adjacencies(FakeGNMIClient(ospf=ospf_payload)) == {}


# --- get_reality (gNMI client mocked) ----------------------------------------

class FakeGNMIClient:
    """Stand-in for pygnmi's gNMIclient — a context manager whose .get()
    returns a canned response chosen by the requested path.

    nokia.py calls gc.get() for "/interface", "/network-instance",
    ".../protocols/bgp/neighbor", and ".../protocols/ospfv2/instance"; this
    fake answers each from a payload handed in at construction. Anything else
    returns an empty notification, the same as a real node with no data.
    """

    def __init__(self, interface=None, network_instance=None, bgp=None, ospf=None):
        self._interface = interface
        self._network_instance = network_instance
        self._bgp = bgp
        self._ospf = ospf

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path=None, datatype=None):
        key = path[0] if path else ""
        if key == "/interface" and self._interface is not None:
            return gnmi_response(self._interface)
        if key == "/network-instance" and self._network_instance is not None:
            return gnmi_response(self._network_instance)
        if "bgp/neighbor" in key and self._bgp is not None:
            return gnmi_response(self._bgp)
        if "ospf/instance" in key and self._ospf is not None:
            return gnmi_response(self._ospf)
        return {"notification": []}


# A complete, internally consistent device: ethernet-1/1 is an access port on
# VLAN 10 bound into the "mac-vrf-10" instance; mgmt0 is routed with an IP.
# One BGP neighbor to 10.0.0.1, one OSPF adjacency to 1.1.1.1.
INTERFACE_PAYLOAD = {"srl_nokia-interfaces:interface": [
    {
        "name": "ethernet-1/1",
        "description": "Access port - users",
        "admin-state": "enable",
        "subinterface": [BRIDGED_SUBIF_VLAN10],
    },
    {
        "name": "mgmt0",
        "description": "",
        "admin-state": "enable",
        "subinterface": [ROUTED_SUBIF_WITH_IP],
    },
]}

NETWORK_INSTANCE_PAYLOAD = {"srl_nokia-network-instance:network-instance": [
    {
        "name": "mac-vrf-10",
        "type": "srl_nokia-network-instance:mac-vrf",
        "interface": [{"name": "ethernet-1/1.0"}],
    },
]}

BGP_PAYLOAD = {"srl_nokia-bgp:neighbor": [
    {
        "peer-address": "10.0.0.1",
        "peer-as": 65000,
        "admin-state": "enable",
        "description": "iBGP to core-sw-01",
        "session-state": "established",
    },
]}

OSPF_PAYLOAD = {"srl_nokia-ospf:instance": [
    {
        "name": "default",
        "area": [
            {
                "area-id": "0.0.0.0",
                "interface": [
                    {
                        "interface-name": "ethernet-1/1.0",
                        "neighbor": [
                            {
                                "neighbor-router-id": "1.1.1.1",
                                "adjacency-state": "full",
                            },
                        ],
                    },
                ],
            },
        ],
    },
]}

DEVICE = {
    "name": "nokia-sw-01",
    "hostname": "172.20.20.21",
    "username": "admin",
    "password": "irrelevant-no-socket-opens",
}


def _run_get_reality():
    """Run get_reality() with the gNMI client patched out for the fake."""
    fake = FakeGNMIClient(
        interface=INTERFACE_PAYLOAD,
        network_instance=NETWORK_INSTANCE_PAYLOAD,
        bgp=BGP_PAYLOAD,
        ospf=OSPF_PAYLOAD,
    )
    # nokia.py does `with gNMIclient(...) as gc:` — patch the name it imported
    # so constructing it returns our fake instead of opening a connection.
    with patch("netdrift.collectors.nokia.gNMIclient", return_value=fake):
        return get_reality(DEVICE)


def test_get_reality_top_level_shape():
    result = _run_get_reality()
    assert result["device"] == "nokia-sw-01"
    assert result["platform"] == "nokia_srlinux"
    assert set(result.keys()) == {
        "device", "platform", "collected_at", "interfaces", "vlans",
        "bgp_neighbors", "ospf", "running_config",
    }


def test_get_reality_running_config_is_empty_by_design():
    # SR Linux exposes no text running-config that would match a NetBox
    # text-rendered intent; the collector returns "" so the differ skips the
    # config diff (schema allows "" when unavailable).
    result = _run_get_reality()
    assert result["running_config"] == ""


def test_get_reality_collected_at_is_utc_iso():
    # schema Rule 2: ISO 8601 UTC with a Z suffix.
    result = _run_get_reality()
    assert result["collected_at"].endswith("Z")


def test_get_reality_builds_both_interfaces():
    result = _run_get_reality()
    assert set(result["interfaces"]) == {"ethernet-1/1", "mgmt0"}


def test_get_reality_access_interface_mapped():
    result = _run_get_reality()
    eth1 = result["interfaces"]["ethernet-1/1"]
    assert eth1["mode"] == "access"
    assert eth1["untagged_vlan"] == 10


def test_get_reality_routed_interface_mapped():
    result = _run_get_reality()
    mgmt = result["interfaces"]["mgmt0"]
    assert mgmt["mode"] == "routed"
    assert mgmt["ip_addresses"] == ["172.20.20.21/24"]


def test_get_reality_vlan_name_comes_from_macvrf():
    # The cross-wiring test: VLAN 10's name is the mac-vrf instance the access
    # port's subinterface is bound to — joined across two separate gNMI calls.
    result = _run_get_reality()
    assert result["vlans"] == {"10": {"name": "mac-vrf-10"}}


def test_get_reality_builds_bgp_neighbors_block():
    result = _run_get_reality()
    assert result["bgp_neighbors"] == {
        "10.0.0.1": {
            "remote_as": 65000,
            "enabled": True,
            "description": "iBGP to core-sw-01",
            "session_state": "established",
        },
    }


def test_get_reality_builds_ospf_block():
    result = _run_get_reality()
    assert result["ospf"] == {
        "adjacencies": {
            "1.1.1.1": {
                "area": "0.0.0.0",
                "interface": "ethernet-1/1.0",
                "adjacency_state": "full",
            },
        },
    }
