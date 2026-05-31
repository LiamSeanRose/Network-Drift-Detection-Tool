"""api/app.py — FastAPI application (v0.2 / v2.5).

A thin HTTP layer over the storage layer. It does not compute drift or talk to
devices directly; it serves drift events that storage.repository has already
persisted and orchestrates remediation via the applier registry.

Run it locally with:
    uvicorn netdrift.api.app:app --reload

Then visit http://localhost:8001/health  or  http://localhost:8001/docs

Requires DATABASE_URL set in the environment (same var the storage layer and
Alembic use), e.g.
    postgresql+psycopg://postgres:devpassword@localhost:5432/netdrift

v2.5 environment variables:
    AUTO_REMEDIATION_ENABLED  — set to "true" to allow auto_apply_enabled=True
                                on known issues (default: false)
    CONFIRM_THRESHOLD         — successful applies required before auto-apply
                                can be enabled on a known issue (default: 3)
"""

import logging
import os
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netdrift.appliers.base import RemediationBlockedError, check_blocked
from netdrift.appliers.registry import get_applier
from netdrift.diagnose import diagnose
from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.storage.database import get_sessionmaker
from netdrift.storage.repository import (
    confirmed_count,
    get_drift_event,
    get_known_issue_by_id,
    get_remediation_events,
    get_drifts,
    get_drift_history,
    list_known_issues,
    save_known_issue,
    save_remediation_event,
    set_auto_apply_enabled,
    update_known_issue_remediation,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="netdrift API", version="0.2.0")

# ---------------------------------------------------------------------------
# v2.5 configuration
# ---------------------------------------------------------------------------

AUTO_REMEDIATION_ENABLED: bool = (
    os.environ.get("AUTO_REMEDIATION_ENABLED", "false").lower() == "true"
)
CONFIRM_THRESHOLD: int = int(os.environ.get("CONFIRM_THRESHOLD", "3"))

# devices.yml lives at the repo root (three package levels up from this file).
_DEVICES_FILE = Path(__file__).resolve().parents[3] / "devices.yml"
_devices_cache: dict | None = None


def _load_devices() -> dict:
    """Load devices.yml lazily (once per process). Returns {} if file missing."""
    global _devices_cache
    if _devices_cache is None:
        if _DEVICES_FILE.exists():
            with open(_DEVICES_FILE) as f:
                _devices_cache = yaml.safe_load(f) or {}
        else:
            _devices_cache = {}
    return _devices_cache


def _get_device(device_name: str) -> dict:
    """Return the device dict for device_name, or raise 404."""
    devices = _load_devices()
    if device_name not in devices:
        raise HTTPException(
            status_code=404,
            detail=f"Device '{device_name}' not found in devices.yml.",
        )
    return {"name": device_name, **devices[device_name]}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class KnownIssueIn(BaseModel):
    """Request body for POST /known-issues."""
    object: str
    field: str
    drift_kind: str
    cause: str
    fix: str
    remediation: dict | None = None  # optional; null means diagnosis-only


class RemediationPayloadIn(BaseModel):
    """Request body for PATCH /known-issues/{id}."""
    remediation: dict | None = None


class AutoApplyIn(BaseModel):
    """Request body for PATCH /known-issues/{id}/auto-apply."""
    enabled: bool


class RemediateRequest(BaseModel):
    """Request body for dry-run and apply endpoints."""
    drift_event_id: int


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------

_SessionLocal = None


def _get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = get_sessionmaker()
    return _SessionLocal


def get_session():
    """FastAPI dependency: yield one database session per request."""
    session = _get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue_dict(issue, count: int) -> dict:
    """Serialize a KnownIssue row to a response dict."""
    return {
        "id": issue.id,
        "fingerprint": issue.fingerprint,
        "cause": issue.cause,
        "fix": issue.fix,
        "created_at": issue.created_at.isoformat(),
        "confirmed_count": count,
        "remediation": issue.remediation,
        "auto_apply_enabled": issue.auto_apply_enabled,
    }


