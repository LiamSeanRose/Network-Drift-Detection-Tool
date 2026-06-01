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
from netdrift.storage.repository import save_known_issue, set_auto_apply_enabled, set_device_paused


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
