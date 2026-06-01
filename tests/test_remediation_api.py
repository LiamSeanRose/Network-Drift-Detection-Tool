"""tests/test_remediation_api.py — v2.5 remediation API tests.

Tests the dry-run, apply, auto-apply gate, and audit-log endpoints using
FastAPI's TestClient with an in-memory SQLite database and a fake applier
injected via the applier registry. No real device or NAPALM required.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.api.app import app, get_session
from netdrift.appliers import base as applier_base
from netdrift.appliers import registry as applier_registry
from netdrift.storage.models import Base
from netdrift.storage.repository import (
    confirmed_count,
    save_drifts,
    save_known_issue,
    save_remediation_event,
)


# ---------------------------------------------------------------------------
# Helpers — fake applier
# ---------------------------------------------------------------------------

def _make_fake_applier(*, rendered="interface Ethernet1\n no shutdown", diff="+ no shutdown"):
    """Return an Applier-protocol-compatible callable for testing."""
    def apply(remediation, drift, device, *, dry_run=False):
        return applier_base.ApplyResult(
            transport="cli",
            rendered_commands=rendered,
            dry_run_diff=diff,
            applied=not dry_run,
        )
    apply.__name__ = "fake_apply"
    apply.__qualname__ = "fake_apply"
    return apply


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_applier_registry():
    """Reset the applier registry around each test."""
    applier_base._reset_registry()
    applier_registry._reset()
    yield
    applier_base._reset_registry()
    applier_registry._reset()


@pytest.fixture
def db_session():
    """Yield a session on a fresh in-memory SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


@pytest.fixture
def client(db_session, monkeypatch):
    """TestClient with seeded data, fake applier, and patched devices.yml loader."""
    # Seed drift events (with platform so remediate endpoints can dispatch)
    save_drifts(db_session, [
        {
            "device": "core-sw-01",
            "object": "interface:Ethernet1",
            "field": "enabled",
            "intent": True,
            "reality": False,
            "drift_kind": "value_mismatch",
            "severity": "critical",
            "detected_at": "2026-05-31T10:00:00Z",
            "platform": "arista_eos",
        },
    ])
    db_session.commit()

    # Register a fake applier for arista_eos
    applier_base.register("arista_eos")(_make_fake_applier())

    # Patch _load_devices so the API doesn't require devices.yml on disk
    monkeypatch.setattr(
        "netdrift.api.app._devices_cache",
        {"core-sw-01": {"hostname": "127.0.0.1", "username": "admin", "password": "test"}},
    )

    engine = db_session.get_bind()
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers to create fixture data via client
# ---------------------------------------------------------------------------

def _create_known_issue(client, remediation=None):
    payload = {
        "object": "interface:Ethernet1",
        "field": "enabled",
        "drift_kind": "value_mismatch",
        "cause": "Interface manually shut",
        "fix": "Re-enable with 'no shutdown'",
    }
    if remediation is not None:
        payload["remediation"] = remediation
    return client.post("/known-issues", json=payload)


# ---------------------------------------------------------------------------
# POST /known-issues — v2.5 response shape
# ---------------------------------------------------------------------------

