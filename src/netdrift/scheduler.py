"""scheduler.py — the polling loop (v0.2, extended in v3.0).

The "actively listens" piece. Instead of running `driftcheck` by hand, this
runs run_drift_check for every device on a fixed interval (default 5 minutes),
persisting drift each cycle.

Structure mirrors pipeline.py: the job-registration logic is a separate,
injectable function (schedule_drift_checks) so it can be unit-tested without
waiting for timers or touching the lab; main() wires up the real scheduler,
loads the real devices.yml, and blocks.

v3.0 additions:
- Structured logging (logging.getLogger("netdrift.scheduler")) instead of print,
  plus APScheduler EVENT_JOB_EXECUTED / EVENT_JOB_ERROR listeners.
- A WebhookDispatcher, initialised in main() alongside the SyslogReceiver, that
  fires on new critical-severity drift and on auto-apply success/failure.
- A post-apply re-poll: after a successful auto-apply, the affected device is
  re-checked once within REPOLL_DELAY_SECONDS to confirm the fix took.

Run it:
    python -m netdrift.scheduler
    python -m netdrift.scheduler --interval 1     # poll every minute

Requires DATABASE_URL (to persist) and, when the jobs actually fire,
NETBOX_URL / NETBOX_TOKEN and a reachable devices.yml + lab — the same inputs
driftcheck needs. Set WEBHOOK_URL to enable outbound notifications. Stop with
Ctrl+C.
"""

import argparse
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.blocking import BlockingScheduler

from netdrift.auto_apply import run_auto_apply
from netdrift.cli import load_devices
from netdrift.pipeline import run_drift_check
from netdrift.syslog_receiver import SyslogReceiver, DEFAULT_PORT as DEFAULT_SYSLOG_PORT
from netdrift.webhook import WebhookDispatcher

logger = logging.getLogger("netdrift.scheduler")

DEFAULT_INTERVAL_MINUTES = 5
REPOLL_DELAY_SECONDS = 60


def _fire_critical_drifts(dispatcher, device_name, drifts):
    """Fire one critical_drift webhook per newly persisted critical drift."""
    if dispatcher is None:
        return
    for d in drifts:
        if d.get("severity") == "critical":
            dispatcher.fire("critical_drift", {
                "device": device_name,
                "timestamp": d.get("detected_at"),
                "detail": (
                    f"{d.get('object')} {d.get('field')}: "
                    f"intent={d.get('intent')!r} reality={d.get('reality')!r}"
                ),
            })


def _make_auto_apply_fn(dispatcher, device_name, *, _run_auto_apply=run_auto_apply):
    """Build an auto_apply_fn (matching run_auto_apply's signature) that runs the
    loop and fires apply_success / apply_failure webhooks for each outcome.

    Passed to run_drift_check as `auto_apply_fn`; pipeline supplies
    is_device_paused_fn and schedule_repoll_fn, which we forward unchanged. When
    `dispatcher` is None this is just run_auto_apply with no firing.
    """
    def _fn(drifts, device, session_factory, *, is_device_paused_fn, schedule_repoll_fn):
        outcomes = _run_auto_apply(
            drifts, device, session_factory,
            is_device_paused_fn=is_device_paused_fn,
            schedule_repoll_fn=schedule_repoll_fn,
        )
        if dispatcher is not None:
            event_for = {"success": "apply_success", "failure": "apply_failure"}
            for o in outcomes:
                event_type = event_for.get(o.result)
                if event_type:
                    dispatcher.fire(event_type, {
                        "device": device_name,
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                        "detail": f"known_issue_id={o.known_issue_id} platform={o.platform}",
                    })
        return outcomes
    return _fn


def _check_one(device, *, dispatcher=None, schedule_repoll_fn=None):
    """Run the pipeline for one device, swallowing per-device errors so one
    unreachable device doesn't kill the whole polling loop.

    Fires critical-drift and auto-apply webhooks via `dispatcher` when supplied.
    main() binds `dispatcher` and `schedule_repoll_fn` before handing this to
    the scheduler and syslog receiver.
    """
    name = device["name"]
    try:
        drifts = run_drift_check(
            device,
            schedule_repoll_fn=schedule_repoll_fn,
            auto_apply_fn=_make_auto_apply_fn(dispatcher, name),
        )
        logger.info("%s: %d drift(s) recorded.", name, len(drifts))
        _fire_critical_drifts(dispatcher, name, drifts)
    except Exception as e:  # noqa: BLE001 — a poller must not die on one device
        logger.error("%s: drift check failed: %s", name, e)


