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


# --- SLA breach state (v4.5 edge-triggered alerting) -------------------------

def test_record_and_read_active_sla_breach(session):
    from netdrift.storage.repository import active_sla_breaches, record_sla_breach
    now = datetime.now(tz=timezone.utc)
    record_sla_breach(session, "core-sw-01", "interface|enabled|value_mismatch", now)
    session.commit()
    assert active_sla_breaches(session) == {
        ("core-sw-01", "interface|enabled|value_mismatch")
    }


def test_record_sla_breach_is_idempotent(session):
    from netdrift.storage.repository import active_sla_breaches, record_sla_breach
    now = datetime.now(tz=timezone.utc)
    fp = "tunnel|tunnel_state|value_mismatch"
    first = record_sla_breach(session, "core-sw-01", fp, now)
    again = record_sla_breach(session, "core-sw-01", fp, now + timedelta(minutes=5))
    session.commit()
    # Same row returned; first_fired_at is not bumped on the second call.
    assert again.first_fired_at == first.first_fired_at
    assert len(active_sla_breaches(session)) == 1


def test_clear_sla_breach_removes_only_that_key(session):
    from netdrift.storage.repository import (
        active_sla_breaches,
        clear_sla_breach,
        record_sla_breach,
    )
    now = datetime.now(tz=timezone.utc)
    record_sla_breach(session, "core-sw-01", "a|b|value_mismatch", now)
    record_sla_breach(session, "core-sw-02", "a|b|value_mismatch", now)
    session.commit()
    assert clear_sla_breach(session, "core-sw-01", "a|b|value_mismatch") == 1
    session.commit()
    assert active_sla_breaches(session) == {("core-sw-02", "a|b|value_mismatch")}


def test_drift_first_seen_returns_earliest_per_identity(session):
    from netdrift.storage.repository import drift_first_seen
    # Same drift identity re-saved across two polls; the earliest is "first seen".
    save_drifts(session, [
        _drift(detected_at="2026-05-26T14:00:00Z"),
        _drift(detected_at="2026-05-26T14:05:00Z"),
    ])
    session.commit()
    ident = ("core-sw-01", "interface:Ethernet2", "untagged_vlan", "value_mismatch")
    got = drift_first_seen(session, [ident])
    # The earlier of the two timestamps (14:00, not 14:05).
    assert (got[ident].hour, got[ident].minute) == (14, 0)


def test_drift_first_seen_empty_input_skips_query(session):
    from netdrift.storage.repository import drift_first_seen
    assert drift_first_seen(session, []) == {}


def test_count_drifts_for_identity(session):
    from netdrift.storage.repository import count_drifts_for_identity
    save_drifts(session, [
        _drift(detected_at="2026-05-26T14:00:00Z"),
        _drift(detected_at="2026-05-26T14:05:00Z"),
        _drift(device="core-sw-02", detected_at="2026-05-26T14:05:00Z"),
    ])
    session.commit()
    n = count_drifts_for_identity(
        session, "core-sw-01", "interface:Ethernet2", "untagged_vlan", "value_mismatch"
    )
    assert n == 2  # the two core-sw-01 rows, not the core-sw-02 one


def test_record_sla_breach_stores_severity(session):
    from netdrift.storage.repository import record_sla_breach, sla_breach_severity_map
    now = datetime.now(tz=timezone.utc)
    record_sla_breach(session, "core-sw-01", "a|b|value_mismatch", now, severity="critical")
    session.commit()
    assert sla_breach_severity_map(session) == {
        ("core-sw-01", "a|b|value_mismatch"): "critical"
    }


def test_record_sla_breach_backfills_missing_severity(session):
    from netdrift.storage.repository import record_sla_breach, sla_breach_severity_map
    now = datetime.now(tz=timezone.utc)
    fp = "a|b|value_mismatch"
    record_sla_breach(session, "core-sw-01", fp, now)  # severity omitted
    record_sla_breach(session, "core-sw-01", fp, now + timedelta(minutes=5), severity="warning")
    session.commit()
    # The second call backfills severity without re-timing the breach.
    assert sla_breach_severity_map(session)[("core-sw-01", fp)] == "warning"


