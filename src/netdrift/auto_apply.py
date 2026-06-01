"""auto_apply.py — scheduler auto-apply loop.

Given a list of drift records already persisted for one device, cross-references
against known_issues where auto_apply_enabled=True and the global kill-switch is
on, then applies each matching fix.

One RemediationEvent is written per match regardless of outcome (success,
failure, or blocked). Three consecutive scheduler failures for the same
KnownIssue disable its auto_apply_enabled flag automatically.

This module is a pure orchestration function — it has no I/O of its own beyond
the database session it receives. Device connections are made inside the injected
applier callable. This makes the function fully testable with fakes.

Wired into pipeline.run_drift_check() by the caller (pipeline.py step, Matthew).
Called directly in tests.
"""

import logging
import os
from typing import NamedTuple

from netdrift.appliers.base import RemediationBlockedError
from netdrift.appliers.registry import get_applier
from netdrift.storage.models import KnownIssue, RemediationEvent
from netdrift.storage.repository import save_remediation_event, set_auto_apply_enabled

_log = logging.getLogger(__name__)

# Consecutive scheduler failures before auto_apply_enabled is cleared.
FAILURE_THRESHOLD = 3


class AutoApplyOutcome(NamedTuple):
    """Lightweight result record returned for each drift processed.

    Avoids returning SQLAlchemy ORM objects that would be detached (and
    therefore inaccessible) once the session context manager closes.
    """
    known_issue_id: int
    result: str      # "success" | "failure" | "blocked"
    platform: str


def _is_auto_remediation_enabled() -> bool:
    return os.environ.get("AUTO_REMEDIATION_ENABLED", "").lower() == "true"


def _fingerprint(drift: dict) -> str:
    """Derive the known-issue fingerprint from a drift record."""
    object_type = drift["object"].split(":")[0]
    return f"{object_type}|{drift['field']}|{drift['drift_kind']}"


def _consecutive_failures(session, known_issue_id: int) -> int:
    """Count consecutive scheduler failures for a KnownIssue since the last success.

    Reads the most recent FAILURE_THRESHOLD scheduler-applied events. Returns
    how many leading failures there are before any success — so 3 failures in a
    row returns 3 regardless of older history.
    """
    recent = (
        session.query(RemediationEvent)
        .filter(
            RemediationEvent.known_issue_id == known_issue_id,
            RemediationEvent.applied_by == "scheduler",
            RemediationEvent.result.in_(["success", "failure"]),
        )
        .order_by(RemediationEvent.applied_at.desc())
        .limit(FAILURE_THRESHOLD)
        .all()
    )
    count = 0
    for event in recent:
        if event.result == "failure":
            count += 1
        else:
            break
    return count


def _default_is_device_paused(device_name: str, session) -> bool:
    # Returns False until the device_settings table (v3.0 Matthew task) is
    # wired in. Replace this default once that migration lands.
    return False  # noqa: PIE807


def run_auto_apply(
    drifts: list[dict],
    device: dict,
    session_factory,
    *,
    applier_fn=get_applier,
    is_device_paused_fn=_default_is_device_paused,
    schedule_repoll_fn=None,
) -> list:
    """Cross-reference persisted drifts against known_issues and apply matching fixes.

    Args:
        drifts: drift records as returned by differ.diff(), already stamped with
            ``device`` and ``platform`` by pipeline.run_drift_check.
        device: device dict with at least ``name``, ``hostname``, ``username``,
            ``password`` (passed through to the applier).
        session_factory: callable() -> Session context manager. Tests inject an
            in-memory SQLite factory; production uses get_sessionmaker().
        applier_fn: callable(platform) -> apply_fn. Defaults to get_applier;
            tests inject a fake.
        is_device_paused_fn: callable(device_name, session) -> bool. Returns True
            if auto-apply is paused for this device (per-device kill-switch).
            Defaults to a no-op stub until device_settings table is available.
        schedule_repoll_fn: optional callable(device) invoked once after any
            successful apply to trigger a re-poll outside the normal schedule.
            The scheduler passes its own re-poll helper; tests assert it is called.

    Returns a list of RemediationEvent ORM rows written during this call (one per
    matched drift, regardless of outcome).
    """
    if not _is_auto_remediation_enabled():
        return []

    if not drifts:
        return []

    outcomes: list[AutoApplyOutcome] = []

    with session_factory() as session:
        device_name = device["name"]

        if is_device_paused_fn(device_name, session):
            _log.info("Auto-apply paused for device %r; skipping cycle.", device_name)
            return []

        for drift in drifts:
            fp = _fingerprint(drift)

            issue = (
                session.query(KnownIssue)
                .filter(
                    KnownIssue.fingerprint == fp,
                    KnownIssue.auto_apply_enabled.is_(True),
                )
                .one_or_none()
            )
            if issue is None:
                continue

            remediation = issue.remediation or {}

            # Only restore_intent is safe for unattended auto-apply.
            # raw_snippet commands are arbitrary config text — require a human
            # to manually apply at least once before auto-apply can be considered.
            if remediation.get("kind") != "restore_intent":
                continue

            platform = drift.get("platform", "")
            result = "failure"
            rendered_commands = ""
            dry_run_diff = ""

            try:
                applier = applier_fn(platform)
            except KeyError:
                _log.warning(
                    "No applier for platform %r on device %r; skipping %s.",
                    platform, device_name, fp,
                )
                continue

            try:
                apply_result = applier(remediation, drift, device, dry_run=False)
                result = "success"
                rendered_commands = apply_result.rendered_commands
                dry_run_diff = apply_result.dry_run_diff
                _log.info(
                    "Auto-apply succeeded: device=%r fingerprint=%r known_issue_id=%d",
                    device_name, fp, issue.id,
                )
            except RemediationBlockedError as exc:
                result = "blocked"
                rendered_commands = str(exc)
                _log.info(
                    "Auto-apply blocked: device=%r fingerprint=%r reason=%s",
                    device_name, fp, exc,
                )
            except Exception as exc:  # noqa: BLE001
                result = "failure"
                rendered_commands = f"{type(exc).__name__}: {exc}"
                _log.warning(
                    "Auto-apply failed: device=%r fingerprint=%r error=%s",
                    device_name, fp, exc,
                )

            save_remediation_event(
                session,
                known_issue_id=issue.id,
                platform=platform,
                rendered_commands=rendered_commands,
                dry_run_diff=dry_run_diff,
                result=result,
                applied_by="scheduler",
            )
            session.commit()
            outcomes.append(AutoApplyOutcome(
                known_issue_id=issue.id,
                result=result,
                platform=platform,
            ))

            if result == "failure":
                consecutive = _consecutive_failures(session, issue.id)
                if consecutive >= FAILURE_THRESHOLD:
                    set_auto_apply_enabled(session, issue.id, False)
                    session.commit()
                    _log.warning(
                        "Auto-apply disabled for known_issue_id=%d after %d consecutive "
                        "failures (fingerprint=%r). Re-enable via API after investigating.",
                        issue.id, consecutive, fp,
                    )

        # Repoll check inside the session block — outcomes are populated here.
        if schedule_repoll_fn is not None and any(
            o.result == "success" for o in outcomes
        ):
            schedule_repoll_fn(device)

    return outcomes
