"""tests/test_api.py — API endpoint tests (v0.2).

Uses FastAPI's TestClient, which calls endpoints in-process — no running
uvicorn, no network. The app's get_session dependency is overridden to hand
out sessions on an in-memory SQLite database (same approach as the storage
tests), so the API is exercised end-to-end with zero infrastructure.
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.api.app import app, get_session
from netdrift.storage.models import Base
from netdrift.storage.repository import create_api_key, save_drifts


@pytest.fixture
def client():
    """A TestClient whose get_session dependency points at a fresh in-memory
    SQLite database, seeded with two drift events.

    NOTE: ':memory:' gives each new connection its own empty database. To make
    the seeded table visible to the endpoint (which opens its own session), we
    pin the engine to ONE shared connection via StaticPool — so the create,
    the seed, and the endpoint query all hit the same in-memory database.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    # Seed two events so the endpoint has something to return.
    with TestingSession() as s:
        save_drifts(s, [
            {"device": "core-sw-01", "object": "interface:Ethernet2",
             "field": "untagged_vlan", "intent": 10, "reality": 99,
             "drift_kind": "value_mismatch", "severity": "warning",
             "detected_at": "2026-05-26T14:32:00Z"},
            {"device": "core-sw-02", "object": "vlan:20", "field": "name",
             "intent": "voice", "reality": "Voice-VLAN",
             "drift_kind": "value_mismatch", "severity": "info",
             "detected_at": "2026-05-26T14:32:01Z"},
        ])
        # v3.5: mutating endpoints require X-API-Key; seed a key for this client.
        raw_key, _ = create_api_key(s, name="test")
        s.commit()

    # Override the app's real session dependency with the test database.
    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app, headers={"X-API-Key": raw_key})
    app.dependency_overrides.clear()  # cleanup so tests don't leak


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_drifts_returns_all_events(client):
    response = client.get("/drifts")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_drifts_preserves_json_types(client):
    response = client.get("/drifts")
    events = response.json()
    # Find the untagged_vlan event; its intent must be the integer 10.
    vlan_event = next(e for e in events if e["field"] == "untagged_vlan")
    assert vlan_event["intent"] == 10
    assert isinstance(vlan_event["intent"], int)


def test_drifts_includes_platform_field(client):
    response = client.get("/drifts")
    for event in response.json():
        assert "platform" in event


def test_drifts_filters_by_device(client):
    response = client.get("/drifts?device=core-sw-02")
    events = response.json()
    assert len(events) == 1
    assert events[0]["device"] == "core-sw-02"


def test_drifts_respects_limit(client):
    response = client.get("/drifts?limit=1")
    assert len(response.json()) == 1


def test_drifts_includes_causes(client):
    response = client.get("/drifts")
    for event in response.json():
        assert "causes" in event
        assert isinstance(event["causes"], list)


def test_suggested_fix_returns_cause_and_fix(client):
    # AI3: the endpoint suggests a cause + fix (deterministic by default).
    event_id = client.get("/drifts").json()[0]["id"]
    resp = client.get(f"/drifts/{event_id}/suggested-fix")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cause"]
    assert body["fix"]
    assert body["source"] == "deterministic"


def test_suggested_fix_404_for_unknown_event(client):
    assert client.get("/drifts/999999/suggested-fix").status_code == 404


def test_drifts_explanation_null_by_default(client):
    # With no explanation generated (the default), every event reports null.
    response = client.get("/drifts")
    for event in response.json():
        assert "explanation" in event
        assert event["explanation"] is None


