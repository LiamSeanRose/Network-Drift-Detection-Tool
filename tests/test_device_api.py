"""tests/test_device_api.py — PATCH /devices/{name}/auto-apply endpoint.

The per-device runtime kill-switch surface. API-only in v3.0 (the dashboard
toggle lands in v3.5). Uses TestClient over in-memory SQLite, with the
devices.yml loader monkeypatched so no real inventory file is needed.
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
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    # v3.5: mutating endpoints require X-API-Key; seed a key for this client.
    with TestingSession() as s:
        raw_key, _ = create_api_key(s, name="test")
        s.commit()

    # Only core-sw-01 exists in the inventory; anything else should 404.
    monkeypatch.setattr(
        "netdrift.api.app._devices_cache",
        {"core-sw-01": {"hostname": "127.0.0.1", "username": "admin", "password": "test"}},
    )

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app, headers={"X-API-Key": raw_key})
    app.dependency_overrides.clear()


def test_pause_device(client):
    r = client.patch("/devices/core-sw-01/auto-apply",
                     json={"paused": True, "reason": "maintenance"})
    assert r.status_code == 200
    data = r.json()
    assert data["device_name"] == "core-sw-01"
    assert data["auto_remediation_paused"] is True
    assert data["paused_reason"] == "maintenance"
    assert data["paused_at"] is not None


def test_unpause_device_clears_state(client):
    client.patch("/devices/core-sw-01/auto-apply", json={"paused": True, "reason": "x"})
    r = client.patch("/devices/core-sw-01/auto-apply", json={"paused": False})
    assert r.status_code == 200
    data = r.json()
    assert data["auto_remediation_paused"] is False
    assert data["paused_at"] is None
    assert data["paused_reason"] is None


def test_pause_without_reason(client):
    r = client.patch("/devices/core-sw-01/auto-apply", json={"paused": True})
    assert r.status_code == 200
    assert r.json()["paused_reason"] is None


def test_pause_unknown_device_returns_404(client):
    r = client.patch("/devices/nonexistent/auto-apply", json={"paused": True})
    assert r.status_code == 404


def test_pause_state_persists_across_requests(client):
    client.patch("/devices/core-sw-01/auto-apply", json={"paused": True, "reason": "loop"})
    # A second toggle reads the existing row (upsert), proving persistence.
    r = client.patch("/devices/core-sw-01/auto-apply", json={"paused": True, "reason": "still"})
    assert r.json()["paused_reason"] == "still"


# ---------------------------------------------------------------------------
# GET /devices — list devices + their pause state (v3.5, backs the UI toggle)
# ---------------------------------------------------------------------------

def test_list_devices_returns_inventory_with_pause_state(client):
    r = client.get("/devices")
    assert r.status_code == 200
    devices = r.json()
    names = {d["name"] for d in devices}
    assert "core-sw-01" in names
    core = next(d for d in devices if d["name"] == "core-sw-01")
    assert core["auto_remediation_paused"] is False  # no row yet → safe default


def test_list_devices_reflects_pause(client):
    client.patch("/devices/core-sw-01/auto-apply", json={"paused": True, "reason": "loop"})
    core = next(d for d in client.get("/devices").json() if d["name"] == "core-sw-01")
    assert core["auto_remediation_paused"] is True
    assert core["paused_reason"] == "loop"


def test_list_devices_is_public_no_api_key(client):
    # GET is exempt from auth by design; sending no key still works.
    r = client.get("/devices", headers={"X-API-Key": ""})
    assert r.status_code == 200
