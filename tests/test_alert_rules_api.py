"""tests/test_alert_rules_api.py — /alert-rules endpoints (v3.5).

CRUD for SLA alert rules. POST validates severity and window_minutes; GET lists;
DELETE removes. Mutating verbs are auth-gated by the X-API-Key middleware.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.api.app import app, get_session
from netdrift.storage.models import Base
from netdrift.storage.repository import create_api_key


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as s:
        raw_key, _ = create_api_key(s, name="test")
        s.commit()

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app, headers={"X-API-Key": raw_key})
    app.dependency_overrides.clear()


def test_post_creates_rule(client):
    resp = client.post("/alert-rules", json={
        "device": "core-sw-01", "severity": "critical", "window_minutes": 10,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["device"] == "core-sw-01"
    assert body["severity"] == "critical"
    assert body["window_minutes"] == 10
    assert body["enabled"] is True
    assert "id" in body and "created_at" in body


def test_post_allows_null_device_for_all_devices(client):
    resp = client.post("/alert-rules", json={
        "device": None, "severity": "warning", "window_minutes": 30,
    })
    assert resp.status_code == 200
    assert resp.json()["device"] is None


def test_post_creates_object_type_scoped_rule(client):
    resp = client.post("/alert-rules", json={
        "severity": "critical", "window_minutes": 10, "object_type": "interface",
    })
    assert resp.status_code == 200
    assert resp.json()["object_type"] == "interface"


def test_post_defaults_object_type_to_null(client):
    resp = client.post("/alert-rules", json={
        "severity": "critical", "window_minutes": 10,
    })
    assert resp.status_code == 200
    assert resp.json()["object_type"] is None


def test_post_rejects_invalid_object_type(client):
    resp = client.post("/alert-rules", json={
        "severity": "critical", "window_minutes": 10, "object_type": "bogus",
    })
    assert resp.status_code == 422


def test_post_rejects_invalid_severity(client):
    resp = client.post("/alert-rules", json={
        "severity": "catastrophic", "window_minutes": 10,
    })
    assert resp.status_code == 422


def test_post_rejects_non_positive_window(client):
    resp = client.post("/alert-rules", json={
        "severity": "critical", "window_minutes": 0,
    })
    assert resp.status_code == 422


def test_get_lists_rules(client):
    client.post("/alert-rules", json={"severity": "critical", "window_minutes": 10})
    client.post("/alert-rules", json={"severity": "warning", "window_minutes": 30})
    resp = client.get("/alert-rules")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_delete_removes_rule(client):
    rule_id = client.post(
        "/alert-rules", json={"severity": "critical", "window_minutes": 10}
    ).json()["id"]
    assert client.delete(f"/alert-rules/{rule_id}").status_code == 200
    assert client.get("/alert-rules").json() == []


def test_delete_unknown_returns_404(client):
    assert client.delete("/alert-rules/9999").status_code == 404


def test_post_requires_api_key(client):
    resp = client.post(
        "/alert-rules", json={"severity": "critical", "window_minutes": 10},
        headers={"X-API-Key": ""},
    )
    assert resp.status_code == 401
