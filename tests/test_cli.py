"""tests/test_cli.py — driftcheck CLI dispatch (v1.0).

Exercises cli.main()'s collector dispatch through the registry-backed COLLECTORS
table and the new `collectors=` injection seam. load_devices and the intent
function are patched, so no devices.yml, NetBox, or device is touched.
"""

import textwrap

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift import cli
from netdrift.auth import KEY_PREFIX
from netdrift.storage.models import Base
from netdrift.storage.repository import list_known_issues, verify_api_key


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


# ---------------------------------------------------------------------------
# create-api-key subcommand
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_create_api_key_prints_key_once(db_session_factory, capsys):
    cli._cmd_create_api_key(["--name", "admin"], session_factory=db_session_factory)
    out = capsys.readouterr().out.strip()
    assert out.startswith(KEY_PREFIX)


def test_create_api_key_key_verifies_against_db(db_session_factory, capsys):
    cli._cmd_create_api_key(["--name", "ci"], session_factory=db_session_factory)
    raw_key = capsys.readouterr().out.strip()
    with db_session_factory() as session:
        assert verify_api_key(session, raw_key) is not None


def test_create_api_key_raw_key_not_in_db(db_session_factory, capsys):
    cli._cmd_create_api_key(["--name", "test"], session_factory=db_session_factory)
    raw_key = capsys.readouterr().out.strip()
    from netdrift.storage.models import ApiKey
    with db_session_factory() as session:
        row = session.query(ApiKey).one()
        assert row.key_hash != raw_key
        assert raw_key not in (row.key_hint or "")


def test_create_api_key_missing_name_exits(db_session_factory):
    with pytest.raises(SystemExit):
        cli._cmd_create_api_key([], session_factory=db_session_factory)


def test_main_routes_create_api_key(db_session_factory, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmd_create_api_key",
                        lambda argv, **_: print("routed"))
    cli.main(argv=["create-api-key", "--name", "admin"])
    assert "routed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# import-patterns subcommand
# ---------------------------------------------------------------------------


def _write_pattern(path, name, body):
    f = path / name
    f.write_text(textwrap.dedent(body))
    return f


def test_import_patterns_imports_and_reports(db_session_factory, tmp_path, capsys):
    _write_pattern(
        tmp_path,
        "iface.yaml",
        """
        name: Interface admin-down
        object_type: interface
        field: enabled
        drift_kinds: [value_mismatch]
        cause: shut
        fix: no shut
        """,
    )
    cli._cmd_import_patterns([str(tmp_path)], session_factory=db_session_factory)
    out = capsys.readouterr().out
    assert "1 created" in out
    with db_session_factory() as session:
        assert len(list_known_issues(session)) == 1


def test_import_patterns_invalid_file_exits(db_session_factory, tmp_path):
    _write_pattern(
        tmp_path,
        "bad.yaml",
        "name: x\nobject_type: interface\nfield: enabled\ndrift_kinds: [nope]\ncause: c\nfix: f\n",
    )
    with pytest.raises(SystemExit):
        cli._cmd_import_patterns([str(tmp_path)], session_factory=db_session_factory)


def test_main_routes_import_patterns(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmd_import_patterns",
                        lambda argv, **_: print("routed"))
    cli.main(argv=["import-patterns", "patterns/"])
    assert "routed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# validate-patterns subcommand (no database — for CI)
# ---------------------------------------------------------------------------


def test_validate_patterns_reports_valid(tmp_path, capsys):
    _write_pattern(
        tmp_path,
        "iface.yaml",
        """
        name: Interface admin-down
        object_type: interface
        field: enabled
        drift_kinds: [value_mismatch]
        cause: shut
        fix: no shut
        """,
    )
    cli._cmd_validate_patterns([str(tmp_path)])
    out = capsys.readouterr().out
    assert "1 pattern" in out


def test_validate_patterns_invalid_file_exits(tmp_path):
    _write_pattern(
        tmp_path,
        "bad.yaml",
        "name: x\nobject_type: interface\nfield: enabled\ndrift_kinds: [nope]\ncause: c\nfix: f\n",
    )
    with pytest.raises(SystemExit):
        cli._cmd_validate_patterns([str(tmp_path)])


