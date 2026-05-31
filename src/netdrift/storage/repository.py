"""storage/repository.py — saving and querying drift events (v0.2 / v2.5).

The bridge between the diff engine's plain dicts and the database.

v2.5 additions:
- save_drifts stores the optional "platform" field from each drift record.
- save_known_issue accepts a remediation payload; confirmed_count removed.
- confirmed_count() derives the success count from remediation_events.
- get_known_issue_by_id, update_known_issue_remediation, set_auto_apply_enabled.
- save_remediation_event, get_remediation_events (append-only audit log).
"""

from datetime import datetime, timedelta, timezone

from netdrift.storage.models import DriftEvent, KnownIssue, RemediationEvent


def _parse_detected_at(value):
    """Convert the differ's ISO-8601 'Z' string into a real datetime."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def save_drifts(session, drifts):
    """Persist a list of drift-record dicts as DriftEvent rows.

    Returns the list of created DriftEvent objects. Does NOT commit.
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
            platform=record.get("platform"),  # v2.5: may be absent in old records
        )
        session.add(event)
        events.append(event)
    session.flush()
    return events


def get_drift_history(session, hours=24, device=None):
    """Return drift counts grouped into 5-minute buckets, oldest first."""
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
    """Return stored drift events, newest first."""
    query = session.query(DriftEvent).order_by(DriftEvent.detected_at.desc())
    if device is not None:
        query = query.filter(DriftEvent.device == device)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def get_drift_event(session, event_id):
    """Return a single DriftEvent by primary key, or None."""
    return session.query(DriftEvent).filter(DriftEvent.id == event_id).one_or_none()


# ---------------------------------------------------------------------------
# known_issues
# ---------------------------------------------------------------------------

def save_known_issue(session, fingerprint, cause, fix, remediation=None):
    """Insert a new KnownIssue row.

    Does NOT commit. Raises on duplicate fingerprint (unique constraint).
    remediation defaults to None (diagnosis-only, no executable fix).
    """
    issue = KnownIssue(
        fingerprint=fingerprint,
        cause=cause,
        fix=fix,
        created_at=datetime.now(tz=timezone.utc),
        remediation=remediation,
        auto_apply_enabled=False,
    )
    session.add(issue)
    session.flush()
    return issue


def get_known_issue(session, fingerprint):
    """Return the KnownIssue for this fingerprint, or None."""
    return (
        session.query(KnownIssue)
        .filter(KnownIssue.fingerprint == fingerprint)
        .one_or_none()
    )


def get_known_issue_by_id(session, issue_id):
    """Return a KnownIssue by primary key, or None."""
    return session.query(KnownIssue).filter(KnownIssue.id == issue_id).one_or_none()


def list_known_issues(session):
    """Return all KnownIssue rows, oldest first."""
    return session.query(KnownIssue).order_by(KnownIssue.created_at).all()


def update_known_issue_remediation(session, issue_id, remediation):
    """Set the remediation payload on an existing KnownIssue.

    Returns the updated row, or None if not found.
    """
    issue = get_known_issue_by_id(session, issue_id)
    if issue is None:
        return None
    issue.remediation = remediation
    session.flush()
    return issue


def set_auto_apply_enabled(session, issue_id, enabled):
    """Flip auto_apply_enabled on a KnownIssue.

    Caller is responsible for enforcing business rules (kind check, threshold,
    global kill-switch) before calling this. Returns the updated row or None.
    """
    issue = get_known_issue_by_id(session, issue_id)
    if issue is None:
        return None
    issue.auto_apply_enabled = enabled
    session.flush()
    return issue


# ---------------------------------------------------------------------------
# v2.5 — remediation_events
# ---------------------------------------------------------------------------

def confirmed_count(session, known_issue_id):
    """Count successful remediations for a known issue.

    Derived as COUNT(*) WHERE result = 'success'. Never stored as a mutable
    field — an append-only log cannot be manipulated to bypass the confirm-N gate.
    """
    return (
        session.query(RemediationEvent)
        .filter(
            RemediationEvent.known_issue_id == known_issue_id,
            RemediationEvent.result == "success",
        )
        .count()
    )


def save_remediation_event(
    session,
    known_issue_id,
    platform,
    rendered_commands,
    dry_run_diff,
    result,
    applied_by,
    drift_event_id=None,
):
    """Insert a RemediationEvent row (append-only audit log).

    Returns the created row with its database-assigned id. Does NOT commit.
    result must be one of: "success", "failure", "dry_run_only".
    """
    event = RemediationEvent(
        known_issue_id=known_issue_id,
        drift_event_id=drift_event_id,
        platform=platform,
        rendered_commands=rendered_commands,
        dry_run_diff=dry_run_diff,
        result=result,
        applied_by=applied_by,
        applied_at=datetime.now(tz=timezone.utc),
    )
    session.add(event)
    session.flush()
    return event


def get_remediation_events(session, known_issue_id):
    """Return all RemediationEvents for a known issue, newest first."""
    return (
        session.query(RemediationEvent)
        .filter(RemediationEvent.known_issue_id == known_issue_id)
        .order_by(RemediationEvent.applied_at.desc())
        .all()
    )
