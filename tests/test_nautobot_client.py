"""tests/test_nautobot_client.py — Nautobot intent client unit tests (v0.3).

Mirrors test structure of the collector tests: all pynautobot calls are
replaced by fake objects — no live Nautobot instance needed. Tests verify
that get_intent() returns the correct normalized schema shape and that the
key Nautobot-specific behaviour is correct (local_config_context for routing
intent, platform slug mapping, VLAN scoping to site).

NOTE: field names on the fake objects match what pynautobot v1.x returns
against a real Nautobot. If a name is wrong it will surface as an
AttributeError when running against a live instance — fix it here, not in
the schema.
"""

import pytest
from unittest.mock import patch

from netdrift.nautobot_client import (
    _build_routing_from_context,
    _build_vlans,
    _interface_vlan_fields,
    _normalize_platform,
    get_intent,
)


# ---------------------------------------------------------------------------
# Fake pynautobot objects
# ---------------------------------------------------------------------------

class FakePlatform:
    def __init__(self, slug):
        self.slug = slug


class FakeSite:
    def __init__(self, id):
        self.id = id


class FakeVlan:
    def __init__(self, vid, name=""):
        self.vid = vid
        self.name = name


class FakeIP:
    def __init__(self, address):
        self.address = address


class FakeInterface:
    def __init__(self, id, name, description="", enabled=True,
                 mode=None, untagged_vlan=None, tagged_vlans=None):
        self.id = id
        self.name = name
        self.description = description
        self.enabled = enabled
        self.mode = mode
        self.untagged_vlan = untagged_vlan
        self.tagged_vlans = tagged_vlans or []


class FakeDevice:
    def __init__(self, name, platform_slug="arista-eos", site_id=1,
                 local_config_context=None):
        self.name = name
        self.platform = FakePlatform(platform_slug)
        self.site = FakeSite(site_id)
        # Nautobot v1.x uses local_config_context, not local_context_data.
        self.local_config_context = local_config_context or {}
        self.id = 1


class FakeNautobotAPI:
    """Stand-in for the pynautobot API handle.

    get_intent() calls: nb.dcim.devices.get, nb.dcim.interfaces.filter,
    nb.ipam.ip_addresses.filter, nb.ipam.vlans.filter.
    Each is backed by a simple list that .get()/.filter() searches.
    """

    def __init__(self, device, interfaces=None, ips=None, vlans=None):
        self._device = device
        self._interfaces = interfaces or []
        self._ips = ips or []
        self._vlans = vlans or []
        self.dcim = self._DCIMEndpoint(device, self._interfaces)
        self.ipam = self._IPAMEndpoint(self._ips, self._vlans)

    class _DCIMEndpoint:
        def __init__(self, device, interfaces):
            self.devices = self._DeviceEndpoint(device)
            self.interfaces = self._InterfaceEndpoint(interfaces)

        class _DeviceEndpoint:
            def __init__(self, device):
                self._device = device

            def get(self, name=None):
                if self._device and self._device.name == name:
                    return self._device
                return None

        class _InterfaceEndpoint:
            def __init__(self, interfaces):
                self._interfaces = interfaces

            def filter(self, device_id=None):
                return self._interfaces

    class _IPAMEndpoint:
        def __init__(self, ips, vlans):
            self._ips = ips
            self._vlans = vlans
            self.ip_addresses = self._IPEndpoint(ips)
            self.vlans = self._VLANEndpoint(vlans)

        class _IPEndpoint:
            def __init__(self, ips):
                self._ips = ips

            def filter(self, interface_id=None):
                return self._ips

        class _VLANEndpoint:
            def __init__(self, vlans):
                self._vlans = vlans

            def filter(self, site_id=None):
                return self._vlans


def _run_get_intent(device, interfaces=None, ips=None, vlans=None):
    """Run get_intent() with pynautobot.api patched to return a fake."""
    fake_api = FakeNautobotAPI(device, interfaces=interfaces, ips=ips, vlans=vlans)
    with patch("netdrift.nautobot_client._connect", return_value=fake_api):
        return get_intent(device.name)


