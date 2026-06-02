"""tests/test_storage.py — storage layer unit tests (v0.2).

These run against an in-memory SQLite database, not Postgres. The storage code
goes through SQLAlchemy, so the same save_drifts / get_drifts run unchanged on
SQLite — letting the suite run fast, in CI, with no database to set up. The
real Postgres container is used for manual end-to-end checks, not these tests.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from datetime import datetime, timedelta, timezone

from netdrift.storage.models import Base
from netdrift.storage.repository import save_drifts, get_drifts, get_drift_history


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


# ---------------------------------------------------------------------------
# get_drift_history tests
# These tests use datetime.now() for detected_at so the events always fall
# inside the 24-hour window regardless of when the suite runs.
# ---------------------------------------------------------------------------

def _now_str():
    return datetime.now(tz=timezone.utc).isoformat()


def test_history_groups_same_bucket_into_one(session):
    """Two events in the same 5-minute bucket produce one entry with count=2."""
    now = _now_str()
    save_drifts(session, [_drift(detected_at=now), _drift(detected_at=now)])
    session.commit()
    history = get_drift_history(session)
    assert len(history) == 1
    assert history[0]["count"] == 2
    assert history[0]["device"] == "core-sw-01"


def test_history_separates_different_buckets(session):
    """Events 10 minutes apart land in different 5-minute buckets."""
    now = datetime.now(tz=timezone.utc)
    earlier = (now - timedelta(minutes=10)).isoformat()
    save_drifts(session, [
        _drift(detected_at=now.isoformat()),
        _drift(detected_at=earlier),
    ])
    session.commit()
    history = get_drift_history(session)
    assert len(history) == 2


def test_history_separates_different_devices(session):
    """Same bucket, different devices → two entries."""
    now = _now_str()
    save_drifts(session, [
        _drift(device="core-sw-01", detected_at=now),
        _drift(device="core-sw-02", detected_at=now),
    ])
    session.commit()
    history = get_drift_history(session)
    assert len(history) == 2


def test_history_excludes_events_older_than_window(session):
    """Events outside the hours window are not returned."""
    old = (datetime.now(tz=timezone.utc) - timedelta(hours=25)).isoformat()
    save_drifts(session, [_drift(detected_at=old)])
    session.commit()
    assert get_drift_history(session) == []


def test_history_severity_breakdown(session):
    """Each bucket reports per-severity counts."""
    now = _now_str()
    save_drifts(session, [
        _drift(severity="critical", detected_at=now),
        _drift(severity="warning", detected_at=now),
        _drift(severity="info", detected_at=now),
    ])
    session.commit()
    history = get_drift_history(session)
    assert len(history) == 1
    assert history[0]["count"] == 3
    assert history[0]["critical"] == 1
    assert history[0]["warning"] == 1
    assert history[0]["info"] == 1


def test_history_filters_by_device(session):
    """device= kwarg limits results to one device."""
    now = _now_str()
    save_drifts(session, [
        _drift(device="core-sw-01", detected_at=now),
        _drift(device="core-sw-02", detected_at=now),
    ])
    session.commit()
    history = get_drift_history(session, device="core-sw-01")
    assert len(history) == 1
    assert history[0]["device"] == "core-sw-01"


# ---------------------------------------------------------------------------
# PERF4 — confirmed_counts: one GROUP BY for many issues, not N+1 COUNT(*).
# ---------------------------------------------------------------------------

def test_confirmed_counts_returns_success_count_per_issue(session):
    from netdrift.storage.repository import (
        confirmed_counts,
        save_known_issue,
        save_remediation_event,
    )

    a = save_known_issue(session, "obj|fa|value_mismatch", "c", "f")
    b = save_known_issue(session, "obj|fb|value_mismatch", "c", "f")
    c = save_known_issue(session, "obj|fc|value_mismatch", "c", "f")
    session.commit()

    # a: 2 successes + 1 failure (failure must not count); b: none; c: 1 success.
    save_remediation_event(session, a.id, "p", "", "", "success", "api")
    save_remediation_event(session, a.id, "p", "", "", "success", "api")
    save_remediation_event(session, a.id, "p", "", "", "failure", "api")
    save_remediation_event(session, c.id, "p", "", "", "success", "api")
    session.commit()

    counts = confirmed_counts(session, [a.id, b.id, c.id])

    # Every requested id is present, including the one with zero successes.
    assert counts == {a.id: 2, b.id: 0, c.id: 1}


def test_confirmed_counts_empty_input_returns_empty(session):
    from netdrift.storage.repository import confirmed_counts
    assert confirmed_counts(session, []) == {}


# --- drift explanations (AI1) ------------------------------------------------

def test_upsert_explanation_inserts(session):
    from netdrift.storage.repository import get_explanations, upsert_explanation
    upsert_explanation(session, "interface|enabled|value_mismatch", "Likely a shut.", "llm")
    session.commit()
    got = get_explanations(session, ["interface|enabled|value_mismatch"])
    assert got["interface|enabled|value_mismatch"].explanation == "Likely a shut."
    assert got["interface|enabled|value_mismatch"].source == "llm"


def test_upsert_explanation_updates_in_place(session):
    from netdrift.storage.repository import get_explanations, upsert_explanation
    fp = "tunnel|tunnel_state|value_mismatch"
    upsert_explanation(session, fp, "first", "deterministic")
    upsert_explanation(session, fp, "second", "llm")
    session.commit()
    got = get_explanations(session, [fp])
    assert len(got) == 1  # one row per fingerprint, updated not duplicated
    assert got[fp].explanation == "second"
    assert got[fp].source == "llm"


def test_get_explanations_empty_iterable_returns_empty_without_query(session):
    from netdrift.storage.repository import get_explanations
    assert get_explanations(session, []) == {}


def test_get_explanations_filters_to_requested(session):
    from netdrift.storage.repository import get_explanations, upsert_explanation
    upsert_explanation(session, "a|b|value_mismatch", "x", "llm")
    upsert_explanation(session, "c|d|value_mismatch", "y", "llm")
    session.commit()
    got = get_explanations(session, ["a|b|value_mismatch"])
    assert set(got) == {"a|b|value_mismatch"}


# --- maintenance windows -----------------------------------------------------

def _mw_repo():
    from netdrift.storage import repository
    return repository


def test_create_and_list_maintenance_window(session):
    r = _mw_repo()
    now = datetime.now(tz=timezone.utc)
    r.create_maintenance_window(session, "core-sw-01", now, now + timedelta(hours=1), "upgrade")
    session.commit()
    rows = r.list_maintenance_windows(session)
    assert len(rows) == 1
    assert rows[0].device == "core-sw-01"
    assert rows[0].reason == "upgrade"


def test_is_in_maintenance_window_active(session):
    r = _mw_repo()
    now = datetime.now(tz=timezone.utc)
    r.create_maintenance_window(session, "core-sw-01", now - timedelta(minutes=10),
                                now + timedelta(minutes=10))
    session.commit()
    assert r.is_in_maintenance_window(session, "core-sw-01", now=now) is True
    assert r.is_in_maintenance_window(session, "core-sw-02", now=now) is False


def test_is_in_maintenance_window_respects_time_bounds(session):
    r = _mw_repo()
    now = datetime.now(tz=timezone.utc)
    # A window that has not started yet.
    r.create_maintenance_window(session, "core-sw-01", now + timedelta(hours=1),
                                now + timedelta(hours=2))
    session.commit()
    assert r.is_in_maintenance_window(session, "core-sw-01", now=now) is False


def test_global_window_matches_every_device(session):
    r = _mw_repo()
    now = datetime.now(tz=timezone.utc)
    r.create_maintenance_window(session, None, now - timedelta(minutes=5),
                                now + timedelta(minutes=5), "global freeze")
    session.commit()
    assert r.is_in_maintenance_window(session, "any-device", now=now) is True


def test_delete_maintenance_window(session):
    r = _mw_repo()
    now = datetime.now(tz=timezone.utc)
    w = r.create_maintenance_window(session, "core-sw-01", now, now + timedelta(hours=1))
    session.commit()
    assert r.delete_maintenance_window(session, w.id) == 1
    session.commit()
    assert r.list_maintenance_windows(session) == []