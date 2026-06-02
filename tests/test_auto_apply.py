"""tests/test_auto_apply.py — unit tests for the scheduler auto-apply loop.

All tests run without a real database or network device.
- in-memory SQLite for storage
- fake applier callable instead of NAPALM / gNMI
- AUTO_REMEDIATION_ENABLED injected via monkeypatch

Patterns follow test_pipeline.py: session_factory fixture using SQLite,
storage helpers to seed rows, injectable callables for every I/O boundary.
"""

import pytest
from sqlalchemy import create_engine

from netdrift.appliers.base import ApplyResult, RemediationBlockedError
from netdrift.auto_apply import FAILURE_THRESHOLD, AutoApplyOutcome, run_auto_apply
from netdrift.storage.database import create_all, get_sessionmaker
from netdrift.storage.models import KnownIssue
from netdrift.storage.repository import (
    create_acknowledgement,
    save_known_issue,
    save_remediation_event,
    set_auto_apply_enabled,
    set_device_paused,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    return get_sessionmaker(engine)


DEVICE = {
    "name": "core-sw-01",
    "hostname": "172.20.20.11",
    "username": "admin",
    "password": "admin",
}

# A single drift dict as pipeline.run_drift_check would produce it.
DRIFT = {
    "object": "interface:Ethernet1",
    "field": "description",
    "drift_kind": "value_mismatch",
    "severity": "info",
    "intent": "Uplink to core",
    "reality": "old desc",
    "detected_at": "2026-06-01T00:00:00Z",
    "device": "core-sw-01",
    "platform": "arista_eos",
}

FINGERPRINT = "interface|description|value_mismatch"

RESTORE_INTENT_REMEDIATION = {
    "kind": "restore_intent",
    "schema_version": 1,
    "object_type": "interface",
    "field": "description",
    "drift_kinds": ["value_mismatch"],
}

RAW_SNIPPET_REMEDIATION = {
    "kind": "raw_snippet",
    "schema_version": 1,
    "by_platform": {
        "arista_eos": {"transport": "cli", "body": "interface Ethernet1\n   description Uplink"},
    },
}


def _make_issue(session_factory, *, auto_apply_enabled=True, remediation=None):
    """Seed a KnownIssue with a matching fingerprint and return its id."""
    if remediation is None:
        remediation = RESTORE_INTENT_REMEDIATION
    with session_factory() as session:
        issue = save_known_issue(
            session,
            fingerprint=FINGERPRINT,
            cause="Description was changed",
            fix="Restore description",
            remediation=remediation,
        )
        if auto_apply_enabled:
            set_auto_apply_enabled(session, issue.id, True)
        session.commit()
        return issue.id


def _success_applier_fn(platform):
    def apply(remediation, drift, device, *, dry_run=False):
        return ApplyResult(
            transport="cli",
            rendered_commands="interface Ethernet1\n   description Uplink to core",
            dry_run_diff="- description old desc\n+ description Uplink to core",
            applied=True,
        )
    return apply


def _blocked_applier_fn(platform):
    def apply(remediation, drift, device, *, dry_run=False):
        raise RemediationBlockedError("Management interface blocked")
    return apply


def _failing_applier_fn(platform):
    def apply(remediation, drift, device, *, dry_run=False):
        raise RuntimeError("SSH connection refused")
    return apply


# ---------------------------------------------------------------------------
# Kill-switch and short-circuit tests
# ---------------------------------------------------------------------------

def test_kill_switch_off_returns_empty(monkeypatch, session_factory):
    monkeypatch.delenv("AUTO_REMEDIATION_ENABLED", raising=False)
    _make_issue(session_factory)
    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)
    assert result == []


def test_kill_switch_false_returns_empty(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "false")
    _make_issue(session_factory)
    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)
    assert result == []


def test_empty_drifts_returns_empty(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    result = run_auto_apply([], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)
    assert result == []


def test_per_device_pause_via_real_repository(monkeypatch, session_factory):
    # Verify the default is_device_paused_fn actually consults device_settings.
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)
    with session_factory() as session:
        set_device_paused(session, "core-sw-01", True, reason="test")
        session.commit()
    # No applier_fn override — uses the real _default_is_device_paused.
    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)
    assert result == []


def test_per_device_pause_returns_empty(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)
    result = run_auto_apply(
        [DRIFT], DEVICE, session_factory,
        applier_fn=_success_applier_fn,
        is_device_paused_fn=lambda name, session: True,
    )
    assert result == []


# ---------------------------------------------------------------------------
# No matching known issue
# ---------------------------------------------------------------------------

def test_no_matching_known_issue_skips(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    # No KnownIssue seeded at all.
    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)
    assert result == []