# ---------------------------------------------------------------------------
# _normalize_platform
# ---------------------------------------------------------------------------

def test_normalize_platform_arista_slug():
    device = FakeDevice("sw-01", platform_slug="arista-eos")
    assert _normalize_platform(device) == "arista_eos"


def test_normalize_platform_nokia_slug():
    device = FakeDevice("sw-01", platform_slug="nokia-srlinux")
    assert _normalize_platform(device) == "nokia_srlinux"


def test_normalize_platform_no_platform_raises():
    device = FakeDevice("sw-01")
    device.platform = None
    with pytest.raises(ValueError, match="no platform set"):
        _normalize_platform(device)


def test_normalize_platform_unknown_slug_raises():
    device = FakeDevice("sw-01", platform_slug="cisco-iosxe")
    with pytest.raises(ValueError, match="unknown platform slug"):
        _normalize_platform(device)


# ---------------------------------------------------------------------------
# _interface_vlan_fields
# ---------------------------------------------------------------------------

def test_interface_vlan_fields_routed_when_mode_none():
    iface = FakeInterface(1, "Ethernet1", mode=None)
    result = _interface_vlan_fields(iface)
    assert result["mode"] == "routed"
    assert result["untagged_vlan"] is None
    assert result["tagged_vlans"] == []


def test_interface_vlan_fields_access_mode():
    iface = FakeInterface(2, "Ethernet2", mode="access",
                          untagged_vlan=FakeVlan(10))
    result = _interface_vlan_fields(iface)
    assert result["mode"] == "access"
    assert result["untagged_vlan"] == 10


def test_interface_vlan_fields_tagged_mode():
    iface = FakeInterface(3, "Ethernet3", mode="tagged",
                          tagged_vlans=[FakeVlan(20), FakeVlan(10)])
    result = _interface_vlan_fields(iface)
    assert result["mode"] == "tagged"
    # schema Rule 3: tagged_vlans sorted ascending.
    assert result["tagged_vlans"] == [10, 20]


# ---------------------------------------------------------------------------
# _build_vlans
# ---------------------------------------------------------------------------

def test_build_vlans_returns_string_keyed_dict():
    fake_api = FakeNautobotAPI(
        FakeDevice("sw-01"),
        vlans=[FakeVlan(10, "users"), FakeVlan(20, "voice")],
    )
    result = _build_vlans(fake_api, site_id=1)
    assert result == {"10": {"name": "users"}, "20": {"name": "voice"}}


def test_build_vlans_empty_name_is_empty_string():
    fake_api = FakeNautobotAPI(FakeDevice("sw-01"), vlans=[FakeVlan(30, "")])
    result = _build_vlans(fake_api, site_id=1)
    assert result["30"]["name"] == ""


# ---------------------------------------------------------------------------
# _build_routing_from_context
# ---------------------------------------------------------------------------

def test_build_routing_reads_local_config_context():
    # Key Nautobot difference: field is local_config_context, not local_context_data.
    device = FakeDevice("sw-01", local_config_context={
        "bgp_neighbors": {
            "10.0.0.2": {
                "remote_as": 65000,
                "enabled": True,
                "description": "iBGP peer",
                "session_state": "established",
            },
        },
        "ospf": {"adjacencies": {}},
    })
    bgp, ospf = _build_routing_from_context(device)
    assert "10.0.0.2" in bgp
    assert bgp["10.0.0.2"]["remote_as"] == 65000


def test_build_routing_empty_context_yields_empty_containers():
    device = FakeDevice("sw-01", local_config_context={})
    bgp, ospf = _build_routing_from_context(device)
    assert bgp == {}
    assert ospf == {"adjacencies": {}}


def test_build_routing_none_context_yields_empty_containers():
    device = FakeDevice("sw-01")
    device.local_config_context = None
    bgp, ospf = _build_routing_from_context(device)
    assert bgp == {}
    assert ospf == {"adjacencies": {}}


# ---------------------------------------------------------------------------
# get_intent — top-level shape and field checks
# ---------------------------------------------------------------------------

