"""tests/test_scheduler.py — scheduler registration tests (v0.2).

schedule_drift_checks registers one recurring job per device. These tests
verify the registration (count, interval, the device passed) WITHOUT starting
the scheduler — so no timers fire and the lab is never touched. A fake `check`
callable is injected in place of the real pipeline.
"""

from collections import namedtuple

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler

from netdrift import scheduler as scheduler_mod
from netdrift.scheduler import schedule_drift_checks, start_syslog_receiver

DEVICES = {
    "core-sw-01": {"hostname": "172.20.20.11", "username": "admin", "password": "x"},
    "core-sw-02": {"hostname": "172.20.20.12", "username": "admin", "password": "x"},
}


class FakeDispatcher:
    """Records fire() calls; mimics WebhookDispatcher's surface."""

    def __init__(self):
        self.fired = []
        self.started = False

    def fire(self, event_type, payload):
        self.fired.append((event_type, payload))

    def start(self):
        self.started = True


_Outcome = namedtuple("_Outcome", "known_issue_id result platform")


def _fresh_scheduler():
    """A scheduler we never start — add_job works without firing anything."""
    return BackgroundScheduler()


def test_registers_one_job_per_device():
    sched = _fresh_scheduler()
    ids = schedule_drift_checks(sched, DEVICES, check=lambda dev: None)
    assert len(ids) == 2
    assert len(sched.get_jobs()) == 2


def test_job_ids_name_each_device():
    sched = _fresh_scheduler()
    ids = schedule_drift_checks(sched, DEVICES, check=lambda dev: None)
    assert "drift-check:core-sw-01" in ids
    assert "drift-check:core-sw-02" in ids


def test_each_job_carries_its_device_dict():
    sched = _fresh_scheduler()
    schedule_drift_checks(sched, DEVICES, check=lambda dev: None)
    job = sched.get_job("drift-check:core-sw-01")
    # The job's first positional arg is the device dict, with name folded in.
    device_arg = job.args[0]
    assert device_arg["name"] == "core-sw-01"
    assert device_arg["hostname"] == "172.20.20.11"


def test_empty_inventory_registers_no_jobs():
    sched = _fresh_scheduler()
    ids = schedule_drift_checks(sched, {}, check=lambda dev: None)
    assert ids == []
    assert sched.get_jobs() == []


# ---------------------------------------------------------------------------
# start_syslog_receiver tests
# ---------------------------------------------------------------------------

def test_start_syslog_receiver_passes_devices_and_port():
    """start_syslog_receiver builds the receiver with the right devices and port."""
    captured = {}

    class FakeReceiver:
        def __init__(self, devices, *, check, port, **_):
            captured["devices"] = devices
            captured["port"] = port

        def start(self):
            pass

    start_syslog_receiver(DEVICES, check=lambda d: None, port=9514,
                          _factory=FakeReceiver)
    assert "core-sw-01" in captured["devices"]
    assert captured["port"] == 9514


def test_start_syslog_receiver_returns_receiver():
    """start_syslog_receiver returns the receiver object."""

    class FakeReceiver:
        def __init__(self, *_, **__):
            pass

        def start(self):
            pass

    result = start_syslog_receiver(DEVICES, check=lambda d: None,
                                   _factory=FakeReceiver)
    assert isinstance(result, FakeReceiver)


# ---------------------------------------------------------------------------
# v3.0 — webhook firing + structured logging + listeners
# ---------------------------------------------------------------------------

def test_check_one_fires_critical_drift_webhook(monkeypatch):
    disp = FakeDispatcher()
    crit = {"object": "interface:Ethernet1", "field": "enabled", "intent": True,
            "reality": False, "severity": "critical", "detected_at": "t",
            "device": "core-sw-01"}
    warn = {"object": "vlan:10", "field": "name", "intent": "a", "reality": "b",
            "severity": "warning", "detected_at": "t", "device": "core-sw-01"}
    monkeypatch.setattr(scheduler_mod, "run_drift_check", lambda device, **kw: [crit, warn])

    scheduler_mod._check_one({"name": "core-sw-01"}, dispatcher=disp)

    events = [e for e, _ in disp.fired]
    assert events.count("critical_drift") == 1  # only the critical one
    payload = next(p for e, p in disp.fired if e == "critical_drift")
    assert payload["device"] == "core-sw-01"
    assert "timestamp" in payload and "detail" in payload


def test_check_one_without_dispatcher_is_safe(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "run_drift_check", lambda device, **kw: [])
    # No dispatcher bound — must not raise.
    scheduler_mod._check_one({"name": "core-sw-01"})


def test_check_one_logs_error_and_does_not_raise(monkeypatch, caplog):
    def boom(device, **kw):
        raise RuntimeError("device unreachable")
    monkeypatch.setattr(scheduler_mod, "run_drift_check", boom)

    with caplog.at_level("ERROR"):
        scheduler_mod._check_one({"name": "core-sw-01"}, dispatcher=FakeDispatcher())

    assert any("unreachable" in r.message for r in caplog.records)


def test_auto_apply_wrapper_fires_success_and_failure():
    disp = FakeDispatcher()
    outcomes = [
        _Outcome(1, "success", "arista_eos"),
        _Outcome(2, "failure", "arista_eos"),
        _Outcome(3, "blocked", "arista_eos"),
    ]
    fn = scheduler_mod._make_auto_apply_fn(
        disp, "core-sw-01", _run_auto_apply=lambda *a, **k: outcomes,
    )
    result = fn([], {"name": "core-sw-01"}, lambda: None,
                is_device_paused_fn=lambda n, s: False, schedule_repoll_fn=None)

    assert result is outcomes
    events = [e for e, _ in disp.fired]
    assert "apply_success" in events
    assert "apply_failure" in events
    assert "apply_blocked" not in events  # only success/failure dispatch


def test_auto_apply_wrapper_forwards_injectables():
    captured = {}

    def fake_run(drifts, device, session_factory, *, is_device_paused_fn, schedule_repoll_fn):
        captured.update(is_device_paused_fn=is_device_paused_fn,
                        schedule_repoll_fn=schedule_repoll_fn)
        return []

    fn = scheduler_mod._make_auto_apply_fn(None, "x", _run_auto_apply=fake_run)
    paused, repoll = object(), object()
    fn([1], {"name": "x"}, "sf", is_device_paused_fn=paused, schedule_repoll_fn=repoll)

    assert captured["is_device_paused_fn"] is paused
    assert captured["schedule_repoll_fn"] is repoll


def test_register_listeners_adds_executed_and_error():
    masks = []

    class FakeScheduler:
        def add_listener(self, fn, mask):
            masks.append(mask)

    scheduler_mod.register_listeners(FakeScheduler())
    assert EVENT_JOB_EXECUTED in masks
    assert EVENT_JOB_ERROR in masks