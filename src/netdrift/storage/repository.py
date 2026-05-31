"""storage/repository.py — saving and querying drift events (v0.2).

The bridge between the diff engine's plain dicts and the database. Two public
functions:

    save_drifts(session, drifts)  -> store a list of drift-record dicts
    get_drifts(session, ...)      -> read drift events back, newest first

Both take a Session (from storage.database.get_sessionmaker) so the caller
controls the transaction boundary and tests can pass a throwaway session.
"""

from datetime import datetime, timedelta, timezone

from netdrift.storage.models import DriftEvent, KnownIssue


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


def get_drift_history(session, hours=24, device=None):
    """Return drift counts grouped into 5-minute buckets, oldest first.

    Each entry is a dict:
        {"detected_at": ISO str, "device": str, "count": int,
         "critical": int, "warning": int, "info": int}

    Grouping is done in Python so the function works identically on SQLite
    (used in tests) and Postgres (production). The trade-off is that all raw
    events in the window are fetched first — acceptable for the lab scale and
    for history windows of up to a day or two.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    query = (
        session.query(DriftEvent)
        .filter(DriftEvent.detected_at >= cutoff)
        .order_by(DriftEvent.detected_at)
    )
    if device is not None:
        query = query.filter(DriftEvent.device == device)

    buckets = {}
    for e in query.all():
        dt = e.detected_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Round down to the nearest 5-minute boundary.
        bucket_dt = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
        key = (bucket_dt.isoformat(), e.device)
        if key not in buckets:
            buckets[key] = {
                "detected_at": bucket_dt.isoformat(),
                "device": e.device,
                "count": 0,
                "critical": 0,
                "warning": 0,
                "info": 0,
            }
        buckets[key]["count"] += 1
        if e.severity in buckets[key]:
            buckets[key][e.severity] += 1

    return sorted(buckets.values(), key=lambda x: (x["detected_at"], x["device"]))


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


def save_known_issue(session, fingerprint, cause, fix):
    """Insert a new KnownIssue row.

    Does NOT commit — the caller owns the transaction. Raises on duplicate
    fingerprint (the unique constraint enforces one record per pattern).
    """
    issue = KnownIssue(
        fingerprint=fingerprint,
        cause=cause,
        fix=fix,
        created_at=datetime.now(tz=timezone.utc),
        confirmed_count=1,
    )
    session.add(issue)
    session.flush()
    return issue


def get_known_issue(session, fingerprint):
    """Return the KnownIssue for this fingerprint, or None if not found."""
    return (
        session.query(KnownIssue)
        .filter(KnownIssue.fingerprint == fingerprint)
        .one_or_none()
    )


def list_known_issues(session):
    """Return all KnownIssue rows, oldest first."""
    return session.query(KnownIssue).order_by(KnownIssue.created_at).all()