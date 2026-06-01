"""tests/test_cli.py — driftcheck CLI dispatch (v1.0).

Exercises cli.main()'s collector dispatch through the registry-backed COLLECTORS
table and the new `collectors=` injection seam. load_devices and the intent
function are patched, so no devices.yml, NetBox, or device is touched.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift import cli
from netdrift.auth import KEY_PREFIX
from netdrift.storage.models import Base
from netdrift.storage.repository import verify_api_key


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
