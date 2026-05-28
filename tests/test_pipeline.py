"""tests/test_pipeline.py — pipeline orchestration tests (v0.2).

run_drift_check wires together intent + reality + diff + save. Its lab-facing
parts (get_intent, the collectors) are injected, so these tests verify the
orchestration with fakes — no NetBox, no device, no real database. The
session_factory is pointed at a shared in-memory SQLite database so the save
step really runs and can be read back.
"""

import pytest
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.pipeline import run_drift_check
from netdrift.storage.models import Base
from netdrift.storage.repository import get_drifts


def _state(interfaces=None, vlans=None, bgp_neighbors=None, ospf=None, platform="arista_eos"):
    """A schema-complete device-state dict for intent/reality fakes.
    v0.3 adds bgp_neighbors and ospf; both default to the "no routing on
    this device" empty shape."""
    return {
        "device": "core-sw-01",
        "platform": platform,
        "collected_at": "2026-05-26T14:32:00Z",
        "interfaces": interfaces or {},
        "vlans": vlans or {},
        "bgp_neighbors": bgp_neighbors or {},
        "ospf": ospf or {"adjacencies": {}},
    }


def _iface(**overrides):
    base = {
        "description": "", "enabled": True, "ip_addresses": [],
        "mode": "routed", "untagged_vlan": None, "tagged_vlans": [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def session_factory():
    """A callable returning sessions on one shared in-memory SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def factory():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    factory._Session = Session  # exposed so tests can read back independently
    return factory


def test_pipeline_saves_drift_when_intent_and_reality_differ(session_factory):
    device = {"name": "core-sw-01", "hostname": "x", "username": "u", "password": "p"}

    # Intent says Ethernet1 is enabled; reality says it's down -> one drift.
    intent = _state({"Ethernet1": _iface(enabled=True)})
    reality = _state({"Ethernet1": _iface(enabled=False)})

    drifts = run_drift_check(
        device,
        get_intent=lambda name: intent,
        collectors={"arista_eos": lambda dev: reality},
        session_factory=session_factory,
    )

    # It returned the drift...
    assert len(drifts) == 1
    assert drifts[0]["field"] == "enabled"

    # ...and persisted it: read back from the same database.
    with session_factory._Session() as s:
        rows = get_drifts(s)
    assert len(rows) == 1
    assert rows[0].field == "enabled"


def test_pipeline_saves_nothing_when_states_match(session_factory):
    device = {"name": "core-sw-01"}
    state = _state({"Ethernet1": _iface(enabled=True)})

    drifts = run_drift_check(
        device,
        get_intent=lambda name: state,
        collectors={"arista_eos": lambda dev: state},
        session_factory=session_factory,
    )

    assert drifts == []
    with session_factory._Session() as s:
        assert get_drifts(s) == []


def test_pipeline_dispatches_on_platform(session_factory):
    device = {"name": "nokia-sw-01"}
    intent = _state(platform="nokia_srlinux")
    reality = _state(platform="nokia_srlinux")

    called = {"arista": False, "nokia": False}

    def fake_arista(dev):
        called["arista"] = True
        return reality

    def fake_nokia(dev):
        called["nokia"] = True
        return reality

    run_drift_check(
        device,
        get_intent=lambda name: intent,
        collectors={"arista_eos": fake_arista, "nokia_srlinux": fake_nokia},
        session_factory=session_factory,
    )

    # Platform was nokia_srlinux, so only the nokia collector should run.
    assert called["nokia"] is True
    assert called["arista"] is False


def test_pipeline_raises_on_unknown_platform(session_factory):
    device = {"name": "mystery-01"}
    intent = _state(platform="cisco_iosxe")  # no collector registered for it

    with pytest.raises(ValueError, match="No collector for platform"):
        run_drift_check(
            device,
            get_intent=lambda name: intent,
            collectors={"arista_eos": lambda dev: intent},
            session_factory=session_factory,
        )