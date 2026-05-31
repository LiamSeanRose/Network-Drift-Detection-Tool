"""tests/test_known_issues.py — repository tests for the known_issues table.

Uses the same in-memory SQLite pattern as test_storage.py — no Postgres needed.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netdrift.storage.models import Base
from netdrift.storage.repository import (
    confirmed_count,
    get_known_issue,
    list_known_issues,
    save_known_issue,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def test_save_and_retrieve(session):
    save_known_issue(session, "interface|enabled|value_mismatch", "Manual shutdown", "Re-enable the interface")
    session.commit()

    result = get_known_issue(session, "interface|enabled|value_mismatch")
    assert result is not None
    assert result.cause == "Manual shutdown"
    assert result.fix == "Re-enable the interface"
    # confirmed_count is derived from remediation_events, not stored; starts at 0.
    assert confirmed_count(session, result.id) == 0


def test_get_returns_none_for_unknown(session):
    assert get_known_issue(session, "nonexistent|fingerprint|here") is None


def test_list_empty(session):
    assert list_known_issues(session) == []


def test_list_returns_all(session):
    save_known_issue(session, "interface|enabled|value_mismatch", "Cause A", "Fix A")
    save_known_issue(session, "bgp_neighbor|session_state|value_mismatch", "Cause B", "Fix B")
    session.commit()

    results = list_known_issues(session)
    assert len(results) == 2
    fingerprints = {r.fingerprint for r in results}
    assert "interface|enabled|value_mismatch" in fingerprints
    assert "bgp_neighbor|session_state|value_mismatch" in fingerprints


def test_save_sets_created_at(session):
    save_known_issue(session, "vlan|name|value_mismatch", "Doc lag", "Update NetBox")
    session.commit()

    result = get_known_issue(session, "vlan|name|value_mismatch")
    assert result.created_at is not None


def test_duplicate_fingerprint_raises(session):
    save_known_issue(session, "interface|enabled|value_mismatch", "Cause A", "Fix A")
    session.commit()

    with pytest.raises(Exception):
        save_known_issue(session, "interface|enabled|value_mismatch", "Cause B", "Fix B")
        session.commit()
