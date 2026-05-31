"""tests/test_registry.py — collector registry + plugin contract.

Verifies the in-tree plugin architecture (collectors/base.py + registry.py):
the three bundled collectors self-register via @register, the registry exposes
them as the dispatch map and the slug map, duplicate platforms fail loud, and a
broken collector module is skipped rather than crashing the whole registry. No
lab and no installed plugin package needed — a bad module is simulated by name.
"""

import logging

import pytest

from netdrift.collectors import arista, base, cisco, nokia, registry


def test_build_collectors_returns_the_three_bundled_platforms():
    collectors = registry.build_collectors()
    assert set(collectors) == {"arista_eos", "cisco_iosxe", "nokia_srlinux"}
    # Values are the actual collector callables, not copies/wrappers.
    assert collectors["arista_eos"] is arista.get_reality
    assert collectors["cisco_iosxe"] is cisco.get_reality
    assert collectors["nokia_srlinux"] is nokia.get_reality


def test_build_platform_map_maps_every_slug():
    slug_map = registry.build_platform_map()
    assert slug_map == {
        "arista-eos": "arista_eos",
        "eos": "arista_eos",
        "cisco-ios-xe": "cisco_iosxe",
        "ios-xe": "cisco_iosxe",
        "nokia-srlinux": "nokia_srlinux",
        "srlinux": "nokia_srlinux",
    }


def test_registered_collectors_satisfy_the_collector_protocol():
    for fn in registry.build_collectors().values():
        assert isinstance(fn, base.Collector)


def test_duplicate_platform_registration_raises():
    registry.build_collectors()  # ensure arista_eos is already registered
    with pytest.raises(base.DuplicatePlatformError, match="arista_eos"):
        base.register("arista_eos")(lambda device: {})


def test_broken_collector_module_is_skipped(monkeypatch, caplog):
    # A module name that cannot be imported stands in for a broken/uninstalled
    # collector. It must be logged and skipped — the good ones still load.
    monkeypatch.setattr(
        registry,
        "COLLECTOR_MODULES",
        ("arista", "cisco", "nokia", "nonexistent_vendor"),
    )
    registry._reset()
    with caplog.at_level(logging.WARNING):
        collectors = registry.build_collectors()

    assert {"arista_eos", "cisco_iosxe", "nokia_srlinux"} <= set(collectors)
    assert "nonexistent_vendor" not in str(collectors)
    assert any("nonexistent_vendor" in r.message for r in caplog.records)

    registry._reset()  # restore the normal lazy-load for other tests