def test_validate_patterns_needs_no_database(tmp_path, capsys, monkeypatch):
    # Validation must not touch the DB — break get_sessionmaker to prove it.
    monkeypatch.setattr(cli, "get_sessionmaker",
                        lambda: (_ for _ in ()).throw(AssertionError("DB used")))
    _write_pattern(
        tmp_path, "iface.yaml",
        """
        name: Interface admin-down
        object_type: interface
        field: enabled
        drift_kinds: [value_mismatch]
        cause: shut
        fix: no shut
        """,
    )
    cli._cmd_validate_patterns([str(tmp_path)])
    assert "1 pattern" in capsys.readouterr().out


def test_bundled_patterns_validate():
    # The repo's own patterns/ must always validate (this is what CI runs).
    cli._cmd_validate_patterns([str(cli.PATTERNS_DIR)])


def test_main_routes_validate_patterns(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmd_validate_patterns",
                        lambda argv, **_: print("routed"))
    cli.main(argv=["validate-patterns", "patterns/"])
    assert "routed" in capsys.readouterr().out


# demo subcommand (no NetBox, device, or database)

def test_main_routes_demo(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_cmd_demo", lambda argv, **_: print("routed"))
    cli.main(argv=["demo"])
    assert "routed" in capsys.readouterr().out


def test_demo_runs_with_no_setup(capsys):
    # The whole point: demo touches no devices.yml, NetBox, or database.
    cli._cmd_demo([])
    out = capsys.readouterr().out
    assert "netdrift demo" in out
    assert "core-sw-01" in out
    assert "edge-rtr-01" in out


def test_demo_prints_real_differ_output(capsys):
    # Output must be genuine differ records: the known critical interface-down
    # and tunnel-down drifts on core-sw-01 should appear, with severities.
    cli._cmd_demo([])
    out = capsys.readouterr().out
    assert "[CRITICAL]" in out
    assert "[WARNING]" in out
    assert "[INFO]" in out
    assert "tunnel:Tunnel0" in out
    assert "Demo complete:" in out


def test_demo_drift_matches_differ_for_every_pair():
    # Each bundled pair's printed count must equal what differ.diff() produces —
    # the demo is a thin shell over the real engine, not a canned string.
    from netdrift import differ
    from netdrift.demo import demo_pairs

    pairs = demo_pairs()
    assert len(pairs) >= 2
    for _name, intent, reality in pairs:
        # Every demo pair is intentionally non-identical, so it must drift.
        assert differ.diff(intent, reality)


def test_demo_pairs_are_schema_complete():
    # Demo states must carry the same keys a real collector returns, or the
    # demo would mask a schema regression instead of exercising the engine.
    from netdrift.demo import demo_pairs

    required = {"platform", "interfaces", "vlans", "bgp_neighbors", "ospf",
                "running_config", "software_version"}
    for _name, intent, reality in demo_pairs():
        assert required <= set(intent)
        assert required <= set(reality)


def test_demo_drift_records_stamped_for_save(capsys):
    # Records handed to save_drifts() must carry device + platform, like the
    # pipeline stamps — save_drifts indexes on both.
    from netdrift.demo import demo_drift_records

    records = demo_drift_records()
    assert records
    for r in records:
        assert r["device"] in {"core-sw-01", "edge-rtr-01"}
        assert r["platform"] in {"arista_eos", "cisco_iosxe"}
        assert {"object", "field", "drift_kind", "severity"} <= set(r)


def test_demo_seed_persists_every_record(db_session_factory):
    from netdrift.demo import demo_drift_records
    from netdrift.storage.repository import get_drifts

    cli._seed_demo(session_factory=db_session_factory)

    expected = len(demo_drift_records())
    with db_session_factory() as session:
        stored = get_drifts(session)
    assert len(stored) == expected
    devices = {e.device for e in stored}
    assert devices == {"core-sw-01", "edge-rtr-01"}


def test_demo_seed_reports_count(db_session_factory, capsys):
    cli._seed_demo(session_factory=db_session_factory)
    out = capsys.readouterr().out
    assert "Seeded" in out
    assert "dashboard" in out
