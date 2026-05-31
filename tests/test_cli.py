"""tests/test_cli.py — driftcheck CLI dispatch (v1.0).

Exercises cli.main()'s collector dispatch through the registry-backed COLLECTORS
table and the new `collectors=` injection seam. load_devices and the intent
function are patched, so no devices.yml, NetBox, or device is touched.
"""

import pytest

from netdrift import cli


def _state(platform):
    """A schema-complete device-state dict (empty everywhere) for a platform."""
    return {
        "device": "sw",
        "platform": platform,
        "collected_at": "2026-05-31T00:00:00Z",
        "interfaces": {},
        "vlans": {},
        "bgp_neighbors": {},
        "ospf": {"adjacencies": {}},
        "running_config": "",
    }


@pytest.fixture
def fake_inventory(monkeypatch):
    """Patch out devices.yml so main() finds one device 'sw'."""
    monkeypatch.setattr(
        cli, "load_devices",
        lambda: {"sw": {"hostname": "h", "username": "u", "password": "p"}},
    )


def test_default_collectors_come_from_registry():
    # Wiring proof: cli's dispatch table is the registry, not a hand-built dict.
    assert {"arista_eos", "cisco_iosxe", "nokia_srlinux"} <= set(cli.COLLECTORS)


def test_main_dispatches_to_injected_collector(fake_inventory, monkeypatch, capsys):
    intent = _state("new_vendor")
    monkeypatch.setattr(cli, "_resolve_intent_fn", lambda: (lambda name: intent))

    called = {}

    def fake_collector(device):
        called["device"] = device
        return _state("new_vendor")  # reality == intent -> no drift

    cli.main(argv=["sw"], collectors={"new_vendor": fake_collector})

    # A vendor with no core edit dispatched correctly via the injected table.
    assert called["device"]["name"] == "sw"
    assert "no drift" in capsys.readouterr().out


def test_main_exits_on_unknown_platform(fake_inventory, monkeypatch):
    intent = _state("mystery_platform")
    monkeypatch.setattr(cli, "_resolve_intent_fn", lambda: (lambda name: intent))

    with pytest.raises(SystemExit) as exc:
        cli.main(argv=["sw"], collectors={"arista_eos": lambda d: intent})

    assert "no collector for platform 'mystery_platform'" in str(exc.value)