def _known_fix_dict(issue, count: int) -> dict | None:
    """Return the known-fix payload for a GET /drifts response entry, or None."""
    if issue is None:
        return None
    return {
        "id": issue.id,
        "cause": issue.cause,
        "fix": issue.fix,
        "confirmed_count": count,
        "remediation": issue.remediation,
        "auto_apply_enabled": issue.auto_apply_enabled,
    }


def _remediation_event_dict(ev) -> dict:
    return {
        "id": ev.id,
        "known_issue_id": ev.known_issue_id,
        "drift_event_id": ev.drift_event_id,
        "platform": ev.platform,
        "rendered_commands": ev.rendered_commands,
        "dry_run_diff": ev.dry_run_diff,
        "result": ev.result,
        "applied_by": ev.applied_by,
        "applied_at": ev.applied_at.isoformat(),
    }


def _validate_remediation_kind(kind: str | None) -> None:
    """Raise 422 if kind is not one of the allowed discriminator values."""
    allowed = {"restore_intent", "raw_snippet", None}
    if kind not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"remediation.kind must be one of {sorted(str(k) for k in allowed if k)!r} or null.",
        )


# ---------------------------------------------------------------------------
# Routes — health + drift events
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Liveness check — no database involved."""
    return {"status": "ok"}


@app.get("/drifts/history")
def list_drift_history(device: str | None = None, hours: int = 24,
                       session: Session = Depends(get_session)):
    """Return drift counts bucketed into 5-minute intervals, oldest first."""
    return get_drift_history(session, hours=hours, device=device)


@app.get("/drifts")
def list_drifts(device: str | None = None, limit: int = 100,
                session: Session = Depends(get_session)):
    """Return stored drift events as JSON, newest first."""
    events = get_drifts(session, device=device, limit=limit)
    all_issues = list_known_issues(session)
    known = {i.fingerprint: i for i in all_issues}
    counts = {i.id: confirmed_count(session, i.id) for i in all_issues}

    rows = []
    for e in events:
        fp = make_fingerprint({"object": e.object_ref, "field": e.field, "drift_kind": e.drift_kind})
        issue = known.get(fp)
        rows.append({
            "id": e.id,
            "device": e.device,
            "object": e.object_ref,
            "field": e.field,
            "intent": e.intent,
            "reality": e.reality,
            "drift_kind": e.drift_kind,
            "severity": e.severity,
            "detected_at": e.detected_at.isoformat(),
            "platform": e.platform,
            "causes": diagnose({
                "object": e.object_ref,
                "field": e.field,
                "drift_kind": e.drift_kind,
            }),
            "known_fix": _known_fix_dict(issue, counts.get(issue.id, 0) if issue else 0),
        })
    return rows


# ---------------------------------------------------------------------------
# Routes — known issues (CRUD + remediation payload)
# ---------------------------------------------------------------------------

@app.post("/known-issues")
def create_known_issue(body: KnownIssueIn, session: Session = Depends(get_session)):
    """Record a cause and fix for a drift pattern identified by its fingerprint."""
    fp = make_fingerprint({"object": body.object, "field": body.field, "drift_kind": body.drift_kind})
    if body.remediation is not None:
        _validate_remediation_kind(body.remediation.get("kind"))
    issue = save_known_issue(session, fp, body.cause, body.fix, body.remediation)
    session.commit()
    return _issue_dict(issue, 0)


@app.get("/known-issues")
def get_all_known_issues(session: Session = Depends(get_session)):
    """Return all stored known issues, oldest first."""
    issues = list_known_issues(session)
    return [_issue_dict(i, confirmed_count(session, i.id)) for i in issues]


@app.patch("/known-issues/{issue_id}")
def patch_known_issue(issue_id: int, body: RemediationPayloadIn,
                      session: Session = Depends(get_session)):
    """Update the remediation payload on an existing known issue."""
    if body.remediation is not None:
        _validate_remediation_kind(body.remediation.get("kind"))
    issue = update_known_issue_remediation(session, issue_id, body.remediation)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Known issue {issue_id} not found.")
    # If the kind changed to raw_snippet or null, auto_apply_enabled must be cleared.
    kind = (issue.remediation or {}).get("kind") if issue.remediation else None
    if kind in ("raw_snippet", None) and issue.auto_apply_enabled:
        set_auto_apply_enabled(session, issue_id, False)
        issue.auto_apply_enabled = False
    session.commit()
    return _issue_dict(issue, confirmed_count(session, issue_id))


@app.patch("/known-issues/{issue_id}/auto-apply")
def patch_auto_apply(issue_id: int, body: AutoApplyIn,
                     session: Session = Depends(get_session)):
    """Enable or disable per-issue auto-apply.

    Enabling is subject to three gates:
      1. remediation.kind must be "restore_intent".
      2. confirmed_count must be >= CONFIRM_THRESHOLD (default 3).
      3. The global AUTO_REMEDIATION_ENABLED kill-switch must be true.
    """
    issue = get_known_issue_by_id(session, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Known issue {issue_id} not found.")

    if body.enabled:
        kind = (issue.remediation or {}).get("kind") if issue.remediation else None
        if kind != "restore_intent":
            raise HTTPException(
                status_code=422,
                detail="auto_apply_enabled can only be true when remediation.kind is 'restore_intent'.",
            )
        count = confirmed_count(session, issue_id)
        if count < CONFIRM_THRESHOLD:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"auto_apply_enabled requires at least {CONFIRM_THRESHOLD} confirmed successful "
                    f"remediations; this issue has {count}."
                ),
            )
        if not AUTO_REMEDIATION_ENABLED:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The global AUTO_REMEDIATION_ENABLED kill-switch is off. "
                    "Set AUTO_REMEDIATION_ENABLED=true in the server environment to allow auto-apply."
                ),
            )

    updated = set_auto_apply_enabled(session, issue_id, body.enabled)
    session.commit()
    logger.info(
        "auto_apply_enabled set to %s for known_issue_id=%d (applied_by=api)",
        body.enabled,
        issue_id,
    )
    return _issue_dict(updated, confirmed_count(session, issue_id))


# ---------------------------------------------------------------------------
# Routes — remediation (dry-run + apply + audit log)
# ---------------------------------------------------------------------------

def _build_drift_record(event) -> dict:
    """Reconstruct a drift record dict from a DriftEvent row."""
    return {
        "device": event.device,
        "object": event.object_ref,
        "field": event.field,
        "intent": event.intent,
        "reality": event.reality,
        "drift_kind": event.drift_kind,
        "severity": event.severity,
        "detected_at": event.detected_at.isoformat(),
    }


@app.post("/known-issues/{issue_id}/remediate/dry-run")
def remediate_dry_run(issue_id: int, body: RemediateRequest,
                      session: Session = Depends(get_session)):
    """Run a live dry-run for a known-issue fix and return the candidate diff.

    Delegates rendering to the platform's registered applier. The diff comes
    from a live device call (NAPALM compare_config / gNMI read-back) — never
    from the stored payload.

    Records a dry_run_only event in remediation_events.
    """
    issue = get_known_issue_by_id(session, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Known issue {issue_id} not found.")

    remediation = issue.remediation
    if not remediation or remediation.get("kind") is None:
        raise HTTPException(
            status_code=422,
            detail="This known issue has no executable remediation payload (kind=null).",
        )

    drift_event = get_drift_event(session, body.drift_event_id)
    if drift_event is None:
        raise HTTPException(
            status_code=404,
            detail=f"Drift event {body.drift_event_id} not found.",
        )

    platform = drift_event.platform
    if not platform:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Drift event {body.drift_event_id} has no platform stored. "
                "Re-run a drift check to populate it."
            ),
        )

    device = _get_device(drift_event.device)
    drift_record = _build_drift_record(drift_event)

    try:
        check_blocked(drift_record, device)
    except RemediationBlockedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        applier = get_applier(platform)
    except KeyError:
        raise HTTPException(
            status_code=422,
            detail=f"No applier registered for platform '{platform}'.",
        )

    try:
        result = applier(remediation, drift_record, device, dry_run=True)
    except RemediationBlockedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Dry-run failed: {exc}")

    save_remediation_event(
        session,
        known_issue_id=issue_id,
        platform=platform,
        rendered_commands=result.rendered_commands,
        dry_run_diff=result.dry_run_diff,
        result="dry_run_only",
        applied_by="api",
        drift_event_id=body.drift_event_id,
    )
    session.commit()

    return {
        "transport": result.transport,
        "rendered_commands": result.rendered_commands,
        "dry_run_diff": result.dry_run_diff,
        "would_apply": False,
    }


@app.post("/known-issues/{issue_id}/remediate/apply")
def remediate_apply(issue_id: int, body: RemediateRequest,
                    session: Session = Depends(get_session)):
    """Apply a known-issue fix to the affected device.

    After a successful apply, schedules a post-apply re-poll (≤60s) to verify
    the fix took effect. The re-poll result appears in the next GET /drifts.

    Records a success or failure event in remediation_events.
    """
    issue = get_known_issue_by_id(session, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Known issue {issue_id} not found.")

    remediation = issue.remediation
    if not remediation or remediation.get("kind") is None:
        raise HTTPException(
            status_code=422,
            detail="This known issue has no executable remediation payload (kind=null).",
        )

    drift_event = get_drift_event(session, body.drift_event_id)
    if drift_event is None:
        raise HTTPException(
            status_code=404,
            detail=f"Drift event {body.drift_event_id} not found.",
        )

    platform = drift_event.platform
    if not platform:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Drift event {body.drift_event_id} has no platform stored. "
                "Re-run a drift check to populate it."
            ),
        )

    device = _get_device(drift_event.device)
    drift_record = _build_drift_record(drift_event)

    try:
        check_blocked(drift_record, device)
    except RemediationBlockedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        applier = get_applier(platform)
    except KeyError:
        raise HTTPException(
            status_code=422,
            detail=f"No applier registered for platform '{platform}'.",
        )

    apply_error: str | None = None
    result_obj = None
    try:
        result_obj = applier(remediation, drift_record, device, dry_run=False)
        event_result = "success"
    except RemediationBlockedError as exc:
        apply_error = str(exc)
        event_result = "failure"
    except Exception as exc:  # noqa: BLE001
        apply_error = str(exc)
        event_result = "failure"

    saved_event = save_remediation_event(
        session,
        known_issue_id=issue_id,
        platform=platform,
        rendered_commands=result_obj.rendered_commands if result_obj else "",
        dry_run_diff=result_obj.dry_run_diff if result_obj else "",
        result=event_result,
        applied_by="api",
        drift_event_id=body.drift_event_id,
    )
    session.commit()

    if event_result == "failure":
        raise HTTPException(status_code=502, detail=f"Apply failed: {apply_error}")

    # Schedule a post-apply re-poll to verify the fix took effect.
    try:
        from netdrift.api.repoll import schedule_repoll
        schedule_repoll(device)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not schedule post-apply re-poll: %s", exc)

    return {
        "transport": result_obj.transport,
        "rendered_commands": result_obj.rendered_commands,
        "dry_run_diff": result_obj.dry_run_diff,
        "applied": result_obj.applied,
        "remediation_event_id": saved_event.id,
    }


@app.get("/known-issues/{issue_id}/remediation-events")
def get_issue_remediation_events(issue_id: int, session: Session = Depends(get_session)):
    """Return the full remediation audit log for a known issue, newest first."""
    issue = get_known_issue_by_id(session, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Known issue {issue_id} not found.")
    return [_remediation_event_dict(ev) for ev in get_remediation_events(session, issue_id)]
