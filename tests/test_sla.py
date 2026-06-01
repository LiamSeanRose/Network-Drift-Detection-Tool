"""tests/test_sla.py — SLA breach evaluation (v3.5).

evaluate_sla() walks the enabled alert rules and fires one webhook per breaching
(device, fingerprint): a drift of the rule's severity, on the rule's device (or
any device when the rule's device is null), whose detected_at is older than the
rule's window and which is not acknowledged.

Per the project's non-negotiable testing rule, evaluate_sla takes an injectable
``now`` — every timestamp here is manufactured; there is no time.sleep anywhere.
A fake dispatcher records fire() calls so the test is independent of the real
WebhookDispatcher's event filtering and HTTP.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.sla import evaluate_sla
from netdrift.storage.models import Base
from netdrift.storage.repository import (
    create_acknowledgement,
    create_alert_rule,
    record_collection,
    save_drifts,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

CRITICAL_DRIFT = {
    "device": "core-sw-01", "object": "interface:Ethernet1", "field": "enabled",
    "intent": True, "reality": False, "drift_kind": "value_mismatch",
    "severity": "critical",
}
FP = make_fingerprint({"object": "interface:Ethernet1", "field": "enabled",
                       "drift_kind": "value_mismatch"})


class FakeDispatcher:
    """Records fire() calls instead of sending HTTP."""

    def __init__(self):
        self.fired = []

    def fire(self, event_type, payload):
        self.fired.append((event_type, payload))


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


def _seed_drift(session, *, minutes_ago, **overrides):
    record = {**CRITICAL_DRIFT,
              "detected_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}
    record.update(overrides)
    save_drifts(session, [record])
    session.commit()


def test_breach_fires_one_webhook(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=11)
    session.commit()

    fake = FakeDispatcher()
    breaches = evaluate_sla(session, fake, now=NOW)

    assert len(fake.fired) == 1
    event_type, payload = fake.fired[0]
    assert event_type == "sla_breached"
    assert payload["device"] == "core-sw-01"
    assert len(breaches) == 1


def test_drift_within_window_does_not_breach(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=5)  # younger than the 10-minute window
    session.commit()

    fake = FakeDispatcher()
    assert evaluate_sla(session, fake, now=NOW) == []
    assert fake.fired == []


def test_acknowledged_drift_is_suppressed(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=20)
    create_acknowledgement(session, "core-sw-01", FP, acknowledged_until=None)
    session.commit()

    fake = FakeDispatcher()
    assert evaluate_sla(session, fake, now=NOW) == []
    assert fake.fired == []


def test_expired_acknowledgement_does_not_suppress(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=20)
    # Ack expired an hour before NOW — must not suppress.
    create_acknowledgement(session, "core-sw-01", FP,
                           acknowledged_until=NOW - timedelta(hours=1))
    session.commit()

    fake = FakeDispatcher()
    assert len(evaluate_sla(session, fake, now=NOW)) == 1


def test_null_device_rule_matches_any_device(session):
    create_alert_rule(session, device=None, severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=11, device="edge-rtr-09")
    session.commit()

    fake = FakeDispatcher()
    breaches = evaluate_sla(session, fake, now=NOW)
    assert len(breaches) == 1
    assert fake.fired[0][1]["device"] == "edge-rtr-09"


def test_disabled_rule_does_not_fire(session):
    create_alert_rule(session, device=None, severity="critical",
                      window_minutes=10, enabled=False)
    _seed_drift(session, minutes_ago=20)
    session.commit()

    fake = FakeDispatcher()
    assert evaluate_sla(session, fake, now=NOW) == []


def test_severity_mismatch_does_not_fire(session):
    create_alert_rule(session, device=None, severity="warning", window_minutes=10)
    _seed_drift(session, minutes_ago=20)  # this drift is critical, rule wants warning
    session.commit()

    fake = FakeDispatcher()
    assert evaluate_sla(session, fake, now=NOW) == []


def test_same_drift_pattern_alerts_once_per_run(session):
    # The same logical drift is re-persisted every poll: two rows, same
    # (device, fingerprint). A single run must alert once, not per row.
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=20)
    _seed_drift(session, minutes_ago=15)
    session.commit()

    fake = FakeDispatcher()
    breaches = evaluate_sla(session, fake, now=NOW)
    assert len(fake.fired) == 1
    assert len(breaches) == 1


# ---------------------------------------------------------------------------
# device_unreachable substitution
# ---------------------------------------------------------------------------

def test_unreachable_device_fires_device_unreachable_not_breach(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=20)
    session.commit()  # no collection ever recorded -> unreachable

    fake = FakeDispatcher()
    evaluate_sla(session, fake, now=NOW, unreachable_after_minutes=10)

    assert [e for e, _ in fake.fired] == ["device_unreachable"]


def test_reachable_device_fires_sla_breach(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=20)
    record_collection(session, "core-sw-01", NOW - timedelta(minutes=1))  # fresh
    session.commit()

    fake = FakeDispatcher()
    evaluate_sla(session, fake, now=NOW, unreachable_after_minutes=10)

    assert [e for e, _ in fake.fired] == ["sla_breached"]


def test_no_unreachable_threshold_always_breaches(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=20)
    session.commit()  # uncollected, but no threshold passed -> reachability not checked

    fake = FakeDispatcher()
    evaluate_sla(session, fake, now=NOW)  # unreachable_after_minutes defaults None

    assert [e for e, _ in fake.fired] == ["sla_breached"]


def test_unreachable_fires_once_per_device(session):
    create_alert_rule(session, device="core-sw-01", severity="critical", window_minutes=10)
    _seed_drift(session, minutes_ago=20, field="enabled")
    _seed_drift(session, minutes_ago=20, field="description")  # different fingerprint
    session.commit()

    fake = FakeDispatcher()
    evaluate_sla(session, fake, now=NOW, unreachable_after_minutes=10)

    assert [e for e, _ in fake.fired] == ["device_unreachable"]
