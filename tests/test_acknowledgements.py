"""tests/test_acknowledgements.py — drift acknowledgement storage (v3.5).

An acknowledgement says "this drift is intentional, stop alerting" for a
(device, fingerprint) pair, optionally until an expiry. It must live in its own
table — DriftEvent rows are immutable and recreated every poll cycle, so an ack
stored on a row would be lost on the next poll. Keying by (device, fingerprint)
makes the ack persist across cycles and reuses the v2.0 fingerprint signature.

Repository-level tests on in-memory SQLite. Expiry uses an injected ``now`` —
no time.sleep.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.storage.models import Base
from netdrift.storage.repository import (
    create_acknowledgement,
    delete_acknowledgement,
    is_acknowledged,
    list_acknowledgements,
)

FP = "interface:Ethernet1|enabled|value_mismatch"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


def test_not_acknowledged_when_no_row(session):
    assert is_acknowledged(session, "core-sw-01", FP) is False


def test_acknowledged_with_no_expiry_is_permanent(session):
    create_acknowledgement(session, "core-sw-01", FP, acknowledged_until=None)
    session.commit()
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert is_acknowledged(session, "core-sw-01", FP, now=far_future) is True


def test_acknowledged_until_future_is_active(session):
    until = datetime(2026, 7, 1, tzinfo=timezone.utc)
    create_acknowledgement(session, "core-sw-01", FP, acknowledged_until=until)
    session.commit()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert is_acknowledged(session, "core-sw-01", FP, now=now) is True


def test_expired_acknowledgement_is_not_active(session):
    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    create_acknowledgement(session, "core-sw-01", FP, acknowledged_until=until)
    session.commit()
    later = datetime(2026, 6, 2, tzinfo=timezone.utc)
    assert is_acknowledged(session, "core-sw-01", FP, now=later) is False


def test_acknowledgement_is_scoped_to_device_and_fingerprint(session):
    create_acknowledgement(session, "core-sw-01", FP)
    session.commit()
    # Same fingerprint, different device — not acknowledged.
    assert is_acknowledged(session, "core-sw-02", FP) is False
    # Same device, different fingerprint — not acknowledged.
    assert is_acknowledged(session, "core-sw-01", "vlan:10|name|value_mismatch") is False


def test_delete_removes_acknowledgement(session):
    ack = create_acknowledgement(session, "core-sw-01", FP)
    session.commit()
    assert is_acknowledged(session, "core-sw-01", FP) is True
    assert delete_acknowledgement(session, ack.id) is True
    session.commit()
    assert is_acknowledged(session, "core-sw-01", FP) is False


def test_delete_unknown_id_returns_false(session):
    assert delete_acknowledgement(session, 999) is False


def test_list_returns_acknowledgements(session):
    create_acknowledgement(session, "core-sw-01", FP)
    create_acknowledgement(session, "core-sw-02", "vlan:10|name|value_mismatch")
    session.commit()
    rows = list_acknowledgements(session)
    assert len(rows) == 2
    assert {r.device for r in rows} == {"core-sw-01", "core-sw-02"}
