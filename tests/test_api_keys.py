"""tests/test_api_keys.py — API key generation, hashing, and storage (v3.5).

API keys authenticate mutating endpoints (v3.5 Feature 5). A key is shown to
the operator exactly once at creation; only its SHA-256 hash is persisted, so a
database leak never exposes usable keys. Verification hashes the presented key
and looks up the row; revocation is a hard delete.

Pure-logic helpers (netdrift/auth.py) and repository CRUD on in-memory SQLite —
no API, no lab. Expiry is tested with an injected ``now`` (no time.sleep).
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.auth import KEY_PREFIX, generate_api_key, hash_api_key
from netdrift.storage.models import ApiKey, Base
from netdrift.storage.repository import (
    create_api_key,
    delete_api_key,
    get_api_key_by_id,
    list_api_keys,
    verify_api_key,
)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


# ---------------------------------------------------------------------------
# auth.py — pure helpers
# ---------------------------------------------------------------------------

def test_generated_key_has_prefix():
    key = generate_api_key()
    assert key.startswith(KEY_PREFIX)


def test_generated_keys_are_unique():
    assert generate_api_key() != generate_api_key()


def test_hash_is_deterministic_sha256_hex():
    raw = generate_api_key()
    # Same input -> same hash; 64 hex chars for SHA-256.
    assert hash_api_key(raw) == hash_api_key(raw)
    digest = hash_api_key(raw)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_differs_from_raw():
    raw = generate_api_key()
    assert hash_api_key(raw) != raw


# ---------------------------------------------------------------------------
# repository — create
# ---------------------------------------------------------------------------

def test_create_returns_raw_key_once_and_stores_only_hash(session):
    raw, row = create_api_key(session, name="admin")
    session.commit()
    # Raw key is returned to the caller exactly once...
    assert raw.startswith(KEY_PREFIX)
    # ...but only the hash is persisted — never the plaintext.
    assert row.key_hash == hash_api_key(raw)
    assert raw not in (row.key_hash, row.key_hint)
    # No column anywhere holds the raw key.
    assert not any(getattr(row, c.name) == raw for c in ApiKey.__table__.columns)


def test_create_sets_hint_to_token_fragment_not_full_key(session):
    raw, row = create_api_key(session, name="admin")
    token = raw[len(KEY_PREFIX):]
    assert row.key_hint == token[:8]
    assert row.key_hint != raw


def test_create_stores_name_and_created_at(session):
    _, row = create_api_key(session, name="ci-bot")
    assert row.name == "ci-bot"
    assert row.created_at is not None


# ---------------------------------------------------------------------------
# repository — verify
# ---------------------------------------------------------------------------

def test_verify_returns_row_for_valid_key(session):
    raw, row = create_api_key(session, name="admin")
    session.commit()
    found = verify_api_key(session, raw)
    assert found is not None
    assert found.id == row.id


def test_verify_returns_none_for_unknown_key(session):
    create_api_key(session, name="admin")
    session.commit()
    assert verify_api_key(session, "sk-netdrift-not-a-real-key") is None


def test_verify_updates_last_used_at(session):
    raw, row = create_api_key(session, name="admin")
    session.commit()
    assert row.last_used_at is None
    stamp = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    verify_api_key(session, raw, now=stamp)
    assert get_api_key_by_id(session, row.id).last_used_at == stamp


def test_verify_rejects_expired_key(session):
    past = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw, _ = create_api_key(session, name="temp", expires_at=past)
    session.commit()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert verify_api_key(session, raw, now=now) is None


def test_verify_accepts_unexpired_key(session):
    future = datetime(2026, 12, 31, tzinfo=timezone.utc)
    raw, _ = create_api_key(session, name="temp", expires_at=future)
    session.commit()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert verify_api_key(session, raw, now=now) is not None


def test_verify_treats_null_expiry_as_never_expires(session):
    raw, _ = create_api_key(session, name="forever", expires_at=None)
    session.commit()
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert verify_api_key(session, raw, now=far_future) is not None


# ---------------------------------------------------------------------------
# repository — list / delete (revoke)
# ---------------------------------------------------------------------------

def test_list_returns_keys_without_exposing_raw(session):
    raw_a, _ = create_api_key(session, name="a")
    raw_b, _ = create_api_key(session, name="b")
    session.commit()
    rows = list_api_keys(session)
    assert {r.name for r in rows} == {"a", "b"}
    for r in rows:
        for col in ApiKey.__table__.columns:
            assert getattr(r, col.name) not in (raw_a, raw_b)


def test_delete_revokes_key_so_verify_fails(session):
    raw, row = create_api_key(session, name="admin")
    session.commit()
    assert verify_api_key(session, raw) is not None
    deleted = delete_api_key(session, row.id)
    session.commit()
    assert deleted is True
    assert verify_api_key(session, raw) is None


def test_delete_unknown_id_returns_false(session):
    assert delete_api_key(session, 999) is False