def test_get_intent_top_level_shape():
    device = FakeDevice("core-sw-01")
    result = _run_get_intent(device)
    assert result["device"] == "core-sw-01"
    assert result["platform"] == "arista_eos"
    assert set(result.keys()) == {
        "device", "platform", "collected_at", "interfaces", "vlans",
        "bgp_neighbors", "ospf",
    }


def test_get_intent_collected_at_is_utc_iso():
    result = _run_get_intent(FakeDevice("core-sw-01"))
    assert result["collected_at"].endswith("Z")


def test_get_intent_raises_when_device_not_found():
    # _connect() returns a fake whose .dcim.devices.get() returns None.
    fake_api = FakeNautobotAPI(FakeDevice("other-device"))
    with patch("netdrift.nautobot_client._connect", return_value=fake_api):
        with pytest.raises(ValueError, match="not found in Nautobot"):
            get_intent("nonexistent")


def test_get_intent_builds_interfaces():
    device = FakeDevice("core-sw-01")
    interfaces = [
        FakeInterface(1, "Ethernet1", description="Uplink", enabled=True,
                      mode=None),
        FakeInterface(2, "Ethernet2", enabled=True, mode="access",
                      untagged_vlan=FakeVlan(10)),
    ]
    ips = [FakeIP("10.0.0.1/30")]
    result = _run_get_intent(device, interfaces=interfaces, ips=ips)
    assert "Ethernet1" in result["interfaces"]
    assert "Ethernet2" in result["interfaces"]


def test_get_intent_routed_interface_fields():
    device = FakeDevice("core-sw-01")
    iface = FakeInterface(1, "Ethernet1", description="Uplink", enabled=True)
    result = _run_get_intent(device, interfaces=[iface], ips=[FakeIP("10.0.0.1/30")])
    eth1 = result["interfaces"]["Ethernet1"]
    assert eth1["description"] == "Uplink"
    assert eth1["enabled"] is True
    assert eth1["ip_addresses"] == ["10.0.0.1/30"]
    assert eth1["mode"] == "routed"
    assert eth1["untagged_vlan"] is None
    assert eth1["tagged_vlans"] == []


def test_get_intent_access_interface_fields():
    device = FakeDevice("core-sw-01")
    iface = FakeInterface(2, "Ethernet2", enabled=True, mode="access",
                          untagged_vlan=FakeVlan(10))
    result = _run_get_intent(device, interfaces=[iface], ips=[])
    eth2 = result["interfaces"]["Ethernet2"]
    assert eth2["mode"] == "access"
    assert eth2["untagged_vlan"] == 10
    assert eth2["ip_addresses"] == []


def test_get_intent_missing_description_is_empty_string():
    # schema Rule 4: description is always "" not None.
    device = FakeDevice("core-sw-01")
    iface = FakeInterface(1, "Ethernet1", description="")
    result = _run_get_intent(device, interfaces=[iface])
    assert result["interfaces"]["Ethernet1"]["description"] == ""


def test_get_intent_includes_routing_from_local_config_context():
    device = FakeDevice("core-sw-01", local_config_context={
        "bgp_neighbors": {
            "10.0.0.2": {
                "remote_as": 65000,
                "enabled": True,
                "description": "iBGP to core-sw-02",
                "session_state": "established",
            },
        },
        "ospf": {
            "adjacencies": {
                "2.2.2.2": {
                    "area": "0.0.0.0",
                    "interface": "Ethernet1",
                    "adjacency_state": "full",
                },
            },
        },
    })
    result = _run_get_intent(device)
    assert result["bgp_neighbors"] == {
        "10.0.0.2": {
            "remote_as": 65000,
            "enabled": True,
            "description": "iBGP to core-sw-02",
            "session_state": "established",
        },
    }
    assert result["ospf"]["adjacencies"]["2.2.2.2"]["area"] == "0.0.0.0"


def test_get_intent_no_routing_yields_empty_containers():
    result = _run_get_intent(FakeDevice("core-sw-01"))
    assert result["bgp_neighbors"] == {}
    assert result["ospf"] == {"adjacencies": {}}
