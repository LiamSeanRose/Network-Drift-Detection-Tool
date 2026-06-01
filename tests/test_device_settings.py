"""tests/test_device_settings.py — per-device auto-apply kill-switch storage.

The device_settings table backs the per-device runtime kill-switch (v3.0
Feature 3). run_auto_apply() consults is_device_paused() before dispatching any
apply for a device, so an operator can stop auto-remediation on a misbehaving
device without restarting the scheduler.

Repository-level tests on in-memory SQLite — no API, no lab.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.storage.models import Base, DeviceSetting
from netdrift.storage.repository import (
    get_device_setting,
    is_device_paused,
    set_device_paused,
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


def test_is_device_paused_false_when_no_row(session):
    # Absence of a row means "not paused" — the safe default.
    assert is_device_paused(session, "core-sw-01") is False


def test_set_device_paused_creates_row_and_pauses(session):
    set_device_paused(session, "core-sw-01", True, "maintenance window")
    session.commit()
    assert is_device_paused(session, "core-sw-01") is True


def test_set_device_paused_records_reason_and_timestamp(session):
    setting = set_device_paused(session, "core-sw-01", True, "bad fix loop")
    assert setting.paused_reason == "bad fix loop"
    assert setting.paused_at is not None


def test_paused_without_reason_is_allowed(session):
    set_device_paused(session, "core-sw-02", True)
    session.commit()
    assert is_device_paused(session, "core-sw-02") is True
    assert get_device_setting(session, "core-sw-02").paused_reason is None


def test_unpause_clears_timestamp_and_reason(session):
    set_device_paused(session, "core-sw-01", True, "reason")
    session.commit()
    set_device_paused(session, "core-sw-01", False)
    session.commit()
    assert is_device_paused(session, "core-sw-01") is False
    setting = get_device_setting(session, "core-sw-01")
    assert setting.paused_at is None
    assert setting.paused_reason is None


def test_set_device_paused_upserts_single_row(session):
    set_device_paused(session, "core-sw-01", True, "first")
    session.commit()
    set_device_paused(session, "core-sw-01", True, "second")
    session.commit()
    assert session.query(DeviceSetting).count() == 1
    assert get_device_setting(session, "core-sw-01").paused_reason == "second"
