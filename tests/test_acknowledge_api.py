"""tests/test_acknowledge_api.py — POST /drifts/{id}/acknowledge (v3.5).

Acknowledging a drift event records an acknowledgement for that event's
(device, fingerprint) so future occurrences stop alerting. The endpoint derives
the fingerprint from the event (the ack is not tied to the row id, which changes
every poll). Past expiries and windows longer than the 90-day max are rejected
with 422. Auth-gated by the X-API-Key middleware.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.api.app import app, get_session
from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.storage.models import Base
from netdrift.storage.repository import create_api_key, is_acknowledged, save_drifts

DRIFT = {
    "device": "core-sw-01", "object": "interface:Ethernet1", "field": "enabled",
    "intent": True, "reality": False, "drift_kind": "value_mismatch",
    "severity": "critical", "detected_at": "2026-05-31T10:00:00Z",
    "platform": "arista_eos",
}
EXPECTED_FP = make_fingerprint(
    {"object": DRIFT["object"], "field": DRIFT["field"], "drift_kind": DRIFT["drift_kind"]}
)


@pytest.fixture
def ctx():
    """Yield (client, TestingSession). Client sends a valid key by default."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as s:
        events = save_drifts(s, [DRIFT])
        global _EVENT_ID
        _EVENT_ID = events[0].id
        raw_key, _ = create_api_key(s, name="test")
        s.commit()

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app, headers={"X-API-Key": raw_key}), TestingSession, _EVENT_ID
    app.dependency_overrides.clear()


def _future(days):
    return (datetime.now(tz=timezone.utc) + timedelta(days=days)).isoformat()


def test_acknowledge_future_expiry_creates_active_ack(ctx):
    client, TestingSession, event_id = ctx
    resp = client.post(f"/drifts/{event_id}/acknowledge",
                       json={"acknowledged_until": _future(30)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["device"] == "core-sw-01"
    assert body["fingerprint"] == EXPECTED_FP
    # The ack is active for this (device, fingerprint).
    with TestingSession() as s:
        assert is_acknowledged(s, "core-sw-01", EXPECTED_FP) is True


def test_acknowledge_null_expiry_is_permanent(ctx):
    client, TestingSession, event_id = ctx
    resp = client.post(f"/drifts/{event_id}/acknowledge",
                       json={"acknowledged_until": None})
    assert resp.status_code == 200
    assert resp.json()["acknowledged_until"] is None


def test_acknowledge_past_expiry_returns_422(ctx):
    client, _, event_id = ctx
    past = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
    resp = client.post(f"/drifts/{event_id}/acknowledge",
                       json={"acknowledged_until": past})
    assert resp.status_code == 422


def test_acknowledge_beyond_max_window_returns_422(ctx):
    client, _, event_id = ctx
    resp = client.post(f"/drifts/{event_id}/acknowledge",
                       json={"acknowledged_until": _future(91)})
    assert resp.status_code == 422


def test_acknowledge_unknown_drift_returns_404(ctx):
    client, _, _ = ctx
    resp = client.post("/drifts/9999/acknowledge",
                       json={"acknowledged_until": None})
    assert resp.status_code == 404


def test_acknowledge_requires_api_key(ctx):
    client, _, event_id = ctx
    resp = client.post(f"/drifts/{event_id}/acknowledge",
                       json={"acknowledged_until": None},
                       headers={"X-API-Key": ""})
    assert resp.status_code == 401


def test_get_drifts_acknowledged_false_before_ack(ctx):
    client, _, _ = ctx
    rows = client.get("/drifts").json()
    assert rows  # the fixture seeded one event
    assert all(r["acknowledged"] is False for r in rows)


def test_get_drifts_marks_acknowledged_after_ack(ctx):
    client, _, event_id = ctx
    client.post(f"/drifts/{event_id}/acknowledge", json={"acknowledged_until": None})
    rows = client.get("/drifts").json()
    matching = [r for r in rows if r["id"] == event_id]
    assert matching and matching[0]["acknowledged"] is True


# ---------------------------------------------------------------------------
# DELETE /drifts/{id}/acknowledge — un-acknowledge (toggle off)
# ---------------------------------------------------------------------------

def test_unacknowledge_clears_the_acknowledgement(ctx):
    client, TestingSession, event_id = ctx
    client.post(f"/drifts/{event_id}/acknowledge", json={"acknowledged_until": None})

    resp = client.delete(f"/drifts/{event_id}/acknowledge")
    assert resp.status_code == 200
    assert resp.json()["deleted"] >= 1

    with TestingSession() as s:
        assert is_acknowledged(s, "core-sw-01", EXPECTED_FP) is False


def test_unacknowledge_reflected_in_get_drifts(ctx):
    client, _, event_id = ctx
    client.post(f"/drifts/{event_id}/acknowledge", json={"acknowledged_until": None})
    client.delete(f"/drifts/{event_id}/acknowledge")
    rows = client.get("/drifts").json()
    matching = [r for r in rows if r["id"] == event_id]
    assert matching and matching[0]["acknowledged"] is False


def test_unacknowledge_unknown_drift_returns_404(ctx):
    client, _, _ = ctx
    assert client.delete("/drifts/9999/acknowledge").status_code == 404


def test_unacknowledge_requires_api_key(ctx):
    client, _, event_id = ctx
    resp = client.delete(f"/drifts/{event_id}/acknowledge", headers={"X-API-Key": ""})
    assert resp.status_code == 401