def test_inventory_accuracy_classifies_each_device(session):
    from netdrift.storage.repository import inventory_accuracy, record_collection
    now = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)

    # core-sw-01: collected recently AND has drift in window -> drifted
    save_drifts(session, [_drift(
        device="core-sw-01",
        detected_at=(now - timedelta(minutes=5)).isoformat(),
    )])
    record_collection(session, "core-sw-01", when=now - timedelta(minutes=2))
    # core-sw-02: collected recently, no drift -> accurate
    record_collection(session, "core-sw-02", when=now - timedelta(minutes=2))
    # core-sw-03: never collected -> stale (absence of drift isn't trustworthy)
    session.commit()

    result = inventory_accuracy(
        session, ["core-sw-01", "core-sw-02", "core-sw-03"],
        window_minutes=60, now=now,
    )

    assert result["total_devices"] == 3
    assert result["drifted"] == 1
    assert result["accurate"] == 1
    assert result["stale"] == 1
    assert result["accuracy_pct"] == round(1 / 3 * 100, 1)
    by_name = {d["name"]: d for d in result["devices"]}
    assert by_name["core-sw-01"]["status"] == "drifted"
    assert by_name["core-sw-02"]["status"] == "accurate"
    assert by_name["core-sw-03"]["status"] == "stale"
    assert by_name["core-sw-03"]["last_collected_at"] is None


def test_inventory_accuracy_counts_distinct_drift_not_poll_rows(session):
    """The same drift recorded across many polls is one drifted thing, not many."""
    from netdrift.storage.repository import inventory_accuracy, record_collection
    now = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    # Same (object, field, drift_kind) seen on three separate polls.
    for minutes in (5, 10, 15):
        save_drifts(session, [_drift(
            device="core-sw-01",
            detected_at=(now - timedelta(minutes=minutes)).isoformat(),
        )])
    record_collection(session, "core-sw-01", when=now - timedelta(minutes=2))
    session.commit()

    result = inventory_accuracy(session, ["core-sw-01"], window_minutes=60, now=now)
    assert result["devices"][0]["drift_count"] == 1
    assert result["drifted"] == 1


def test_inventory_accuracy_empty_inventory(session):
    from netdrift.storage.repository import inventory_accuracy
    result = inventory_accuracy(
        session, [], now=datetime(2026, 6, 3, tzinfo=timezone.utc)
    )
    assert result["total_devices"] == 0
    assert result["accuracy_pct"] is None
    assert result["devices"] == []


def test_record_reachability_upserts_and_retains_last_seen(session):
    from netdrift.storage.repository import get_device_setting, record_reachability
    t1 = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    record_reachability(session, "core-sw-01", True, when=t1)
    session.commit()
    setting = get_device_setting(session, "core-sw-01")
    assert setting.reachable is True
    first_seen = setting.last_reachable_at
    assert first_seen is not None

    # A later unreachable probe flips reachable and advances the checked-at
    # stamp, but last_reachable_at (last seen) is retained.
    t2 = t1 + timedelta(minutes=5)
    record_reachability(session, "core-sw-01", False, when=t2)
    session.commit()
    setting = get_device_setting(session, "core-sw-01")
    assert setting.reachable is False
    assert setting.last_reachable_at == first_seen
    assert setting.reachability_checked_at > setting.last_reachable_at


def test_record_reachability_preserves_pause_and_collection(session):
    """Probing must not clobber unrelated device state."""
    from netdrift.storage.repository import (
        get_device_setting,
        record_collection,
        record_reachability,
        set_device_paused,
    )
    set_device_paused(session, "core-sw-01", True, reason="change freeze")
    record_collection(session, "core-sw-01")
    record_reachability(session, "core-sw-01", True)
    session.commit()
    setting = get_device_setting(session, "core-sw-01")
    assert setting.auto_remediation_paused is True
    assert setting.last_collected_at is not None
    assert setting.reachable is True


def test_inventory_accuracy_window_excludes_old_drift(session):
    """A drift older than the window does not count a recently-collected device
    as drifted."""
    from netdrift.storage.repository import inventory_accuracy, record_collection
    now = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    save_drifts(session, [_drift(
        device="core-sw-01",
        detected_at=(now - timedelta(minutes=120)).isoformat(),  # outside 60-min window
    )])
    record_collection(session, "core-sw-01", when=now - timedelta(minutes=2))
    session.commit()

    result = inventory_accuracy(session, ["core-sw-01"], window_minutes=60, now=now)
    assert result["devices"][0]["status"] == "accurate"
    assert result["accuracy_pct"] == 100.0