def test_matching_issue_auto_apply_disabled_skips(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory, auto_apply_enabled=False)
    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)
    assert result == []


# ---------------------------------------------------------------------------
# Kind gate — only restore_intent is auto-applied
# ---------------------------------------------------------------------------

def test_raw_snippet_kind_is_skipped(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory, remediation=RAW_SNIPPET_REMEDIATION)
    called = []
    def tracking_applier(platform):
        def apply(*a, **kw):
            called.append(1)
            return ApplyResult("cli", "", "", True)
        return apply
    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=tracking_applier)
    assert result == []
    assert called == []


def test_null_kind_is_skipped(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory, remediation={"kind": None})
    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)
    assert result == []


# ---------------------------------------------------------------------------
# Successful apply
# ---------------------------------------------------------------------------

def test_successful_apply_writes_remediation_event(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_id = _make_issue(session_factory)

    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_success_applier_fn)

    assert len(result) == 1
    outcome = result[0]
    assert isinstance(outcome, AutoApplyOutcome)
    assert outcome.result == "success"
    assert outcome.known_issue_id == issue_id
    assert outcome.platform == "arista_eos"

    # Verify the RemediationEvent row was actually written to the DB.
    from netdrift.storage.models import RemediationEvent
    with session_factory() as session:
        rows = session.query(RemediationEvent).all()
        assert len(rows) == 1
        assert rows[0].result == "success"
        assert rows[0].applied_by == "scheduler"
        assert "description" in rows[0].rendered_commands


def test_successful_apply_calls_applier_with_correct_args(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)

    calls = []
    def recording_applier(platform):
        def apply(remediation, drift, device, *, dry_run=False):
            calls.append((remediation, drift, device, dry_run))
            return ApplyResult("cli", "cmd", "", True)
        return apply

    run_auto_apply([DRIFT], DEVICE, session_factory, applier_fn=recording_applier)

    assert len(calls) == 1
    remediation, drift, device, dry_run = calls[0]
    assert remediation["kind"] == "restore_intent"
    assert drift["field"] == "description"
    assert device["name"] == "core-sw-01"
    assert dry_run is False


def test_successful_apply_triggers_repoll(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)

    repolled = []
    run_auto_apply(
        [DRIFT], DEVICE, session_factory,
        applier_fn=_success_applier_fn,
        schedule_repoll_fn=lambda d: repolled.append(d["name"]),
    )

    assert repolled == ["core-sw-01"]


# ---------------------------------------------------------------------------
# Blocked apply
# ---------------------------------------------------------------------------

def test_blocked_apply_writes_blocked_event(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_id = _make_issue(session_factory)

    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_blocked_applier_fn)

    assert len(result) == 1
    assert result[0].result == "blocked"
    assert result[0].known_issue_id == issue_id

    from netdrift.storage.models import RemediationEvent
    with session_factory() as session:
        row = session.query(RemediationEvent).one()
        assert row.result == "blocked"
        assert row.applied_by == "scheduler"


def test_blocked_apply_does_not_trigger_repoll(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)

    repolled = []
    run_auto_apply(
        [DRIFT], DEVICE, session_factory,
        applier_fn=_blocked_applier_fn,
        schedule_repoll_fn=lambda d: repolled.append(d),
    )
    assert repolled == []


