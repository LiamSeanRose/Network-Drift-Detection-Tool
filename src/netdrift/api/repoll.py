"""api/repoll.py — post-apply re-poll scheduler for the API process.

The API and the main scheduler run as separate processes in production.
This module maintains a lightweight BackgroundScheduler inside the API
process so that, after a successful apply, a one-shot re-poll of the
affected device fires within REPOLL_DELAY_SECONDS (default 60s) without
blocking the HTTP response.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from netdrift.pipeline import run_drift_check

logger = logging.getLogger(__name__)

REPOLL_DELAY_SECONDS = 60

_scheduler: BackgroundScheduler | None = None


def _get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
    return _scheduler


def schedule_repoll(device: dict, delay_seconds: int = REPOLL_DELAY_SECONDS) -> None:
    """Schedule a one-shot drift re-poll for the given device.

    Runs run_drift_check in the API process's background scheduler so the
    HTTP response is not blocked. Called after every successful apply to
    verify the fix took effect.

    Args:
        device: device dict (name + connection details) from devices.yml.
        delay_seconds: how long to wait before re-polling (default 60s).
    """
    run_at = datetime.now(tz=timezone.utc) + timedelta(seconds=delay_seconds)
    job_id = f"post-apply-repoll:{device['name']}:{run_at.timestamp()}"
    try:
        _get_scheduler().add_job(
            run_drift_check,
            trigger="date",
            run_date=run_at,
            args=[device],
            id=job_id,
            misfire_grace_time=30,
        )
        logger.info(
            "Post-apply re-poll scheduled for '%s' in %ds (job %s)",
            device["name"],
            delay_seconds,
            job_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to schedule post-apply re-poll for '%s': %s", device["name"], exc
        )
