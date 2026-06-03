"""tests/test_inventory.py — device inventory resolution (list + credentials).

resolve_inventory is source-agnostic: yaml (default) or NetBox. The NetBox path
pairs the NetBox-owned device list with a service-account credential and optional
per-device overrides. All sources are injected, so no live NetBox is touched.
"""

import pytest

from netdrift import netbox_client
from netdrift.inventory import resolve_inventory


def test_yaml_source_returns_yaml_loader_output():
    fake_yaml = {"core-sw-01": {"hostname": "h1", "username": "u", "password": "p"}}
    assert resolve_inventory(source="yaml", yaml_loader=lambda: fake_yaml) == fake_yaml


def test_source_defaults_to_yaml(monkeypatch):
    monkeypatch.delenv("DEVICE_SOURCE", raising=False)
    sentinel = {"d": {"hostname": "h"}}
    assert resolve_inventory(yaml_loader=lambda: sentinel) == sentinel


def test_netbox_source_pairs_list_with_service_account(monkeypatch):
    monkeypatch.setenv("NETDRIFT_DEVICE_USERNAME", "svc")
    monkeypatch.setenv("NETDRIFT_DEVICE_PASSWORD", "secret")
    out = resolve_inventory(
        source="netbox",
        netbox_lister=lambda: [
            {"name": "core-sw-01", "hostname": "10.0.0.1"},
            {"name": "core-sw-02", "hostname": "10.0.0.2"},
        ],
        overrides_loader=lambda: {},
    )
    assert out["core-sw-01"] == {
        "hostname": "10.0.0.1", "username": "svc", "password": "secret",
    }
    assert out["core-sw-02"]["hostname"] == "10.0.0.2"


def test_netbox_source_per_device_override_wins(monkeypatch):
    monkeypatch.setenv("NETDRIFT_DEVICE_USERNAME", "svc")
    monkeypatch.setenv("NETDRIFT_DEVICE_PASSWORD", "secret")
    out = resolve_inventory(
        source="netbox",
        netbox_lister=lambda: [{"name": "core-sw-01", "hostname": "10.0.0.1"}],
        overrides_loader=lambda: {
            "core-sw-01": {"password": "override-pw", "reachability_port": 57400},
        },
    )
    dev = out["core-sw-01"]
    assert dev["username"] == "svc"           # service-account default
    assert dev["password"] == "override-pw"   # per-device override wins
    assert dev["hostname"] == "10.0.0.1"      # from NetBox
    assert dev["reachability_port"] == 57400  # extra override carried through


def test_env_selects_netbox_source(monkeypatch):
    monkeypatch.setenv("DEVICE_SOURCE", "netbox")
    monkeypatch.setenv("NETDRIFT_DEVICE_USERNAME", "svc")
    monkeypatch.setenv("NETDRIFT_DEVICE_PASSWORD", "pw")
    out = resolve_inventory(
        netbox_lister=lambda: [{"name": "d", "hostname": "1.1.1.1"}],
        overrides_loader=lambda: {},
    )
    assert out["d"]["username"] == "svc"


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="Unknown DEVICE_SOURCE"):
        resolve_inventory(source="ldap")


# --- netbox_client.list_devices with a fake nb handle (no live NetBox) -------

class _FakeIP:
    def __init__(self, address):
        self.address = address


class _FakeDevice:
    def __init__(self, name, primary_ip=None):
        self.name = name
        self.primary_ip = primary_ip


class _FakeDevices:
    def __init__(self, devices):
        self._devices = devices

    def all(self):
        return list(self._devices)

    def filter(self, **kwargs):
        return list(self._devices)


class _FakeNB:
    def __init__(self, devices):
        self.dcim = type("_dcim", (), {"devices": _FakeDevices(devices)})()


def test_list_devices_strips_cidr_and_falls_back_to_name():
    nb = _FakeNB([
        _FakeDevice("core-sw-01", _FakeIP("10.0.0.1/24")),
        _FakeDevice("core-sw-02", None),  # no primary IP -> hostname falls back
    ])
    out = netbox_client.list_devices(nb=nb)
    assert {"name": "core-sw-01", "hostname": "10.0.0.1"} in out
    assert {"name": "core-sw-02", "hostname": "core-sw-02"} in out
