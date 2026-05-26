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
from sqlalchemy.orm import Session

from netdrift.storage.database import get_sessionmaker
from netdrift.storage.repository import get_drifts

app = FastAPI(title="netdrift API", version="0.2.0")

# One sessionmaker for the whole app (it builds its own engine from
# DATABASE_URL). Created once at import time, reused for every request.
_SessionLocal = get_sessionmaker()


def get_session():
    """FastAPI dependency: yield one database session per request.

    FastAPI calls this for any endpoint that declares it (see /drifts). The
    code before `yield` is setup (open a session); the code after runs as
    cleanup once the response is sent (close it). This guarantees every
    request gets a fresh session and no session is ever left open.
    """
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


@app.get("/health")
def health():
    """Liveness check — proves the API is up. No database involved."""
    return {"status": "ok"}


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
    # Translate each ORM object into a plain dict FastAPI serializes to JSON.
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
        }
        for e in events
    ]