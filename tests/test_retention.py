"""tests/test_retention.py — drift event retention (v3.5).

drift_events grows ~21M rows/year at 10 devices x 5-minute polls, so old events
must be pruned or history queries slow down. delete_drifts_older_than removes
rows past a cutoff; the scheduler runs it periodically. Cutoff is derived from
an injected ``now`` — no time.sleep.
"""

from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift import scheduler as scheduler_mod
from netdrift.storage.models import Base
from netdrift.storage.repository import delete_drifts_older_than, get_drifts, save_drifts

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _event(field, *, days_ago):
    return {
        "device": "core-sw-01", "object": "interface:Ethernet1", "field": field,
        "intent": True, "reality": False, "drift_kind": "value_mismatch",
        "severity": "critical",
        "detected_at": (NOW - timedelta(days=days_ago)).isoformat(),
    }


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
# repository
# ---------------------------------------------------------------------------

def test_delete_removes_old_keeps_recent(session):
    save_drifts(session, [_event("old", days_ago=100), _event("recent", days_ago=1)])
    session.commit()

    deleted = delete_drifts_older_than(session, NOW - timedelta(days=90))
    session.commit()

    assert deleted == 1
    remaining = get_drifts(session)
    assert len(remaining) == 1
    assert remaining[0].field == "recent"


def test_delete_returns_zero_when_nothing_old(session):
    save_drifts(session, [_event("recent", days_ago=1)])
    session.commit()
    assert delete_drifts_older_than(session, NOW - timedelta(days=90)) == 0


# ---------------------------------------------------------------------------
# scheduler job
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.committed = False
        self.closed = False

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_run_retention_deletes_with_cutoff_then_commits_and_closes():
    captured = {}
    sess = _FakeSession()

    def fake_delete(session, cutoff):
        captured["session"] = session
        captured["cutoff"] = cutoff
        return 3

    scheduler_mod._run_retention(lambda: sess, 90, now=NOW, delete=fake_delete)

    assert captured["session"] is sess
    assert captured["cutoff"] == NOW - timedelta(days=90)
    assert sess.committed is True
    assert sess.closed is True


def test_run_retention_swallows_errors_and_closes(caplog):
    sess = _FakeSession()

    def boom(session, cutoff):
        raise RuntimeError("retention boom")

    with caplog.at_level("ERROR"):
        scheduler_mod._run_retention(lambda: sess, 90, now=NOW, delete=boom)

    assert sess.closed is True
    assert any("retention boom" in r.message for r in caplog.records)


def test_schedule_retention_registers_single_job():
    sched = BackgroundScheduler()
    job_id = scheduler_mod.schedule_retention(sched, job=lambda: None)
    assert job_id == "drift-retention"
    assert sched.get_job("drift-retention") is not None
