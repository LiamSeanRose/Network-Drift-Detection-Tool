"""tests/test_scheduler.py — scheduler registration tests (v0.2).

schedule_drift_checks registers one recurring job per device. These tests
verify the registration (count, interval, the device passed) WITHOUT starting
the scheduler — so no timers fire and the lab is never touched. A fake `check`
callable is injected in place of the real pipeline.
"""

from apscheduler.schedulers.background import BackgroundScheduler

from netdrift.scheduler import schedule_drift_checks

DEVICES = {
    "core-sw-01": {"hostname": "172.20.20.11", "username": "admin", "password": "x"},
    "core-sw-02": {"hostname": "172.20.20.12", "username": "admin", "password": "x"},
}


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