def test_blocked_apply_does_not_disable_auto_apply(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_id = _make_issue(session_factory)

    # Blocked is not a failure — should not count toward the failure threshold.
    for _ in range(FAILURE_THRESHOLD + 1):
        run_auto_apply([DRIFT], DEVICE, session_factory,
                       applier_fn=_blocked_applier_fn)

    with session_factory() as session:
        issue = session.query(KnownIssue).filter_by(id=issue_id).one()
        assert issue.auto_apply_enabled is True


# ---------------------------------------------------------------------------
# Failed apply
# ---------------------------------------------------------------------------

def test_failed_apply_writes_failure_event(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_id = _make_issue(session_factory)

    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=_failing_applier_fn)

    assert len(result) == 1
    assert result[0].result == "failure"
    assert result[0].known_issue_id == issue_id

    from netdrift.storage.models import RemediationEvent
    with session_factory() as session:
        row = session.query(RemediationEvent).one()
        assert row.result == "failure"
        assert "RuntimeError" in row.rendered_commands


def test_failed_apply_does_not_trigger_repoll(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)

    repolled = []
    run_auto_apply(
        [DRIFT], DEVICE, session_factory,
        applier_fn=_failing_applier_fn,
        schedule_repoll_fn=lambda d: repolled.append(d),
    )
    assert repolled == []


# ---------------------------------------------------------------------------
# Consecutive-failure auto-disable
# ---------------------------------------------------------------------------

def test_failure_threshold_disables_auto_apply(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_id = _make_issue(session_factory)

    for _ in range(FAILURE_THRESHOLD):
        run_auto_apply([DRIFT], DEVICE, session_factory,
                       applier_fn=_failing_applier_fn)

    with session_factory() as session:
        issue = session.query(KnownIssue).filter_by(id=issue_id).one()
        assert issue.auto_apply_enabled is False


def test_success_resets_failure_streak(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_id = _make_issue(session_factory)

    # Two failures, then a success, then one more failure — should NOT disable.
    for _ in range(FAILURE_THRESHOLD - 1):
        run_auto_apply([DRIFT], DEVICE, session_factory,
                       applier_fn=_failing_applier_fn)
    run_auto_apply([DRIFT], DEVICE, session_factory, applier_fn=_success_applier_fn)
    run_auto_apply([DRIFT], DEVICE, session_factory, applier_fn=_failing_applier_fn)

    with session_factory() as session:
        issue = session.query(KnownIssue).filter_by(id=issue_id).one()
        assert issue.auto_apply_enabled is True


def test_fewer_than_threshold_failures_does_not_disable(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_id = _make_issue(session_factory)

    for _ in range(FAILURE_THRESHOLD - 1):
        run_auto_apply([DRIFT], DEVICE, session_factory,
                       applier_fn=_failing_applier_fn)

    with session_factory() as session:
        issue = session.query(KnownIssue).filter_by(id=issue_id).one()
        assert issue.auto_apply_enabled is True


# ---------------------------------------------------------------------------
# No registered applier for platform
# ---------------------------------------------------------------------------

def test_unknown_platform_skips_without_crashing(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)

    unknown_drift = {**DRIFT, "platform": "vendor_unknown"}

    def raising_applier(platform):
        raise KeyError(f"No applier for {platform!r}")

    result = run_auto_apply([unknown_drift], DEVICE, session_factory,
                            applier_fn=raising_applier)
    # Skipped silently — no RemediationEvent written for an unknown platform.
    assert result == []


# ---------------------------------------------------------------------------
# Multiple drifts — only matching ones applied
# ---------------------------------------------------------------------------

def test_only_matching_drift_is_applied(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)  # matches DRIFT

    unmatched_drift = {
        **DRIFT,
        "field": "enabled",
        "drift_kind": "value_mismatch",
        # fingerprint = "interface|enabled|value_mismatch" — no KnownIssue for this
    }

    calls = []
    def recording_applier(platform):
        def apply(remediation, drift, device, *, dry_run=False):
            calls.append(drift["field"])
            return ApplyResult("cli", "cmd", "", True)
        return apply

    result = run_auto_apply(
        [DRIFT, unmatched_drift], DEVICE, session_factory,
        applier_fn=recording_applier,
    )

    assert len(result) == 1
    assert calls == ["description"]


# ---------------------------------------------------------------------------
# Repoll not called when no successes
# ---------------------------------------------------------------------------

def test_repoll_not_called_when_no_drifts_match(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    # No KnownIssue seeded.
    repolled = []
    run_auto_apply(
        [DRIFT], DEVICE, session_factory,
        applier_fn=_success_applier_fn,
        schedule_repoll_fn=lambda d: repolled.append(d),
    )
    assert repolled == []


# ---------------------------------------------------------------------------
# Acknowledgement — acknowledged drift must not be auto-applied
# ---------------------------------------------------------------------------

def test_acknowledged_drift_is_skipped(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)

    # Acknowledge the fingerprint permanently (no expiry).
    with session_factory() as session:
        create_acknowledgement(
            session,
            device=DEVICE["name"],
            fingerprint="interface|description|value_mismatch",
        )
        session.commit()

    calls = []

    def recording_applier(platform):
        def apply(remediation, drift, device, *, dry_run=False):
            calls.append(drift["field"])
            return ApplyResult("cli", "cmd", "", True)
        return apply

    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=recording_applier)

    assert result == []
    assert calls == []


def test_expired_acknowledgement_does_not_block_apply(monkeypatch, session_factory):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    _make_issue(session_factory)

    # Acknowledgement expired in the past.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with session_factory() as session:
        create_acknowledgement(
            session,
            device=DEVICE["name"],
            fingerprint="interface|description|value_mismatch",
            acknowledged_until=past,
        )
        session.commit()

    calls = []

    def recording_applier(platform):
        def apply(remediation, drift, device, *, dry_run=False):
            calls.append(drift["field"])
            return ApplyResult("cli", "cmd", "", True)
        return apply

    result = run_auto_apply([DRIFT], DEVICE, session_factory,
                            applier_fn=recording_applier)

    # Expired ack — apply should proceed normally.
    assert len(result) == 1
    assert calls == ["description"]


# ---------------------------------------------------------------------------
# TEST1 — partial failure across multiple matched issues in one call.
# The most dangerous path: a single auto-apply cycle pushes config for two
# different known issues, one succeeds and one fails. We pin (a) that a failure
# mid-loop does not abort the remaining work, and (b) that the consecutive-
# failure disable is accounted per-KnownIssue — a co-processed success must not
# be contaminated by another issue's failure, nor vice versa.
# ---------------------------------------------------------------------------

_ENABLED_FINGERPRINT = "interface|enabled|value_mismatch"

_ENABLED_REMEDIATION = {
    "kind": "restore_intent",
    "schema_version": 1,
    "object_type": "interface",
    "field": "enabled",
    "drift_kinds": ["value_mismatch"],
}

# A second drift that matches a *different* known issue than DRIFT.
DRIFT_ENABLED = {
    **DRIFT,
    "object": "interface:Ethernet2",
    "field": "enabled",
}


def _make_enabled_issue(session_factory, *, auto_apply_enabled=True):
    """Seed a second KnownIssue matching DRIFT_ENABLED's fingerprint."""
    with session_factory() as session:
        issue = save_known_issue(
            session,
            fingerprint=_ENABLED_FINGERPRINT,
            cause="Interface was shut",
            fix="Re-enable interface",
            remediation=_ENABLED_REMEDIATION,
        )
        if auto_apply_enabled:
            set_auto_apply_enabled(session, issue.id, True)
        session.commit()
        return issue.id


def _selective_applier_fn(platform):
    """Succeed for the description drift, fail for the enabled drift."""
    def apply(remediation, drift, device, *, dry_run=False):
        if drift["field"] == "enabled":
            raise RuntimeError("SSH connection refused")
        return ApplyResult(
            transport="cli",
            rendered_commands="interface Ethernet1\n   description Uplink to core",
            dry_run_diff="diff",
            applied=True,
        )
    return apply


def test_partial_failure_processes_all_and_isolates_outcomes(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_ok = _make_issue(session_factory)            # interface|description → succeeds
    issue_bad = _make_enabled_issue(session_factory)   # interface|enabled → fails

    repolled = []
    result = run_auto_apply(
        [DRIFT, DRIFT_ENABLED], DEVICE, session_factory,
        applier_fn=_selective_applier_fn,
        schedule_repoll_fn=lambda d: repolled.append(d["name"]),
    )

    # The mid-loop failure did not abort the cycle — both issues were processed.
    assert {o.known_issue_id: o.result for o in result} == {
        issue_ok: "success",
        issue_bad: "failure",
    }

    # One RemediationEvent per matched issue, regardless of outcome.
    from netdrift.storage.models import RemediationEvent
    with session_factory() as session:
        rows = session.query(RemediationEvent).all()
        assert sorted(r.result for r in rows) == ["failure", "success"]

    # Exactly one success → exactly one repoll.
    assert repolled == ["core-sw-01"]

    # Neither issue is disabled: the success stays on, the single failure is
    # below the threshold.
    with session_factory() as session:
        assert session.query(KnownIssue).filter_by(id=issue_ok).one().auto_apply_enabled is True
        assert session.query(KnownIssue).filter_by(id=issue_bad).one().auto_apply_enabled is True


def test_partial_failure_disable_threshold_is_per_issue(monkeypatch, session_factory):
    monkeypatch.setenv("AUTO_REMEDIATION_ENABLED", "true")
    issue_ok = _make_issue(session_factory)            # succeeds this cycle
    issue_bad = _make_enabled_issue(session_factory)   # fails this cycle (its 3rd)

    # Pre-seed the failing issue with FAILURE_THRESHOLD-1 prior scheduler failures
    # so this cycle's failure is the one that trips the threshold.
    with session_factory() as session:
        for _ in range(FAILURE_THRESHOLD - 1):
            save_remediation_event(
                session, issue_bad, "arista_eos", "", "", "failure", "scheduler",
            )
        session.commit()

    run_auto_apply(
        [DRIFT, DRIFT_ENABLED], DEVICE, session_factory,
        applier_fn=_selective_applier_fn,
    )

    with session_factory() as session:
        # The failing issue tripped its own threshold and is disabled...
        assert session.query(KnownIssue).filter_by(id=issue_bad).one().auto_apply_enabled is False
        # ...but the issue that succeeded in the same call is untouched: the
        # failure counter is not shared across known issues.
        assert session.query(KnownIssue).filter_by(id=issue_ok).one().auto_apply_enabled is True
