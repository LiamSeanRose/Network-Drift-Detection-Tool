"""tests/test_api_auth.py — X-API-Key middleware (v3.5 Feature 5).

Mutating endpoints (POST/PUT/PATCH/DELETE) require a valid X-API-Key; read-only
endpoints (GET /drifts, /health) are public-by-default by deliberate design.
Auth runs as middleware ahead of routing, so an unauthenticated write is
rejected with 401 before any route logic (including 404/422) executes.

TestClient over in-memory SQLite. The middleware resolves its session through
the same get_session dependency override the routes use, so it authenticates
against this test database.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.api.app import app, get_session
from netdrift.storage.models import Base
from netdrift.storage.repository import create_api_key, delete_api_key, save_drifts


@pytest.fixture
def auth_client(monkeypatch):
    """Yield (client, raw_key). The client sends no key by default; tests pass
    the X-API-Key header explicitly so absence and presence are both testable."""
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
             "detected_at": "2026-05-31T10:00:00Z", "platform": "arista_eos"},
        ])
        raw_key, _ = create_api_key(s, name="test")
        s.commit()

    monkeypatch.setattr(
        "netdrift.api.app._devices_cache",
        {"core-sw-01": {"hostname": "127.0.0.1", "username": "admin", "password": "test"}},
    )

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app), raw_key
    app.dependency_overrides.clear()


def _new_issue_payload():
    return {
        "object": "interface:Ethernet1", "field": "enabled",
        "drift_kind": "value_mismatch", "cause": "shut", "fix": "no shutdown",
    }


# Every mutating endpoint that exists today. Auth is enforced before route
# logic, so nonexistent IDs still return 401 (not 404) when the key is absent.
MUTATING_ENDPOINTS = [
    ("post", "/known-issues"),
    ("patch", "/known-issues/1"),
    ("patch", "/known-issues/1/auto-apply"),
    ("patch", "/devices/core-sw-01/auto-apply"),
    ("post", "/known-issues/1/remediate/dry-run"),
    ("post", "/known-issues/1/remediate/apply"),
]


@pytest.mark.parametrize("method,path", MUTATING_ENDPOINTS)
def test_mutating_endpoint_without_key_returns_401(auth_client, method, path):
    client, _ = auth_client
    resp = getattr(client, method)(path, json={})
    assert resp.status_code == 401


def test_mutating_with_invalid_key_returns_401(auth_client):
    client, _ = auth_client
    resp = client.post(
        "/known-issues", json=_new_issue_payload(),
        headers={"X-API-Key": "sk-netdrift-not-a-real-key"},
    )
    assert resp.status_code == 401


def test_mutating_with_valid_key_is_authorized(auth_client):
    client, raw_key = auth_client
    resp = client.post(
        "/known-issues", json=_new_issue_payload(),
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 200


def test_get_drifts_is_exempt_from_auth(auth_client):
    client, _ = auth_client
    assert client.get("/drifts").status_code == 200


def test_health_is_exempt_from_auth(auth_client):
    client, _ = auth_client
    assert client.get("/health").status_code == 200


def test_disable_auth_flag_allows_write_without_key(auth_client, monkeypatch):
    # Trusted/local deployments can opt out of the key requirement entirely.
    client, _ = auth_client
    monkeypatch.setenv("NETDRIFT_DISABLE_API_AUTH", "true")
    resp = client.post("/known-issues", json=_new_issue_payload())  # no X-API-Key
    assert resp.status_code == 200


def test_disable_auth_flag_default_off_still_requires_key(auth_client, monkeypatch):
    client, _ = auth_client
    monkeypatch.delenv("NETDRIFT_DISABLE_API_AUTH", raising=False)
    resp = client.post("/known-issues", json=_new_issue_payload())
    assert resp.status_code == 401


def test_revoked_key_returns_401_on_next_request(auth_client):
    client, _ = auth_client
    # Mint a second key, use it once successfully, then revoke and retry.
    sessionmaker_fn = app.dependency_overrides[get_session]
    gen = sessionmaker_fn()
    session = next(gen)
    raw_key, row = create_api_key(session, name="temp")
    session.commit()
    key_id = row.id
    gen.close()

    ok = client.post("/known-issues", json=_new_issue_payload(),
                     headers={"X-API-Key": raw_key})
    assert ok.status_code == 200

    gen = sessionmaker_fn()
    session = next(gen)
    delete_api_key(session, key_id)
    session.commit()
    gen.close()

    after = client.post("/known-issues", json=_new_issue_payload(),
                        headers={"X-API-Key": raw_key})
    assert after.status_code == 401
