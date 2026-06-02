"""Tests for the bundled community patterns in the repo's patterns/ directory.

The load-and-uniqueness test is the CI ``validate-patterns`` gate: adding a new
``patterns/*.yaml`` requires no Python change, but it must validate and must not
collide with an existing fingerprint.

The differ-alignment test is the load-bearing one: it proves every bundled
pattern's fingerprint is exactly what ``differ.diff()`` produces, so an imported
pattern actually matches the drift it claims to. If the differ's field names,
drift kinds, or sentinel fields ever change, this test fails loudly instead of
the patterns silently never matching.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netdrift import differ
from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.patterns.exporter import export_known_issues
from netdrift.patterns.importer import import_patterns_dir, import_patterns_text
from netdrift.patterns.loader import load_patterns_dir, pattern_fingerprints
from netdrift.storage.models import Base
from netdrift.storage.repository import list_known_issues

REPO_ROOT = Path(__file__).resolve().parents[1]
PATTERNS_DIR = REPO_ROOT / "patterns"
SRC_DIR = REPO_ROOT / "src"


def test_no_unsafe_yaml_load_in_source():
    """Patterns are user-supplied YAML, so the loader must use yaml.safe_load.
    This is the validate-patterns lint, folded into the test suite: no module
    under src/ may call the unsafe yaml.load / yaml.full_load / yaml.unsafe_load.
    """
    banned = ("yaml.load(", "yaml.full_load(", "yaml.unsafe_load(")
    offenders = []
    for py in SRC_DIR.rglob("*.py"):
        text = py.read_text()
        if any(b in text for b in banned):
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert not offenders, f"unsafe YAML load found in: {offenders}; use yaml.safe_load"


def _bundle_fingerprints():
    fps = []
    for _path, pattern in load_patterns_dir(PATTERNS_DIR):
        fps.extend(pattern_fingerprints(pattern))
    return fps


def test_bundle_loads_and_meets_minimum_counts():
    loaded = load_patterns_dir(PATTERNS_DIR)
    by_type: dict[str, int] = {}
    for _path, pattern in loaded:
        by_type[pattern.object_type] = by_type.get(pattern.object_type, 0) + 1

    assert len(loaded) >= 31
    assert by_type["interface"] >= 8
    assert by_type["vlan"] >= 3
    assert by_type["bgp_neighbor"] >= 6
    assert by_type["ospf_adjacency"] >= 5
    assert by_type["config"] >= 1
    assert by_type["device"] >= 1
    assert by_type["tunnel"] >= 7


def test_bundle_fingerprints_are_unique():
    fps = _bundle_fingerprints()
    assert len(fps) == len(set(fps)), "two bundled patterns collide on a fingerprint"


def test_restore_intent_patterns_only_use_applier_supported_fields():
    # restore_intent grants must name a field the appliers can actually render,
    # or enabling auto-apply on the imported issue would fail at apply time.
    supported = {
        "interface": {"description", "enabled", "untagged_vlan", "tagged_vlans"},
        "vlan": {"name"},
    }
    for _path, pattern in load_patterns_dir(PATTERNS_DIR):
        if pattern.remediation and pattern.remediation.kind == "restore_intent":
            assert pattern.field in supported.get(pattern.object_type, set()), (
                f"{pattern.name!r}: restore_intent on unsupported field "
                f"{pattern.object_type}/{pattern.field}"
            )


def _state(**overrides):
    base = {
        "device": "core-sw-01",
        "platform": "arista_eos",
        "collected_at": "2026-06-02T00:00:00Z",
        "interfaces": {},
        "vlans": {},
        "bgp_neighbors": {},
        "ospf": {"adjacencies": {}},
        "running_config": "",
    }
    base.update(overrides)
    return base


def _iface(enabled=True, description="", ip_addresses=None, mode="routed",
           untagged_vlan=None, tagged_vlans=None):
    return {
        "description": description,
        "enabled": enabled,
        "ip_addresses": ip_addresses or [],
        "mode": mode,
        "untagged_vlan": untagged_vlan,
        "tagged_vlans": tagged_vlans or [],
    }


def test_every_drift_the_bundle_covers_matches_differ_output():
    """Craft an intent/reality pair that triggers all 31 bundled drift kinds and
    assert the fingerprints differ produces are exactly the bundle's set."""
    intent = _state(
        interfaces={
            # Eth1 differs on all six comparable fields at once.
            "Ethernet1": _iface(enabled=True, description="uplink",
                                ip_addresses=["10.0.0.1/31"], mode="routed",
                                untagged_vlan=10, tagged_vlans=[10, 20]),
            # Eth2 is in intent only -> interface missing_in_reality.
            "Ethernet2": _iface(),
        },
        vlans={"10": {"name": "users"}, "20": {"name": "voice"}},
        bgp_neighbors={
            "10.0.0.1": {"remote_as": 65001, "enabled": True,
                          "description": "peer-a", "session_state": "established"},
            "10.0.0.2": {"remote_as": 65002, "enabled": True,
                          "description": "peer-b", "session_state": "established"},
        },
        ospf={"adjacencies": {
            "2.2.2.2": {"area": "0.0.0.0", "interface": "Ethernet1",
                         "adjacency_state": "full"},
            "3.3.3.3": {"area": "0.0.0.0", "interface": "Ethernet2",
                         "adjacency_state": "full"},
        }},
        tunnels={
            # Tunnel0 differs on all five comparable fields at once.
            "Tunnel0": {"type": "gre", "source": "192.0.2.1",
                         "destination": "198.51.100.1", "enabled": True,
                         "tunnel_state": "up"},
            # Tunnel1 is in intent only -> tunnel missing_in_reality.
            "Tunnel1": {"type": "gre", "source": "192.0.2.5",
                         "destination": "198.51.100.5", "enabled": True,
                         "tunnel_state": "up"},
        },
        running_config="hostname a\ninterface Ethernet1\n",
        software_version="4.30.1F",
    )
    reality = _state(
        interfaces={
            "Ethernet1": _iface(enabled=False, description="changed",
                                ip_addresses=["10.0.0.3/31"], mode="access",
                                untagged_vlan=99, tagged_vlans=[10]),
            # Eth3 is in reality only -> interface missing_in_intent.
            "Ethernet3": _iface(),
        },
        vlans={"10": {"name": "USERS"}, "30": {"name": "mgmt"}},
        bgp_neighbors={
            # peer-a differs on all four comparable fields.
            "10.0.0.1": {"remote_as": 65009, "enabled": False,
                          "description": "renamed", "session_state": "active"},
            # 10.0.0.2 absent -> bgp_neighbor missing_in_reality.
            # 10.0.0.9 reality-only -> bgp_neighbor missing_in_intent.
            "10.0.0.9": {"remote_as": 65003, "enabled": True,
                          "description": "peer-c", "session_state": "established"},
        },
        ospf={"adjacencies": {
            "2.2.2.2": {"area": "0.0.0.1", "interface": "Ethernet9",
                         "adjacency_state": "init"},
            # 3.3.3.3 absent -> ospf_adjacency missing_in_reality.
            # 9.9.9.9 reality-only -> ospf_adjacency missing_in_intent.
            "9.9.9.9": {"area": "0.0.0.0", "interface": "Ethernet3",
                         "adjacency_state": "full"},
        }},
        tunnels={
            # Tunnel0 differs on type/source/destination/enabled/tunnel_state.
            "Tunnel0": {"type": "vti", "source": "192.0.2.9",
                         "destination": "198.51.100.9", "enabled": False,
                         "tunnel_state": "down"},
            # Tunnel1 absent -> tunnel missing_in_reality.
            # Tunnel2 reality-only -> tunnel missing_in_intent.
            "Tunnel2": {"type": "gre", "source": "192.0.2.7",
                         "destination": "198.51.100.7", "enabled": True,
                         "tunnel_state": "up"},
        },
        # Differs from intent -> config running_config value_mismatch.
        running_config="hostname b\ninterface Ethernet1\n",
        # Differs from intent -> device software_version value_mismatch.
        software_version="4.31.2F",
    )

    produced = {make_fingerprint(d) for d in differ.diff(intent, reality)}
    bundle = set(_bundle_fingerprints())

    # Every drift this pair produces is covered by a bundled pattern, and the
    # bundle has no fingerprint that differ cannot produce.
    assert produced == bundle


def test_real_bundle_imports_and_round_trips():
    """The shipped patterns/ dir imports cleanly and survives an
    export -> wipe -> import -> export cycle byte-for-byte. Guards the actual
    bundle (including the config/device patterns) through the full I/O path."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        summary = import_patterns_dir(s, PATTERNS_DIR)
        s.commit()
        assert summary["created"] >= 31
        assert summary["updated"] == 0
        assert all(i.auto_apply_enabled is False for i in list_known_issues(s))

        export1 = export_known_issues(s)
        for issue in list_known_issues(s):
            s.delete(issue)
        s.commit()

        import_patterns_text(s, export1)
        s.commit()
        assert export_known_issues(s) == export1
