"""scheduler.py — the polling loop (v0.2).

The "actively listens" piece. Instead of running `driftcheck` by hand, this
runs run_drift_check for every device on a fixed interval (default 5 minutes),
persisting drift each cycle.

Structure mirrors pipeline.py: the job-registration logic is a separate,
injectable function (schedule_drift_checks) so it can be unit-tested without
waiting for timers or touching the lab; main() wires up the real scheduler,
loads the real devices.yml, and blocks.

Run it:
    python -m netdrift.scheduler
    python -m netdrift.scheduler --interval 1     # poll every minute

Requires DATABASE_URL (to persist) and, when the jobs actually fire,
NETBOX_URL / NETBOX_TOKEN and a reachable devices.yml + lab — the same inputs
driftcheck needs. Stop with Ctrl+C.
"""

import argparse

from apscheduler.schedulers.blocking import BlockingScheduler

from netdrift.cli import load_devices
from netdrift.pipeline import run_drift_check
from netdrift.syslog_receiver import SyslogReceiver, DEFAULT_PORT as DEFAULT_SYSLOG_PORT

DEFAULT_INTERVAL_MINUTES = 5


def _check_one(device):
    """Run the pipeline for one device, swallowing per-device errors so one
    unreachable device doesn't kill the whole polling loop."""
    name = device["name"]
    try:
        drifts = run_drift_check(device)
        print(f"{name}: {len(drifts)} drift(s) recorded.")
    except Exception as e:  # noqa: BLE001 — a poller must not die on one device
        print(f"{name}: drift check failed: {e}")


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

    devices = load_devices()
    scheduler = BlockingScheduler()
    ids = schedule_drift_checks(scheduler, devices, interval_minutes=args.interval)
    start_syslog_receiver(devices, port=args.syslog_port)
    print(
        f"Scheduled {len(ids)} device(s) every {args.interval} min: "
        f"{', '.join(sorted(devices))}. "
        f"Syslog trigger on UDP:{args.syslog_port}. Ctrl+C to stop."
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")


if __name__ == "__main__":
    main()