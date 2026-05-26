"""tests/test_storage.py — storage layer unit tests (v0.2).

These run against an in-memory SQLite database, not Postgres. The storage code
goes through SQLAlchemy, so the same save_drifts / get_drifts run unchanged on
SQLite — letting the suite run fast, in CI, with no database to set up. The
real Postgres container is used for manual end-to-end checks, not these tests.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netdrift.storage.models import Base
from netdrift.storage.repository import save_drifts, get_drifts


@pytest.fixture
def session():
    """Hand each test a clean, empty in-memory SQLite session.

    A new ':memory:' database is created per test, the tables are built on it,
    and it vanishes when the test ends — so tests never pollute each other.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def _drift(**overrides):
    """A schema-complete drift-record dict; override any field per test."""
    base = {
        "device": "core-sw-01",
        "object": "interface:Ethernet2",
        "field": "untagged_vlan",
        "intent": 10,
        "reality": 99,
        "drift_kind": "value_mismatch",
        "severity": "warning",
        "detected_at": "2026-05-26T14:32:00Z",
    }
    base.update(overrides)
    return base


def test_save_and_read_back_one_event(session):
    save_drifts(session, [_drift()])
    session.commit()
    rows = get_drifts(session)
    assert len(rows) == 1
    assert rows[0].device == "core-sw-01"
    assert rows[0].object_ref == "interface:Ethernet2"
    assert rows[0].field == "untagged_vlan"


def test_database_assigns_an_id(session):
    saved = save_drifts(session, [_drift()])
    session.commit()
    # The dict carried no id; the database assigned one.
    assert saved[0].id is not None


def test_json_columns_preserve_int_types(session):
    save_drifts(session, [_drift(intent=10, reality=99)])
    session.commit()
    row = get_drifts(session)[0]
    # The crucial design check: ints come back as ints, not strings.
    assert row.intent == 10
    assert row.reality == 99
    assert isinstance(row.intent, int)


def test_json_columns_preserve_list_types(session):
    save_drifts(session, [_drift(
        field="ip_addresses",
        intent=["10.0.0.1/30"],
        reality=["10.0.0.5/30"],
    )])
    session.commit()
    row = get_drifts(session)[0]
    assert row.intent == ["10.0.0.1/30"]
    assert isinstance(row.reality, list)


def test_iso_string_becomes_real_datetime(session):
    from datetime import datetime
    save_drifts(session, [_drift(detected_at="2026-05-26T14:32:00Z")])
    session.commit()
    row = get_drifts(session)[0]
    # Stored as a real timestamp, not the original string.
    assert isinstance(row.detected_at, datetime)


def test_get_drifts_filters_by_device(session):
    save_drifts(session, [
        _drift(device="core-sw-01"),
        _drift(device="core-sw-02"),
    ])
    session.commit()
    rows = get_drifts(session, device="core-sw-02")
    assert len(rows) == 1
    assert rows[0].device == "core-sw-02"


def test_get_drifts_orders_newest_first(session):
    save_drifts(session, [
        _drift(detected_at="2026-05-26T14:00:00Z", field="older"),
        _drift(detected_at="2026-05-26T15:00:00Z", field="newer"),
    ])
    session.commit()
    rows = get_drifts(session)
    # Newest detected_at comes first.
    assert rows[0].field == "newer"
    assert rows[1].field == "older"


def test_limit_caps_row_count(session):
    save_drifts(session, [_drift(), _drift(), _drift()])
    session.commit()
    rows = get_drifts(session, limit=2)
    assert len(rows) == 2