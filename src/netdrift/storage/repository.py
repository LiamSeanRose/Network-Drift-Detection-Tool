"""storage/repository.py — saving and querying drift events (v0.2).

The bridge between the diff engine's plain dicts and the database. Two public
functions:

    save_drifts(session, drifts)  -> store a list of drift-record dicts
    get_drifts(session, ...)      -> read drift events back, newest first

Both take a Session (from storage.database.get_sessionmaker) so the caller
controls the transaction boundary and tests can pass a throwaway session.
"""

from datetime import datetime

from netdrift.storage.models import DriftEvent


def _parse_detected_at(value):
    """Convert the differ's ISO-8601 'Z' string into a real datetime.

    The differ emits e.g. "2026-05-20T14:32:00Z" (schema Rule 2). Python's
    fromisoformat handles the offset form "+00:00" but historically choked on
    a trailing "Z", so we swap Z -> +00:00 before parsing. The result is a
    timezone-aware datetime, which is what the detected_at column expects.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def save_drifts(session, drifts):
    """Persist a list of drift-record dicts as DriftEvent rows.

    Returns the list of created DriftEvent objects (now carrying their
    database-assigned ids). Does NOT commit — the caller owns the transaction,
    so several saves can be grouped, or rolled back together on error.
    """
    events = []
    for record in drifts:
        event = DriftEvent(
            device=record["device"],
            object_ref=record["object"],
            field=record["field"],
            intent=record["intent"],
            reality=record["reality"],
            drift_kind=record["drift_kind"],
            severity=record["severity"],
            detected_at=_parse_detected_at(record["detected_at"]),
        )
        session.add(event)
        events.append(event)
    session.flush()  # send INSERTs now so each event.id is populated
    return events


def get_drifts(session, device=None, limit=None):
    """Return stored drift events, newest first.

    Optional filters:
      device — only events for this device name.
      limit  — at most this many rows.
    """
    query = session.query(DriftEvent).order_by(DriftEvent.detected_at.desc())
    if device is not None:
        query = query.filter(DriftEvent.device == device)
    if limit is not None:
        query = query.limit(limit)
    return query.all()