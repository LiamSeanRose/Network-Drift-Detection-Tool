"""tests/test_netbox_client.py — get_intent transform tests.

netbox_client talks to NetBox via pynetbox; these tests inject a fake `nb` (no
socket) and stub the helper lookups so the focus is the interface/IP mapping in
get_intent — in particular that IP addresses are resolved with a single
device-wide query rather than one call per interface.
"""

import types

import pytest

from netdrift import netbox_client


class _Rec(types.SimpleNamespace):
    """A stand-in for a pynetbox record — attribute access over a dict."""


class _Endpoint:
    """Fake pynetbox endpoint recording filter/get calls."""

    def __init__(self, items=None, get_result=None):
        self._items = items or []
        self._get_result = get_result
        self.filter_calls = []

    def filter(self, **kwargs):
        self.filter_calls.append(kwargs)
        return list(self._items)

    def get(self, **kwargs):
        return self._get_result


def _make_nb(device, interfaces, ips):
    nb = types.SimpleNamespace()
    nb.dcim = types.SimpleNamespace(
        devices=_Endpoint(get_result=device),
        interfaces=_Endpoint(items=interfaces),
    )
    nb.ipam = types.SimpleNamespace(ip_addresses=_Endpoint(items=ips))
    return nb


def _stub_helpers(monkeypatch, nb):
    monkeypatch.setattr(netbox_client, "_connect", lambda: nb)
    monkeypatch.setattr(netbox_client, "_normalize_platform", lambda device: "arista_eos")
    monkeypatch.setattr(netbox_client, "_build_vlans", lambda nb, site_id: {})
    monkeypatch.setattr(netbox_client, "_fetch_rendered_config", lambda nb, device: "")
    monkeypatch.setattr(
        netbox_client,
        "_interface_vlan_fields",
        lambda iface: {"mode": "routed", "untagged_vlan": None, "tagged_vlans": []},
    )


def test_get_intent_groups_ips_by_interface_in_one_query(monkeypatch):
    device = _Rec(id=1, name="core-sw-01", site=_Rec(id=9), local_context_data={})
    interfaces = [
        _Rec(id=11, name="Ethernet1", description="uplink", enabled=True),
        _Rec(id=12, name="Ethernet2", description=None, enabled=True),  # no IPs
    ]
    # Two IPs on Ethernet1 (deliberately out of order), none on Ethernet2.
    ips = [
        _Rec(address="10.0.0.2/31", assigned_object_id=11),
        _Rec(address="10.0.0.0/31", assigned_object_id=11),
    ]
    nb = _make_nb(device, interfaces, ips)
    _stub_helpers(monkeypatch, nb)

    intent = netbox_client.get_intent("core-sw-01")

    eth1 = intent["interfaces"]["Ethernet1"]
    eth2 = intent["interfaces"]["Ethernet2"]
    assert eth1["ip_addresses"] == ["10.0.0.0/31", "10.0.0.2/31"]  # sorted
    assert eth2["ip_addresses"] == []
    assert eth2["description"] == ""  # None normalized to ""

    # The N+1 guard: IPs are fetched once for the whole device, not per interface,
    # and the one call filters by device_id.
    ip_calls = nb.ipam.ip_addresses.filter_calls
    assert len(ip_calls) == 1
    assert ip_calls[0] == {"device_id": 1}


def test_get_intent_raises_for_unknown_device(monkeypatch):
    nb = _make_nb(device=None, interfaces=[], ips=[])
    _stub_helpers(monkeypatch, nb)

    with pytest.raises(ValueError, match="not found"):
        netbox_client.get_intent("nope")
