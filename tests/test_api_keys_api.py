"""tests/test_api_keys_api.py — /api-keys endpoints (v3.5 Feature 5).

POST mints a key and returns the raw value exactly once; GET lists keys without
ever exposing the raw value or its hash; DELETE revokes a key so it 401s on the
next request. These endpoints are themselves auth-gated by the X-API-Key
middleware, so a bootstrap key (normally created by the CLI) seeds the client.

TestClient over in-memory SQLite.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.api.app import app, get_session
from netdrift.auth import KEY_PREFIX
from netdrift.storage.models import Base
from netdrift.storage.repository import create_api_key


@pytest.fixture
def client():
    """Auth'd TestClient: a bootstrap key is seeded and sent by default."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as s:
        raw_key, _ = create_api_key(s, name="bootstrap")
        s.commit()

    def override_get_session():
        with TestingSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app, headers={"X-API-Key": raw_key})
    app.dependency_overrides.clear()


def test_post_mints_key_and_returns_raw_once(client):
    resp = client.post("/api-keys", json={"name": "ci-bot"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "ci-bot"
    assert body["key"].startswith(KEY_PREFIX)
    assert "id" in body and "key_hint" in body and "created_at" in body


def test_minted_key_authenticates_subsequent_request(client):
    raw = client.post("/api-keys", json={"name": "ci-bot"}).json()["key"]
    # Use the brand-new key (not the bootstrap key) to make a mutating call.
    resp = client.post(
        "/api-keys", json={"name": "second"}, headers={"X-API-Key": raw}
    )
    assert resp.status_code == 200


def test_post_accepts_expires_at(client):
    resp = client.post(
        "/api-keys", json={"name": "temp", "expires_at": "2026-12-31T00:00:00Z"}
    )
    assert resp.status_code == 200
    assert resp.json()["expires_at"] is not None


def test_get_lists_keys_without_raw_or_hash(client):
    client.post("/api-keys", json={"name": "ci-bot"})
    resp = client.get("/api-keys")
    assert resp.status_code == 200
    rows = resp.json()
    names = {r["name"] for r in rows}
    assert {"bootstrap", "ci-bot"} <= names
    for r in rows:
        assert "key" not in r          # raw value never listed
        assert "key_hash" not in r     # hash never exposed
        assert "key_hint" in r


def test_delete_revokes_key(client):
    new_id = client.post("/api-keys", json={"name": "to-revoke"}).json()["id"]
    # Mint a separate key we can prove stops working after deletion.
    minted = client.post("/api-keys", json={"name": "doomed"}).json()
    raw = minted["key"]
    doomed_id = minted["id"]

    assert client.post(
        "/api-keys", json={"name": "x"}, headers={"X-API-Key": raw}
    ).status_code == 200

    resp = client.delete(f"/api-keys/{doomed_id}")
    assert resp.status_code == 200

    # Revoked key no longer authenticates.
    assert client.post(
        "/api-keys", json={"name": "y"}, headers={"X-API-Key": raw}
    ).status_code == 401

    # And the deleted key is gone from the listing.
    ids = {r["id"] for r in client.get("/api-keys").json()}
    assert doomed_id not in ids
    assert new_id in ids


def test_delete_unknown_id_returns_404(client):
    assert client.delete("/api-keys/9999").status_code == 404
