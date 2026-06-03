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

from sqlalchemy import func

from netdrift.auth import KEY_PREFIX, generate_api_key, hash_api_key
from netdrift.storage.models import (
    Acknowledgement,
    AlertRule,
    ApiKey,
    DeviceSetting,
    DriftEvent,
    DriftExplanation,
    KnownIssue,
    MaintenanceWindow,
    RemediationEvent,
    SlaBreachState,
)


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


def get_drifts(session, device=None, limit=None, since=None, offset=None):
    """Return stored drift events, newest first.

    since: optional datetime; only events with detected_at >= since are returned.
    offset/limit: simple pagination (offset applied before limit).
    """
    query = session.query(DriftEvent).order_by(DriftEvent.detected_at.desc())
    if device is not None:
        query = query.filter(DriftEvent.device == device)
    if since is not None:
        query = query.filter(DriftEvent.detected_at >= since)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def get_drifts_older_than(session, *, severity, older_than, device=None):
    """Return drift events of a severity whose detected_at is at or before
    ``older_than``, oldest first. device=None matches all devices. Used by the
    SLA evaluator to find breaches."""
    query = session.query(DriftEvent).filter(
        DriftEvent.severity == severity,
        DriftEvent.detected_at <= older_than,
    )
    if device is not None:
        query = query.filter(DriftEvent.device == device)
    return query.order_by(DriftEvent.detected_at).all()


def get_drift_event(session, event_id):
    """Return a single DriftEvent by primary key, or None."""
    return session.query(DriftEvent).filter(DriftEvent.id == event_id).one_or_none()


def delete_drifts_older_than(session, cutoff):
    """Delete drift events with detected_at strictly before ``cutoff``.

    Returns the number of rows deleted. Does NOT commit. Backs the retention
    job that keeps the unbounded drift_events table from degrading history
    queries over time.
    """
    return (
        session.query(DriftEvent)
        .filter(DriftEvent.detected_at < cutoff)
        .delete(synchronize_session=False)
    )


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


def upsert_known_issue(session, fingerprint, cause, fix, remediation=None):
    """Insert a KnownIssue, or update the existing row with this fingerprint.

    Idempotent by fingerprint — the key the pattern importer upserts on. On
    insert, ``auto_apply_enabled`` is False (patterns are never auto-apply on
    arrival; schema.md §9 requires manual enablement after the confirm gate).
    On update, cause/fix/remediation are refreshed but ``auto_apply_enabled`` is
    preserved — re-importing a pattern must never silently disable an auto-apply
    an operator turned on.

    Does NOT commit. Returns ``(issue, created)`` where ``created`` is True on
    insert, False on update.
    """
    issue = get_known_issue(session, fingerprint)
    if issue is None:
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
        return issue, True

    issue.cause = cause
    issue.fix = fix
    issue.remediation = remediation
    session.flush()
    return issue, False


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


# --- Drift explanations (AI1) ------------------------------------------------

def upsert_explanation(session, fingerprint, explanation, source):
    """Insert or update the AI explanation for a drift fingerprint.

    One row per fingerprint (generate-once semantics live in the caller). Does
    NOT commit. Returns the row.
    """
    row = session.get(DriftExplanation, fingerprint)
    if row is None:
        row = DriftExplanation(
            fingerprint=fingerprint,
            explanation=explanation,
            source=source,
            generated_at=datetime.now(tz=timezone.utc),
        )
        session.add(row)
    else:
        row.explanation = explanation
        row.source = source
        row.generated_at = datetime.now(tz=timezone.utc)
    session.flush()
    return row


