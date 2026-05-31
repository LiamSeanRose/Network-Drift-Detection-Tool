"""api/app.py — FastAPI application (v0.2).

A thin HTTP layer over the storage layer. It does not compute drift or talk to
devices; it serves drift events that storage.repository has already persisted.

Run it locally with:
    uvicorn netdrift.api.app:app --reload

Then visit http://localhost:8000/health  or  http://localhost:8000/docs

Requires DATABASE_URL set in the environment (same var the storage layer and
Alembic use), e.g.
    postgresql+psycopg://postgres:devpassword@localhost:5432/netdrift
"""

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netdrift.diagnose import diagnose
from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.storage.database import get_sessionmaker
from netdrift.storage.repository import (
    get_drifts,
    get_drift_history,
    list_known_issues,
    save_known_issue,
)

app = FastAPI(title="netdrift API", version="0.2.0")


class KnownIssueIn(BaseModel):
    """Request body for POST /known-issues."""
    object: str   # e.g. "interface:Ethernet1" or "bgp_neighbor:10.0.0.1"
    field: str
    drift_kind: str
    cause: str
    fix: str


def _known_fix_dict(issue):
    """Return {cause, fix} dict for a KnownIssue row, or None."""
    if issue is None:
        return None
    return {"cause": issue.cause, "fix": issue.fix}

# The sessionmaker is built lazily on first use, NOT at import time. Building
# it reads DATABASE_URL, and importing this module must not require a database
# to be configured (e.g. CI imports the app to test /health, which touches no
# database). _get_sessionmaker() creates it once on first request and caches it.
_SessionLocal = None


def _get_sessionmaker():
    """Return the app's sessionmaker, creating it on first use."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = get_sessionmaker()
    return _SessionLocal


def get_session():
    """FastAPI dependency: yield one database session per request.

    FastAPI calls this for any endpoint that declares it (see /drifts). The
    code before `yield` is setup (open a session); the code after runs as
    cleanup once the response is sent (close it). This guarantees every
    request gets a fresh session and no session is ever left open.
    """
    session = _get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@app.get("/health")
def health():
    """Liveness check — proves the API is up. No database involved."""
    return {"status": "ok"}


@app.get("/drifts/history")
def list_drift_history(device: str | None = None, hours: int = 24,
                       session: Session = Depends(get_session)):
    """Return drift counts bucketed into 5-minute intervals, oldest first.

    Query parameters:
      device — only events for this device, e.g. /drifts/history?device=core-sw-01
      hours  — lookback window (default 24)
    """
    return get_drift_history(session, hours=hours, device=device)


@app.get("/drifts")
def list_drifts(device: str | None = None, limit: int = 100,
                session: Session = Depends(get_session)):
    """Return stored drift events as JSON, newest first.

    Query parameters (both optional):
      device — only events for this device, e.g. /drifts?device=core-sw-01
      limit  — max rows to return (default 100), e.g. /drifts?limit=10

    `session` is supplied by FastAPI via Depends(get_session) — the caller
    never passes it; the dependency opens and closes it around the request.
    """
    events = get_drifts(session, device=device, limit=limit)
    known = {i.fingerprint: i for i in list_known_issues(session)}
    return [
        {
            "id": e.id,
            "device": e.device,
            "object": e.object_ref,
            "field": e.field,
            "intent": e.intent,
            "reality": e.reality,
            "drift_kind": e.drift_kind,
            "severity": e.severity,
            "detected_at": e.detected_at.isoformat(),
            "causes": diagnose({
                "object": e.object_ref,
                "field": e.field,
                "drift_kind": e.drift_kind,
            }),
            "known_fix": _known_fix_dict(known.get(
                make_fingerprint({"object": e.object_ref, "field": e.field, "drift_kind": e.drift_kind})
            )),
        }
        for e in events
    ]


@app.post("/known-issues")
def create_known_issue(body: KnownIssueIn, session: Session = Depends(get_session)):
    """Record a cause and fix for a drift pattern identified by its fingerprint."""
    fp = make_fingerprint({"object": body.object, "field": body.field, "drift_kind": body.drift_kind})
    issue = save_known_issue(session, fp, body.cause, body.fix)
    session.commit()
    return {
        "id": issue.id,
        "fingerprint": issue.fingerprint,
        "cause": issue.cause,
        "fix": issue.fix,
        "created_at": issue.created_at.isoformat(),
        "confirmed_count": issue.confirmed_count,
    }


@app.get("/known-issues")
def get_all_known_issues(session: Session = Depends(get_session)):
    """Return all stored known issues, oldest first."""
    return [
        {
            "id": i.id,
            "fingerprint": i.fingerprint,
            "cause": i.cause,
            "fix": i.fix,
            "created_at": i.created_at.isoformat(),
            "confirmed_count": i.confirmed_count,
        }
        for i in list_known_issues(session)
    ]