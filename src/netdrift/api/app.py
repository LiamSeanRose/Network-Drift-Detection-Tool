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
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netdrift.appliers.base import RemediationBlockedError, check_blocked
from netdrift.appliers.registry import get_applier
from netdrift.diagnose import diagnose
from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.storage.database import get_sessionmaker
from netdrift.webhook import WebhookDispatcher
from netdrift.storage.repository import (
    confirmed_count,
    create_acknowledgement,
    create_alert_rule,
    create_api_key,
    delete_acknowledgements_for,
    delete_alert_rule,
    delete_api_key,
    get_device_setting,
    get_drift_event,
    list_alert_rules,
    list_api_keys,
    get_known_issue_by_id,
    get_remediation_events,
    get_drifts,
    get_drift_history,
    is_acknowledged,
    list_known_issues,
    save_known_issue,
    save_remediation_event,
    set_auto_apply_enabled,
    set_device_paused,
    update_known_issue_remediation,
    verify_api_key,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="netdrift API", version="0.2.0")


# ---------------------------------------------------------------------------
# SEC1 — CORS posture. Pinned explicitly so a future change can't silently
# enable `allow_origins=["*"]` over an unauthenticated, mutating API.
# Origins come from CORS_ALLOW_ORIGINS (comma-separated); default empty means
# no cross-origin access (same-origin only), identical to having no middleware.
# A wildcard is never honored while the API ships without auth on GET.
# ---------------------------------------------------------------------------

def _parse_cors_origins(raw: str | None) -> list[str]:
    """Parse CORS_ALLOW_ORIGINS into an explicit origin list, dropping `*`.

    Returns [] for an unset/empty value (no cross-origin). Any `*` entry is
    discarded with a warning rather than honored — wildcard CORS over an
    unauthenticated mutating API would turn it into a drive-by target.
    """
    if not raw:
        return []
    origins = []
    for part in raw.split(","):
        origin = part.strip()
        if not origin:
            continue
        if origin == "*":
            logger.warning(
                "CORS_ALLOW_ORIGINS contains '*'; wildcard CORS is refused while "
                "the API is unauthenticated. Set explicit origins instead."
            )
            continue
        origins.append(origin)
    return origins


_CORS_ORIGINS = _parse_cors_origins(os.environ.get("CORS_ALLOW_ORIGINS"))
if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ---------------------------------------------------------------------------
# v2.5 configuration
# ---------------------------------------------------------------------------

AUTO_REMEDIATION_ENABLED: bool = (
    os.environ.get("AUTO_REMEDIATION_ENABLED", "false").lower() == "true"
)
CONFIRM_THRESHOLD: int = int(os.environ.get("CONFIRM_THRESHOLD", "3"))

# v3.5: the longest an acknowledgement may suppress alerting. A drift silenced
# forever by accident is a liability, so an explicit expiry is capped.
MAX_ACK_DAYS: int = 90

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


class DeviceAutoApplyIn(BaseModel):
    """Request body for PATCH /devices/{name}/auto-apply."""
    paused: bool
    reason: str | None = None


class ApiKeyIn(BaseModel):
    """Request body for POST /api-keys."""
    name: str
    expires_at: datetime | None = None  # null = never expires


class AcknowledgeIn(BaseModel):
    """Request body for POST /drifts/{id}/acknowledge."""
    acknowledged_until: datetime | None = None  # null = permanent