def test_drifts_includes_explanation_when_present():
    # Build a dedicated client whose DB has an explanation for the untagged_vlan
    # drift's fingerprint, and assert it surfaces on that event only.
    from netdrift.storage.repository import upsert_explanation

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as s:
        save_drifts(s, [
            {"device": "core-sw-01", "object": "interface:Ethernet2",
             "field": "untagged_vlan", "intent": 10, "reality": 99,
             "drift_kind": "value_mismatch", "severity": "warning",
             "detected_at": "2026-05-26T14:32:00Z"},
        ])
        upsert_explanation(
            s, "interface|untagged_vlan|value_mismatch",
            "The access VLAN was changed on the device after a move.", "llm",
        )
        s.commit()

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        event = client.get("/drifts").json()[0]
        assert event["explanation"]["source"] == "llm"
        assert "access VLAN" in event["explanation"]["text"]
        assert "generated_at" in event["explanation"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /drifts/history tests
# ---------------------------------------------------------------------------

@pytest.fixture
def history_client():
    """TestClient seeded with recent drift events (within the 24-hour window).

    The main `client` fixture uses 2026-05-26 timestamps, which are outside a
    24-hour window. This fixture seeds three events timestamped now so the
    history endpoint actually returns data.
    """
    now = datetime.now(tz=timezone.utc).isoformat()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as s:
        save_drifts(s, [
            {"device": "core-sw-01", "object": "interface:Ethernet1",
             "field": "enabled", "intent": True, "reality": False,
             "drift_kind": "value_mismatch", "severity": "critical",
             "detected_at": now},
            {"device": "core-sw-01", "object": "interface:Ethernet2",
             "field": "description", "intent": "uplink", "reality": "old",
             "drift_kind": "value_mismatch", "severity": "warning",
             "detected_at": now},
            {"device": "core-sw-02", "object": "vlan:10",
             "field": "name", "intent": "users", "reality": "Users",
             "drift_kind": "value_mismatch", "severity": "info",
             "detected_at": now},
        ])
        s.commit()

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_history_returns_ok(history_client):
    response = history_client.get("/drifts/history")
    assert response.status_code == 200


def test_history_response_structure(history_client):
    response = history_client.get("/drifts/history")
    data = response.json()
    assert isinstance(data, list)
    # Two devices in the same bucket → 2 entries.
    assert len(data) == 2
    entry = next(e for e in data if e["device"] == "core-sw-01")
    assert entry["count"] == 2
    assert entry["critical"] == 1
    assert entry["warning"] == 1
    assert "detected_at" in entry


def test_history_filters_by_device(history_client):
    response = history_client.get("/drifts/history?device=core-sw-01")
    data = response.json()
    assert all(e["device"] == "core-sw-01" for e in data)
    assert len(data) == 1


def test_history_empty_outside_window(client):
    # The main client fixture seeds events from 2026-05-26 (>24 h ago).
    response = client.get("/drifts/history")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# POST /known-issues and GET /known-issues tests
# ---------------------------------------------------------------------------

def test_post_known_issue_creates_record(client):
    payload = {
        "object": "interface:Ethernet1",
        "field": "enabled",
        "drift_kind": "value_mismatch",
        "cause": "Interface manually shut",
        "fix": "Re-enable with 'no shutdown'",
    }
    response = client.post("/known-issues", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["fingerprint"] == "interface|enabled|value_mismatch"
    assert data["cause"] == "Interface manually shut"
    assert data["fix"] == "Re-enable with 'no shutdown'"
    assert data["confirmed_count"] == 0


def test_get_known_issues_empty(client):
    response = client.get("/known-issues")
    assert response.status_code == 200
    assert response.json() == []


def test_get_known_issues_returns_all(client):
    client.post("/known-issues", json={
        "object": "interface:Ethernet1", "field": "enabled",
        "drift_kind": "value_mismatch", "cause": "Cause A", "fix": "Fix A",
    })
    client.post("/known-issues", json={
        "object": "bgp_neighbor:10.0.0.1", "field": "session_state",
        "drift_kind": "value_mismatch", "cause": "Cause B", "fix": "Fix B",
    })
    response = client.get("/known-issues")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_drifts_known_fix_null_when_no_match(client):
    response = client.get("/drifts")
    for event in response.json():
        assert event["known_fix"] is None


def test_drifts_includes_known_fix_when_match(client):
    # Seed a known issue matching the first seeded drift event:
    # interface:Ethernet2 + untagged_vlan + value_mismatch
    # → fingerprint: interface|untagged_vlan|value_mismatch
    client.post("/known-issues", json={
        "object": "interface:Ethernet2",
        "field": "untagged_vlan",
        "drift_kind": "value_mismatch",
        "cause": "VLAN changed on device",
        "fix": "Update device VLAN or correct NetBox",
    })
    response = client.get("/drifts")
    events = response.json()

    matching = next(e for e in events if e["field"] == "untagged_vlan")
    assert matching["known_fix"] is not None
    assert matching["known_fix"]["cause"] == "VLAN changed on device"
    assert matching["known_fix"]["fix"] == "Update device VLAN or correct NetBox"

    non_matching = next(e for e in events if e["field"] == "name")
    assert non_matching["known_fix"] is None


# ---------------------------------------------------------------------------
# GET /drifts?since= filter + Link pagination (v3.5)
# ---------------------------------------------------------------------------
# The `client` fixture seeds two events, both timestamped 2026-05-26 14:32.

def test_drifts_since_excludes_older_events(client):
    resp = client.get("/drifts?since=2026-05-27T00:00:00Z")
    assert resp.status_code == 200
    assert resp.json() == []


def test_drifts_since_includes_newer_events(client):
    resp = client.get("/drifts?since=2026-05-25T00:00:00Z")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_drifts_since_invalid_format_returns_422(client):
    resp = client.get("/drifts?since=not-a-date")
    assert resp.status_code == 422


def test_drifts_pagination_emits_link_next_header(client):
    resp = client.get("/drifts?limit=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    link = resp.headers.get("Link")
    assert link is not None
    assert 'rel="next"' in link
    assert "offset=1" in link


def test_drifts_pagination_offset_returns_next_page(client):
    first = client.get("/drifts?limit=1").json()
    second = client.get("/drifts?limit=1&offset=1").json()
    assert len(second) == 1
    assert first[0]["id"] != second[0]["id"]


def test_drifts_no_link_header_on_last_page(client):
    # limit exceeds the 2 seeded events → no next page.
    resp = client.get("/drifts?limit=100")
    assert "Link" not in resp.headers


# ---------------------------------------------------------------------------
# SEC2 — bound limit/hours so an unauthenticated client can't force a
# full-history scan into memory (?hours=876000, ?limit=999999).
# ---------------------------------------------------------------------------

def test_drifts_limit_below_minimum_returns_422(client):
    assert client.get("/drifts?limit=0").status_code == 422


def test_drifts_limit_above_maximum_returns_422(client):
    assert client.get("/drifts?limit=99999").status_code == 422


def test_drifts_offset_negative_returns_422(client):
    assert client.get("/drifts?offset=-1").status_code == 422


def test_drifts_limit_at_upper_bound_is_accepted(client):
    assert client.get("/drifts?limit=1000").status_code == 200


def test_history_hours_above_maximum_returns_422(client):
    assert client.get("/drifts/history?hours=999999").status_code == 422


def test_history_hours_below_minimum_returns_422(client):
    assert client.get("/drifts/history?hours=0").status_code == 422


def test_history_hours_at_upper_bound_is_accepted(client):
    assert client.get("/drifts/history?hours=168").status_code == 200


# ---------------------------------------------------------------------------
# SEC1 — pin CORS posture. The origin list is parsed from CORS_ALLOW_ORIGINS;
# a wildcard must never be honored while the mutating API is unauthenticated.
# ---------------------------------------------------------------------------

def test_parse_cors_origins_empty_is_no_cross_origin():
    from netdrift.api.app import _parse_cors_origins
    assert _parse_cors_origins("") == []
    assert _parse_cors_origins(None) == []


def test_parse_cors_origins_parses_comma_separated_list():
    from netdrift.api.app import _parse_cors_origins
    assert _parse_cors_origins("https://a.example, https://b.example") == [
        "https://a.example",
        "https://b.example",
    ]


def test_parse_cors_origins_drops_wildcard():
    from netdrift.api.app import _parse_cors_origins
    assert _parse_cors_origins("*") == []
    assert _parse_cors_origins("https://a.example, *") == ["https://a.example"]