def schedule_drift_checks(scheduler, devices, interval_minutes=DEFAULT_INTERVAL_MINUTES,
                          check=_check_one):
    """Register one recurring job per device on the given scheduler.

    Args:
        scheduler: an APScheduler scheduler to add jobs to.
        devices: dict {device_name: connection_details}, as load_devices returns.
        interval_minutes: how often each device is checked.
        check: callable(device_dict) run on each fire. Defaults to _check_one;
            tests inject a fake to avoid touching the lab.

    Returns the list of job ids registered (one per device).
    """
    job_ids = []
    for name, details in devices.items():
        device = {"name": name, **details}
        job = scheduler.add_job(
            check,
            trigger="interval",
            minutes=interval_minutes,
            args=[device],
            id=f"drift-check:{name}",
        )
        job_ids.append(job.id)
    return job_ids


def start_syslog_receiver(devices, check=_check_one, port=DEFAULT_SYSLOG_PORT,
                          _factory=SyslogReceiver):
    """Create and start a SyslogReceiver daemon thread.

    Args:
        devices: device dict as returned by load_devices.
        check: callable(device_dict) to run on each triggered poll.
        port: UDP port to listen on.
        _factory: SyslogReceiver class; tests inject a fake to avoid binding
            a real socket.

    Returns the receiver (mostly useful for tests).
    """
    receiver = _factory(devices, check=check, port=port)
    receiver.start()
    return receiver


def _log_job_executed(event):
    logger.info("Job %s executed.", event.job_id)


def _log_job_error(event):
    logger.error("Job %s raised an exception: %s", event.job_id, event.exception)


def register_listeners(scheduler):
    """Wire APScheduler job-execution / job-error events to the logger."""
    scheduler.add_listener(_log_job_executed, EVENT_JOB_EXECUTED)
    scheduler.add_listener(_log_job_error, EVENT_JOB_ERROR)


def main():
    parser = argparse.ArgumentParser(
        prog="netdrift-scheduler",
        description="Poll every device for drift on a fixed interval.",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_MINUTES,
        help=f"minutes between checks (default {DEFAULT_INTERVAL_MINUTES})",
    )
    parser.add_argument(
        "--syslog-port", type=int, default=DEFAULT_SYSLOG_PORT,
        help=f"UDP port to listen for syslog triggers (default {DEFAULT_SYSLOG_PORT})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    devices = load_devices()
    scheduler = BlockingScheduler()
    register_listeners(scheduler)

    # WebhookDispatcher reads WEBHOOK_URL / WEBHOOK_EVENTS from the environment.
    # With no WEBHOOK_URL it is disabled and fire() is a no-op.
    dispatcher = WebhookDispatcher()
    dispatcher.start()

    # check and repoll reference each other: a successful auto-apply schedules a
    # one-shot re-poll, which re-runs the same check. Both are only called once
    # the scheduler is started, by which point both names are bound.
    def repoll(device):
        run_at = datetime.now(tz=timezone.utc) + timedelta(seconds=REPOLL_DELAY_SECONDS)
        job_id = f"post-apply-repoll:{device['name']}:{run_at.timestamp()}"
        try:
            scheduler.add_job(
                check, trigger="date", run_date=run_at, args=[device],
                id=job_id, misfire_grace_time=30,
            )
            logger.info(
                "Post-apply re-poll scheduled for %s in %ds (job %s).",
                device["name"], REPOLL_DELAY_SECONDS, job_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to schedule re-poll for %s: %s", device["name"], exc)

    def check(device):
        _check_one(device, dispatcher=dispatcher, schedule_repoll_fn=repoll)

    ids = schedule_drift_checks(scheduler, devices, interval_minutes=args.interval,
                                check=check)
    start_syslog_receiver(devices, check=check, port=args.syslog_port)
    logger.info(
        "Scheduled %d device(s) every %d min: %s. Syslog trigger on UDP:%d. "
        "Webhooks %s. Ctrl+C to stop.",
        len(ids), args.interval, ", ".join(sorted(devices)), args.syslog_port,
        "enabled" if dispatcher.enabled else "disabled",
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