class AlertRuleIn(BaseModel):
    """Request body for POST /alert-rules."""
    device: str | None = None  # null = all devices
    severity: str
    window_minutes: int
    enabled: bool = True


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
# API-key authentication middleware (v3.5 Feature 5)
# ---------------------------------------------------------------------------

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Require a valid X-API-Key on every mutating request.

    Auth model — a deliberate joint decision (Matthew + Liam), not an oversight:
    GET/HEAD/OPTIONS requests are unauthenticated. Drift data (``GET /drifts``)
    and the ``/health`` probe are public-by-default for self-hosted deployments.
    Only mutating methods (POST/PUT/PATCH/DELETE) require a key, so every
    current and future write endpoint is protected without per-route wiring.

    The X-API-Key header value is never logged.
    """
    if request.method.upper() in _MUTATING_METHODS:
        presented = request.headers.get("X-API-Key")
        if not presented:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing API key. Provide a valid X-API-Key header."},
            )
        # Resolve a session the same way routes do, honoring dependency
        # overrides so tests authenticate against their in-memory database.
        session_factory = app.dependency_overrides.get(get_session, get_session)
        gen = session_factory()
        session = next(gen)
        try:
            if verify_api_key(session, presented) is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired API key."},
                )
            session.commit()  # persist last_used_at
        finally:
            gen.close()
    return await call_next(request)


# ---------------------------------------------------------------------------
# Webhook dispatcher (v3.0) — one per API process, same class the scheduler uses
# ---------------------------------------------------------------------------

_webhook_dispatcher = None


def _get_dispatcher():
    """Return the process-wide WebhookDispatcher, starting it on first use.

    Reads WEBHOOK_URL / WEBHOOK_EVENTS from the environment; with no WEBHOOK_URL
    it is disabled and fire() is a no-op. Memoized so the API process keeps a
    single daemon worker. Tests monkeypatch the module global with a fake.
    """
    global _webhook_dispatcher
    if _webhook_dispatcher is None:
        _webhook_dispatcher = WebhookDispatcher()
        _webhook_dispatcher.start()
    return _webhook_dispatcher


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
def list_drift_history(device: str | None = None,
                       hours: int = Query(24, ge=1, le=168),
                       session: Session = Depends(get_session)):
    """Return drift counts bucketed into 5-minute intervals, oldest first."""
    return get_drift_history(session, hours=hours, device=device)


@app.get("/drifts")
def list_drifts(request: Request, response: Response,
                device: str | None = None,
                limit: int = Query(100, ge=1, le=1000),
                offset: int = Query(0, ge=0),
                since: str | None = None,
                session: Session = Depends(get_session)):
    """Return stored drift events as JSON, newest first.

    ``since`` (ISO 8601) returns only events at or after that time. ``limit`` and
    ``offset`` paginate; when a full page is returned a ``Link: <next>; rel="next"``
    header points at the following page.
    """
    since_dt = None
    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=422, detail="since must be an ISO 8601 datetime."
            )

    events = get_drifts(session, device=device, limit=limit, offset=offset, since=since_dt)
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
            "acknowledged": is_acknowledged(session, e.device, fp),
        })

    # A full page implies there may be more; point at the next one.
    if limit and len(events) == limit:
        next_url = request.url.include_query_params(offset=offset + limit)
        response.headers["Link"] = f'<{next_url}>; rel="next"'
    return rows


def _ack_dict(ack) -> dict:
    """Serialize an Acknowledgement row to a response dict."""
    return {
        "id": ack.id,
        "device": ack.device,
        "fingerprint": ack.fingerprint,
        "acknowledged_until": (
            ack.acknowledged_until.isoformat() if ack.acknowledged_until else None
        ),
        "created_at": ack.created_at.isoformat(),
    }


@app.post("/drifts/{event_id}/acknowledge")
def acknowledge_drift(event_id: int, body: AcknowledgeIn,
                      session: Session = Depends(get_session)):
    """Acknowledge a drift event — "this is intentional, stop alerting".

    Recorded against the event's (device, fingerprint), not its row id, so the
    acknowledgement persists across poll cycles. An active acknowledgement
    suppresses webhook dispatch, SLA evaluation, and auto-apply for the matching
    drift pattern. acknowledged_until=null is permanent; a past date or a window
    longer than MAX_ACK_DAYS (90) is rejected with 422.
    """
    event = get_drift_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Drift event {event_id} not found.")

    until = body.acknowledged_until
    if until is not None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        if until <= now:
            raise HTTPException(
                status_code=422, detail="acknowledged_until must be in the future."
            )
        if until > now + timedelta(days=MAX_ACK_DAYS):
            raise HTTPException(
                status_code=422,
                detail=f"acknowledged_until may be at most {MAX_ACK_DAYS} days in the future.",
            )

    fp = make_fingerprint({
        "object": event.object_ref, "field": event.field, "drift_kind": event.drift_kind,
    })
    ack = create_acknowledgement(session, event.device, fp, acknowledged_until=until)
    session.commit()
    logger.info(
        "drift acknowledged: device=%r fingerprint=%r until=%s (applied_by=api)",
        event.device, fp, until.isoformat() if until else "permanent",
    )
    return _ack_dict(ack)


@app.delete("/drifts/{event_id}/acknowledge")
def unacknowledge_drift(event_id: int, session: Session = Depends(get_session)):
    """Un-acknowledge a drift event (toggle off) — remove any acknowledgement
    for the event's (device, fingerprint) so it alerts again. 404 if the event
    is unknown."""
    event = get_drift_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Drift event {event_id} not found.")
    fp = make_fingerprint({
        "object": event.object_ref, "field": event.field, "drift_kind": event.drift_kind,
    })
    deleted = delete_acknowledgements_for(session, event.device, fp)
    session.commit()
    logger.info(
        "drift un-acknowledged: device=%r fingerprint=%r removed=%d (applied_by=api)",
        event.device, fp, deleted,
    )
    return {"deleted": deleted, "device": event.device, "fingerprint": fp}


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


@app.get("/known-issues/export")
def export_known_issues_endpoint(session: Session = Depends(get_session)):
    """Export all known issues as importable pattern YAML.

    Round-trips with ``driftcheck import-patterns``: export → import → export is
    byte-identical. This is a public GET, consistent with ``GET /known-issues``,
    which already serves the same known-issue data (remediation included)
    unauthenticated under the deliberate GET-is-public auth model.
    """
    from netdrift.patterns.exporter import export_known_issues

    return Response(content=export_known_issues(session), media_type="application/x-yaml")


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
# Routes — per-device auto-apply kill-switch
# ---------------------------------------------------------------------------

def _device_setting_dict(setting) -> dict:
    return {
        "device_name": setting.device_name,
        "auto_remediation_paused": setting.auto_remediation_paused,
        "paused_at": setting.paused_at.isoformat() if setting.paused_at else None,
        "paused_reason": setting.paused_reason,
    }


@app.get("/devices")
def list_devices(session: Session = Depends(get_session)):
    """List inventory devices with their auto-apply pause state.

    Each device from devices.yml is returned with its device_settings pause
    state (absent row → not paused, the safe default). Backs the dashboard's
    per-device auto-apply toggle. Read-only, so unauthenticated by design.
    """
    result = []
    for name in sorted(_load_devices()):
        setting = get_device_setting(session, name)
        result.append({
            "name": name,
            "auto_remediation_paused": bool(setting and setting.auto_remediation_paused),
            "paused_at": (
                setting.paused_at.isoformat() if setting and setting.paused_at else None
            ),
            "paused_reason": setting.paused_reason if setting else None,
        })
    return result


@app.patch("/devices/{device_name}/auto-apply")
def patch_device_auto_apply(device_name: str, body: DeviceAutoApplyIn,
                            session: Session = Depends(get_session)):
    """Pause or resume auto-apply for one device — the per-device runtime
    kill-switch.

    Unlike the global AUTO_REMEDIATION_ENABLED env var (which needs a process
    restart), this takes effect on the next poll cycle: run_auto_apply consults
    is_device_paused() before dispatching any apply for the device. Use it to
    stop auto-remediation on a device that auto-apply is actively harming.

    404 if the device is not in devices.yml. API-only in v3.0; the dashboard
    toggle is added in v3.5.
    """
    _get_device(device_name)  # 404 if the device is unknown
    setting = set_device_paused(session, device_name, body.paused, body.reason)
    session.commit()
    logger.info(
        "auto_remediation_paused set to %s for device=%r (applied_by=api)",
        body.paused, device_name,
    )
    return _device_setting_dict(setting)


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
        # SEC4: never echo raw NAPALM/gNMI text (hostnames, IPs, partial config)
        # to the client. Log the detail server-side under a ref the operator can
        # correlate; return a generic message.
        ref = uuid.uuid4().hex[:8]
        logger.warning("Dry-run failed (ref=%s) for known issue %s: %s", ref, issue_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Dry-run failed; see server logs (ref={ref}).",
        )

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
                    background_tasks: BackgroundTasks,
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

    # v3.0: notify on the apply result via the same dispatcher the scheduler uses.
    dispatcher = _get_dispatcher()
    webhook_payload = {
        "device": drift_event.device,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "detail": (
            f"known_issue_id={issue_id} platform={platform} "
            f"drift_event_id={body.drift_event_id}"
        ),
    }

    if event_result == "failure":
        # The 502 below replaces the response, so a BackgroundTask would never
        # run — fire directly. fire() only enqueues, so it does not block.
        dispatcher.fire("apply_failure", webhook_payload)
        # SEC4: log the raw applier error server-side; return a generic message
        # so device hostnames/IPs/config never reach the client.
        ref = uuid.uuid4().hex[:8]
        logger.warning("Apply failed (ref=%s) for known issue %s: %s", ref, issue_id, apply_error)
        raise HTTPException(
            status_code=502,
            detail=f"Apply failed; see server logs (ref={ref}).",
        )

    # Success returns normally; defer the (already non-blocking) dispatch to a
    # BackgroundTask per the v3.0 design.
    background_tasks.add_task(dispatcher.fire, "apply_success", webhook_payload)

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


# ---------------------------------------------------------------------------
# Routes — API keys (v3.5)
# ---------------------------------------------------------------------------

def _api_key_dict(key) -> dict:
    """Serialize an ApiKey row for listing — never the raw key or its hash."""
    return {
        "id": key.id,
        "name": key.name,
        "key_hint": key.key_hint,
        "created_at": key.created_at.isoformat(),
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
    }


@app.post("/api-keys")
def create_api_key_endpoint(body: ApiKeyIn, session: Session = Depends(get_session)):
    """Mint a new API key. The raw key is returned exactly once, here — it is
    never stored in plaintext and cannot be retrieved again. Requires a valid
    X-API-Key (the first key is bootstrapped via the driftcheck CLI)."""
    raw_key, key = create_api_key(session, body.name, expires_at=body.expires_at)
    session.commit()
    logger.info("API key created: id=%d name=%r (the raw key is shown once)", key.id, key.name)
    return {**_api_key_dict(key), "key": raw_key}


@app.get("/api-keys")
def list_api_keys_endpoint(session: Session = Depends(get_session)):
    """List API keys (metadata only — never the raw key or its hash)."""
    return [_api_key_dict(k) for k in list_api_keys(session)]


@app.delete("/api-keys/{key_id}")
def delete_api_key_endpoint(key_id: int, session: Session = Depends(get_session)):
    """Revoke an API key. The key 401s on its next request."""
    if not delete_api_key(session, key_id):
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found.")
    session.commit()
    logger.info("API key revoked: id=%d", key_id)
    return {"deleted": True, "id": key_id}


# ---------------------------------------------------------------------------
# Routes — SLA alert rules (v3.5)
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = {"critical", "warning", "info"}


def _alert_rule_dict(rule) -> dict:
    return {
        "id": rule.id,
        "device": rule.device,
        "severity": rule.severity,
        "window_minutes": rule.window_minutes,
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
    }


@app.post("/alert-rules")
def create_alert_rule_endpoint(body: AlertRuleIn, session: Session = Depends(get_session)):
    """Create an SLA alert rule. device=null applies to all devices."""
    if body.severity not in _VALID_SEVERITIES:
        raise HTTPException(
            status_code=422,
            detail=f"severity must be one of {sorted(_VALID_SEVERITIES)!r}.",
        )
    if body.window_minutes <= 0:
        raise HTTPException(status_code=422, detail="window_minutes must be positive.")
    rule = create_alert_rule(
        session, body.device, body.severity, body.window_minutes, enabled=body.enabled
    )
    session.commit()
    return _alert_rule_dict(rule)


@app.get("/alert-rules")
def list_alert_rules_endpoint(session: Session = Depends(get_session)):
    """List all SLA alert rules, oldest first."""
    return [_alert_rule_dict(r) for r in list_alert_rules(session)]


@app.delete("/alert-rules/{rule_id}")
def delete_alert_rule_endpoint(rule_id: int, session: Session = Depends(get_session)):
    """Delete an SLA alert rule."""
    if not delete_alert_rule(session, rule_id):
        raise HTTPException(status_code=404, detail=f"Alert rule {rule_id} not found.")
    session.commit()
    return {"deleted": True, "id": rule_id}