def test_create_known_issue_confirmed_count_starts_at_zero(client):
    resp = _create_known_issue(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["confirmed_count"] == 0
    assert data["remediation"] is None
    assert data["auto_apply_enabled"] is False


def test_create_known_issue_with_restore_intent_remediation(client):
    remediation = {
        "kind": "restore_intent",
        "schema_version": 1,
        "object_type": "interface",
        "field": "enabled",
        "drift_kinds": ["value_mismatch"],
    }
    resp = _create_known_issue(client, remediation=remediation)
    assert resp.status_code == 200
    assert resp.json()["remediation"]["kind"] == "restore_intent"


def test_create_known_issue_rejects_invalid_kind(client):
    resp = _create_known_issue(client, remediation={"kind": "telekinesis"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /known-issues/{id} — update remediation payload
# ---------------------------------------------------------------------------

def test_patch_known_issue_sets_remediation(client):
    issue_id = _create_known_issue(client).json()["id"]
    remediation = {
        "kind": "restore_intent",
        "schema_version": 1,
        "object_type": "interface",
        "field": "enabled",
        "drift_kinds": ["value_mismatch"],
    }
    resp = client.patch(f"/known-issues/{issue_id}", json={"remediation": remediation})
    assert resp.status_code == 200
    assert resp.json()["remediation"]["kind"] == "restore_intent"


def test_patch_known_issue_clears_auto_apply_when_kind_becomes_null(client):
    # Create with restore_intent, enable auto_apply via monkeypatching the threshold, then clear
    # This test just checks that clearing remediation also clears auto_apply_enabled.
    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    resp = client.patch(f"/known-issues/{issue_id}", json={"remediation": None})
    assert resp.status_code == 200
    data = resp.json()
    assert data["remediation"] is None
    assert data["auto_apply_enabled"] is False


def test_patch_known_issue_404_on_missing(client):
    resp = client.patch("/known-issues/9999", json={"remediation": None})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /known-issues/{id}/auto-apply
# ---------------------------------------------------------------------------

def test_auto_apply_rejected_when_kind_not_restore_intent(client):
    issue_id = _create_known_issue(client).json()["id"]
    resp = client.patch(f"/known-issues/{issue_id}/auto-apply", json={"enabled": True})
    assert resp.status_code == 422
    assert "restore_intent" in resp.json()["detail"]


def test_auto_apply_rejected_when_confirmed_count_below_threshold(client, monkeypatch):
    monkeypatch.setattr("netdrift.api.app.AUTO_REMEDIATION_ENABLED", True)
    monkeypatch.setattr("netdrift.api.app.CONFIRM_THRESHOLD", 3)

    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    resp = client.patch(f"/known-issues/{issue_id}/auto-apply", json={"enabled": True})
    assert resp.status_code == 422
    assert "confirmed" in resp.json()["detail"]


def test_auto_apply_rejected_when_kill_switch_off(client, monkeypatch):
    monkeypatch.setattr("netdrift.api.app.AUTO_REMEDIATION_ENABLED", False)
    monkeypatch.setattr("netdrift.api.app.CONFIRM_THRESHOLD", 0)

    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    resp = client.patch(f"/known-issues/{issue_id}/auto-apply", json={"enabled": True})
    assert resp.status_code == 422
    assert "kill-switch" in resp.json()["detail"]


def test_auto_apply_enabled_when_all_gates_pass(client, monkeypatch, db_session):
    monkeypatch.setattr("netdrift.api.app.AUTO_REMEDIATION_ENABLED", True)
    monkeypatch.setattr("netdrift.api.app.CONFIRM_THRESHOLD", 2)

    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    # Manually insert 2 success events to meet threshold
    for _ in range(2):
        save_remediation_event(
            db_session, issue_id, "arista_eos", "cmd", "diff", "success", "api"
        )
    db_session.commit()

    resp = client.patch(f"/known-issues/{issue_id}/auto-apply", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["auto_apply_enabled"] is True


# ---------------------------------------------------------------------------
# POST /known-issues/{id}/remediate/dry-run
# ---------------------------------------------------------------------------

def test_dry_run_returns_diff(client):
    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    drift_id = client.get("/drifts").json()[0]["id"]
    resp = client.post(
        f"/known-issues/{issue_id}/remediate/dry-run",
        json={"drift_event_id": drift_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["would_apply"] is False
    assert data["transport"] == "cli"
    assert "rendered_commands" in data
    assert "dry_run_diff" in data


def test_dry_run_records_audit_event(client):
    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    drift_id = client.get("/drifts").json()[0]["id"]
    client.post(
        f"/known-issues/{issue_id}/remediate/dry-run",
        json={"drift_event_id": drift_id},
    )

    events = client.get(f"/known-issues/{issue_id}/remediation-events").json()
    assert len(events) == 1
    assert events[0]["result"] == "dry_run_only"


def test_dry_run_rejects_null_kind(client):
    issue_id = _create_known_issue(client).json()["id"]  # no remediation
    drift_id = client.get("/drifts").json()[0]["id"]
    resp = client.post(
        f"/known-issues/{issue_id}/remediate/dry-run",
        json={"drift_event_id": drift_id},
    )
    assert resp.status_code == 422


def test_dry_run_blocks_symptom_fields(client, monkeypatch):
    """check_blocked should reject drift on session_state / adjacency_state."""
    def raise_blocked(drift, device):
        raise applier_base.RemediationBlockedError("blocked: symptom field")

    monkeypatch.setattr("netdrift.api.app.check_blocked", raise_blocked)

    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "bgp_neighbor", "field": "session_state", "drift_kinds": ["value_mismatch"],
    }).json()["id"]
    drift_id = client.get("/drifts").json()[0]["id"]
    resp = client.post(
        f"/known-issues/{issue_id}/remediate/dry-run",
        json={"drift_event_id": drift_id},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /known-issues/{id}/remediate/apply
# ---------------------------------------------------------------------------

def test_apply_records_success_event(client):
    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    drift_id = client.get("/drifts").json()[0]["id"]
    resp = client.post(
        f"/known-issues/{issue_id}/remediate/apply",
        json={"drift_event_id": drift_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"] is True
    assert "remediation_event_id" in data


def test_apply_increments_confirmed_count(client):
    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    drift_id = client.get("/drifts").json()[0]["id"]
    client.post(
        f"/known-issues/{issue_id}/remediate/apply",
        json={"drift_event_id": drift_id},
    )

    issues = client.get("/known-issues").json()
    issue = next(i for i in issues if i["id"] == issue_id)
    assert issue["confirmed_count"] == 1


def test_apply_records_failure_event_on_applier_error(client, monkeypatch):
    def failing_apply(remediation, drift, device, *, dry_run=False):
        raise RuntimeError("NAPALM connection refused")

    applier_base._reset_registry()
    applier_registry._reset()
    applier_base.register("arista_eos")(failing_apply)

    issue_id = _create_known_issue(client, remediation={
        "kind": "restore_intent", "schema_version": 1,
        "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
    }).json()["id"]

    drift_id = client.get("/drifts").json()[0]["id"]
    resp = client.post(
        f"/known-issues/{issue_id}/remediate/apply",
        json={"drift_event_id": drift_id},
    )
    assert resp.status_code == 502

    events = client.get(f"/known-issues/{issue_id}/remediation-events").json()
    assert len(events) == 1
    assert events[0]["result"] == "failure"


# ---------------------------------------------------------------------------
# v3.0 — API-path webhook dispatch on apply results
# ---------------------------------------------------------------------------

class _FakeDispatcher:
    """Records fire() calls regardless of WEBHOOK_URL, so the test can assert
    dispatch happened without standing up a real receiver."""

    def __init__(self):
        self.fired = []

    def fire(self, event_type, payload):
        self.fired.append((event_type, payload))


_RESTORE_INTENT = {
    "kind": "restore_intent", "schema_version": 1,
    "object_type": "interface", "field": "enabled", "drift_kinds": ["value_mismatch"],
}


def test_apply_success_fires_apply_success_webhook(client, monkeypatch):
    fake = _FakeDispatcher()
    monkeypatch.setattr("netdrift.api.app._webhook_dispatcher", fake)

    issue_id = _create_known_issue(client, remediation=_RESTORE_INTENT).json()["id"]
    drift_id = client.get("/drifts").json()[0]["id"]
    resp = client.post(
        f"/known-issues/{issue_id}/remediate/apply",
        json={"drift_event_id": drift_id},
    )
    assert resp.status_code == 200

    # TestClient runs BackgroundTasks synchronously after the response.
    events = [e for e, _ in fake.fired]
    assert "apply_success" in events
    payload = next(p for e, p in fake.fired if e == "apply_success")
    assert payload["device"] == "core-sw-01"
    assert "timestamp" in payload and "detail" in payload


def test_apply_failure_fires_apply_failure_webhook(client, monkeypatch):
    def failing_apply(remediation, drift, device, *, dry_run=False):
        raise RuntimeError("NAPALM connection refused")

    applier_base._reset_registry()
    applier_registry._reset()
    applier_base.register("arista_eos")(failing_apply)

    fake = _FakeDispatcher()
    monkeypatch.setattr("netdrift.api.app._webhook_dispatcher", fake)

    issue_id = _create_known_issue(client, remediation=_RESTORE_INTENT).json()["id"]
    drift_id = client.get("/drifts").json()[0]["id"]
    resp = client.post(
        f"/known-issues/{issue_id}/remediate/apply",
        json={"drift_event_id": drift_id},
    )
    assert resp.status_code == 502  # failure still surfaces as 502

    # Failure fires directly (the 502 short-circuits BackgroundTasks).
    events = [e for e, _ in fake.fired]
    assert "apply_failure" in events


# ---------------------------------------------------------------------------
# GET /known-issues/{id}/remediation-events
# ---------------------------------------------------------------------------

def test_remediation_events_empty_initially(client):
    issue_id = _create_known_issue(client).json()["id"]
    resp = client.get(f"/known-issues/{issue_id}/remediation-events")
    assert resp.status_code == 200
    assert resp.json() == []


def test_remediation_events_404_on_missing_issue(client):
    resp = client.get("/known-issues/9999/remediation-events")
    assert resp.status_code == 404


def test_remediation_events_returned_newest_first(client, db_session):
    issue_id = _create_known_issue(client).json()["id"]
    for result in ["success", "failure", "dry_run_only"]:
        save_remediation_event(
            db_session, issue_id, "arista_eos", "cmd", "diff", result, "api"
        )
    db_session.commit()

    events = client.get(f"/known-issues/{issue_id}/remediation-events").json()
    assert len(events) == 3
    # Newest first — last inserted should be first in response
    assert events[0]["result"] == "dry_run_only"


# ---------------------------------------------------------------------------
# confirmed_count derivation
# ---------------------------------------------------------------------------

def test_confirmed_count_only_counts_successes(db_session):
    with db_session as s:
        issue = save_known_issue(s, "obj_type|field|kind", "cause", "fix")
        s.commit()
        save_remediation_event(s, issue.id, "arista_eos", "", "", "success", "api")
        save_remediation_event(s, issue.id, "arista_eos", "", "", "failure", "api")
        save_remediation_event(s, issue.id, "arista_eos", "", "", "dry_run_only", "api")
        s.commit()
        assert confirmed_count(s, issue.id) == 1
