"""tests/test_alert_rules.py — SLA alert rule storage (v3.5).

An alert rule says "if a drift of this severity (on this device, or any device)
stays unresolved longer than window_minutes, alert." This file covers the
storage layer; SLA evaluation against the clock lands in a follow-up.

Repository-level tests on in-memory SQLite.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.storage.models import Base
from netdrift.storage.repository import (
    create_alert_rule,
    delete_alert_rule,
    get_alert_rule,
    list_alert_rules,
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


def test_create_returns_row_with_fields(session):
    rule = create_alert_rule(session, device="core-sw-01",
                             severity="critical", window_minutes=10)
    session.commit()
    assert rule.id is not None
    assert rule.device == "core-sw-01"
    assert rule.severity == "critical"
    assert rule.window_minutes == 10
    assert rule.enabled is True       # default
    assert rule.object_type is None   # default: any object type
    assert rule.created_at is not None


def test_create_with_object_type(session):
    rule = create_alert_rule(session, device="core-sw-01", severity="critical",
                             window_minutes=10, object_type="vlan")
    session.commit()
    assert rule.object_type == "vlan"


def test_device_is_nullable_meaning_all_devices(session):
    rule = create_alert_rule(session, device=None,
                             severity="warning", window_minutes=30)
    session.commit()
    assert rule.device is None


def test_create_respects_enabled_flag(session):
    rule = create_alert_rule(session, device=None, severity="info",
                             window_minutes=60, enabled=False)
    session.commit()
    assert rule.enabled is False


def test_list_returns_all_rules(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    create_alert_rule(session, device=None, severity="warning", window_minutes=30)
    session.commit()
    assert len(list_alert_rules(session)) == 2


def test_get_returns_rule_by_id(session):
    rule = create_alert_rule(session, device=None, severity="critical", window_minutes=5)
    session.commit()
    assert get_alert_rule(session, rule.id).id == rule.id


def test_get_unknown_id_returns_none(session):
    assert get_alert_rule(session, 999) is None


def test_delete_removes_rule(session):
    rule = create_alert_rule(session, device=None, severity="critical", window_minutes=5)
    session.commit()
    assert delete_alert_rule(session, rule.id) is True
    session.commit()
    assert get_alert_rule(session, rule.id) is None


def test_delete_unknown_id_returns_false(session):
    assert delete_alert_rule(session, 999) is False