def create_maintenance_window(session, device, starts_at, ends_at, reason=None):
    """Insert a maintenance window. device=None means all devices. Does NOT commit."""
    window = MaintenanceWindow(
        device=device,
        starts_at=starts_at,
        ends_at=ends_at,
        reason=reason,
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(window)
    session.flush()
    return window


def list_maintenance_windows(session):
    """Return all maintenance windows, soonest start first."""
    return (
        session.query(MaintenanceWindow)
        .order_by(MaintenanceWindow.starts_at)
        .all()
    )


def delete_maintenance_window(session, window_id):
    """Delete a window by id. Returns the number of rows removed (0 or 1).
    Does NOT commit."""
    return (
        session.query(MaintenanceWindow)
        .filter(MaintenanceWindow.id == window_id)
        .delete()
    )


def active_maintenance_windows(session, now=None):
    """Return the windows active at ``now`` (starts_at <= now < ends_at)."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return (
        session.query(MaintenanceWindow)
        .filter(MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at > now)
        .all()
    )


def is_in_maintenance_window(session, device, now=None) -> bool:
    """True if ``device`` is inside an active window (or a global one).

    Mirrors ``is_acknowledged`` as a suppression check: consulted before SLA
    evaluation and auto-apply. A window with device=None matches every device.
    """
    for window in active_maintenance_windows(session, now=now):
        if window.device is None or window.device == device:
            return True
    return False


def active_sla_breaches(session) -> set[tuple[str, str]]:
    """Return the set of ``(device, fingerprint)`` breaches currently being tracked.

    These are the breaches that have already fired an ``sla_breached`` webhook and
    have not yet resolved — evaluate_sla uses this to stay silent on a persisting
    breach and to detect when one clears. Does NOT commit."""
    rows = session.query(SlaBreachState.device, SlaBreachState.fingerprint).all()
    return {(device, fingerprint) for device, fingerprint in rows}


def sla_breach_severity_map(session) -> dict[tuple[str, str], str | None]:
    """Return ``{(device, fingerprint): severity}`` for every tracked breach.

    evaluate_sla uses this to detect persisting/resolved breaches (the key set)
    and to tag the sla_resolved event with the severity stored when the breach
    first fired — the originating drift is gone by then, so its severity can only
    come from here. Does NOT commit."""
    rows = session.query(
        SlaBreachState.device, SlaBreachState.fingerprint, SlaBreachState.severity
    ).all()
    return {(device, fingerprint): severity for device, fingerprint, severity in rows}


def record_sla_breach(session, device, fingerprint, first_fired_at, severity=None):
    """Begin tracking a ``(device, fingerprint)`` breach. Idempotent — a second
    call for an already-tracked breach does not re-time it, but will backfill a
    missing severity. Does NOT commit."""
    existing = session.get(SlaBreachState, (device, fingerprint))
    if existing is not None:
        if existing.severity is None and severity is not None:
            existing.severity = severity
        return existing
    row = SlaBreachState(
        device=device, fingerprint=fingerprint,
        first_fired_at=first_fired_at, severity=severity,
    )
    session.add(row)
    session.flush()
    return row


def clear_sla_breach(session, device, fingerprint):
    """Stop tracking a breach (it resolved). Returns rows removed (0 or 1).
    Does NOT commit."""
    return (
        session.query(SlaBreachState)
        .filter(
            SlaBreachState.device == device,
            SlaBreachState.fingerprint == fingerprint,
        )
        .delete()
    )


def get_explanations(session, fingerprints=None):
    """Return ``{fingerprint: DriftExplanation}``.

    With ``fingerprints`` given, fetch only those in one query (the /drifts read
    path); ``None`` returns all. An empty iterable returns ``{}`` without a query.
    """
    query = session.query(DriftExplanation)
    if fingerprints is not None:
        fps = list(fingerprints)
        if not fps:
            return {}
        query = query.filter(DriftExplanation.fingerprint.in_(fps))
    return {r.fingerprint: r for r in query.all()}


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


def confirmed_counts(session, known_issue_ids):
    """Return ``{known_issue_id: success_count}`` for many issues in one query.

    Bulk form of confirmed_count, so a list endpoint (GET /drifts, GET
    /known-issues) reads every issue's confirmed count with a single GROUP BY
    instead of one COUNT(*) per issue (an N+1 over the largest table). Every
    requested id is present in the result, including those with zero successes.
    """
    if not known_issue_ids:
        return {}
    counts = {kid: 0 for kid in known_issue_ids}
    rows = (
        session.query(
            RemediationEvent.known_issue_id,
            func.count(RemediationEvent.id),
        )
        .filter(
            RemediationEvent.known_issue_id.in_(known_issue_ids),
            RemediationEvent.result == "success",
        )
        .group_by(RemediationEvent.known_issue_id)
        .all()
    )
    counts.update({kid: n for kid, n in rows})
    return counts


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


# ---------------------------------------------------------------------------
# v3.0 — device_settings (per-device auto-apply kill-switch)
# ---------------------------------------------------------------------------

def get_device_setting(session, device_name):
    """Return the DeviceSetting row for device_name, or None."""
    return (
        session.query(DeviceSetting)
        .filter(DeviceSetting.device_name == device_name)
        .one_or_none()
    )


def is_device_paused(session, device_name) -> bool:
    """True if auto-apply is paused for this device.

    Absence of a row means not paused (the safe default). Consulted by
    auto_apply.run_auto_apply before dispatching any apply. Note the argument
    order is (session, device_name) per this module's convention; the
    run_auto_apply seam expects (device_name, session), so the pipeline wiring
    adapts with a one-line lambda.
    """
    setting = get_device_setting(session, device_name)
    return bool(setting and setting.auto_remediation_paused)


def set_device_paused(session, device_name, paused, reason=None):
    """Pause or resume auto-apply for a device (upsert). Does NOT commit.

    Pausing stamps paused_at=now and stores the optional reason; resuming
    clears both. Returns the DeviceSetting row.
    """
    setting = get_device_setting(session, device_name)
    if setting is None:
        setting = DeviceSetting(device_name=device_name)
        session.add(setting)

    setting.auto_remediation_paused = paused
    if paused:
        setting.paused_at = datetime.now(tz=timezone.utc)
        setting.paused_reason = reason
    else:
        setting.paused_at = None
        setting.paused_reason = None

    session.flush()
    return setting


def record_collection(session, device_name, when=None):
    """Stamp a successful collection for a device (upsert). Does NOT commit.

    Creates the device_settings row if absent, preserving any existing pause
    state. ``when`` defaults to UTC now. Backs device_unreachable detection.
    """
    if when is None:
        when = datetime.now(tz=timezone.utc)
    setting = get_device_setting(session, device_name)
    if setting is None:
        setting = DeviceSetting(device_name=device_name)
        session.add(setting)
    setting.last_collected_at = when
    session.flush()
    return setting


def device_last_collected(session, device_name):
    """Return the device's last successful collection time, or None."""
    setting = get_device_setting(session, device_name)
    return setting.last_collected_at if setting else None


# ---------------------------------------------------------------------------
# v3.5 — api_keys (REST API authentication)
# ---------------------------------------------------------------------------

def create_api_key(session, name, expires_at=None):
    """Mint a new API key.

    Returns ``(raw_key, ApiKey)``. The raw key is returned to the caller exactly
    once — only its SHA-256 hash is persisted. key_hint stores the first 8 chars
    of the random token (after the prefix) so the key is distinguishable in
    listings without exposing anything usable. Does NOT commit.
    """
    raw_key = generate_api_key()
    token = raw_key[len(KEY_PREFIX):]
    key = ApiKey(
        key_hash=hash_api_key(raw_key),
        name=name,
        key_hint=token[:8],
        created_at=datetime.now(tz=timezone.utc),
        expires_at=expires_at,
    )
    session.add(key)
    session.flush()
    return raw_key, key


def verify_api_key(session, raw_key, now=None):
    """Return the ApiKey for a presented raw key, or None if invalid.

    Invalid means: no row matches the key's hash, or the key has expired
    (``expires_at`` non-null and in the past). On success, stamps
    ``last_used_at`` with ``now`` (injectable for tests; defaults to UTC now).
    Does NOT commit.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    key = (
        session.query(ApiKey)
        .filter(ApiKey.key_hash == hash_api_key(raw_key))
        .one_or_none()
    )
    if key is None:
        return None

    if key.expires_at is not None:
        expires_at = key.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            return None

    key.last_used_at = now
    session.flush()
    return key


def get_api_key_by_id(session, key_id):
    """Return an ApiKey by primary key, or None."""
    return session.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()


def list_api_keys(session):
    """Return all ApiKey rows, oldest first. Rows never carry the raw key."""
    return session.query(ApiKey).order_by(ApiKey.created_at).all()


def delete_api_key(session, key_id):
    """Revoke (hard-delete) an API key. Returns True if a row was removed.

    A revoked key fails verify_api_key on the very next request. Does NOT commit.
    """
    key = get_api_key_by_id(session, key_id)
    if key is None:
        return False
    session.delete(key)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# v3.5 — acknowledgements (suppress alerting on intentional drift)
# ---------------------------------------------------------------------------

def create_acknowledgement(session, device, fingerprint, acknowledged_until=None):
    """Record an acknowledgement for a (device, fingerprint).

    acknowledged_until=None means permanent. Does NOT commit. Caller is
    responsible for validating the expiry window (the API rejects past dates and
    windows longer than the configured maximum).
    """
    ack = Acknowledgement(
        device=device,
        fingerprint=fingerprint,
        acknowledged_until=acknowledged_until,
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(ack)
    session.flush()
    return ack


def is_acknowledged(session, device, fingerprint, now=None) -> bool:
    """True if an active acknowledgement exists for (device, fingerprint).

    Active means acknowledged_until is null (permanent) or strictly after ``now``
    (injectable for tests; defaults to UTC now). Consulted before webhook
    dispatch, SLA evaluation, and auto-apply.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    acks = (
        session.query(Acknowledgement)
        .filter(
            Acknowledgement.device == device,
            Acknowledgement.fingerprint == fingerprint,
        )
        .all()
    )
    for ack in acks:
        if ack.acknowledged_until is None:
            return True
        until = ack.acknowledged_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until > now:
            return True
    return False


def list_acknowledgements(session):
    """Return all Acknowledgement rows, newest first."""
    return (
        session.query(Acknowledgement)
        .order_by(Acknowledgement.created_at.desc())
        .all()
    )


def delete_acknowledgements_for(session, device, fingerprint) -> int:
    """Delete every acknowledgement for a (device, fingerprint). Returns the
    number removed. Backs the un-acknowledge (toggle-off) endpoint. No commit."""
    return (
        session.query(Acknowledgement)
        .filter(
            Acknowledgement.device == device,
            Acknowledgement.fingerprint == fingerprint,
        )
        .delete(synchronize_session=False)
    )


def delete_acknowledgement(session, ack_id) -> bool:
    """Remove an acknowledgement (un-acknowledge). Returns True if a row was
    removed. Does NOT commit."""
    ack = (
        session.query(Acknowledgement)
        .filter(Acknowledgement.id == ack_id)
        .one_or_none()
    )
    if ack is None:
        return False
    session.delete(ack)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# v3.5 — alert_rules (per-device drift SLA)
# ---------------------------------------------------------------------------

def create_alert_rule(session, device, severity, window_minutes, enabled=True):
    """Insert an AlertRule. device=None means "all devices". Does NOT commit.

    Caller validates severity and window_minutes (the API rejects bad values).
    """
    rule = AlertRule(
        device=device,
        severity=severity,
        window_minutes=window_minutes,
        enabled=enabled,
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(rule)
    session.flush()
    return rule


def list_alert_rules(session):
    """Return all AlertRule rows, oldest first."""
    return session.query(AlertRule).order_by(AlertRule.created_at).all()


def get_alert_rule(session, rule_id):
    """Return an AlertRule by primary key, or None."""
    return session.query(AlertRule).filter(AlertRule.id == rule_id).one_or_none()


def delete_alert_rule(session, rule_id) -> bool:
    """Delete an AlertRule. Returns True if a row was removed. Does NOT commit."""
    rule = get_alert_rule(session, rule_id)
    if rule is None:
        return False
    session.delete(rule)
    session.flush()
    return True
