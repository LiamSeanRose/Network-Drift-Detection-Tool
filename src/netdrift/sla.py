"""sla.py — per-device drift SLA evaluation (v3.5 Feature 4; v4.5 edge-triggered).

evaluate_sla() runs once per scheduler cycle. For each enabled alert rule it
finds drift of the rule's severity (on the rule's device, or any device when the
rule's device is null) older than the rule's window.

Alerting is **edge-triggered** (v4.5): a breaching ``(device, fingerprint)`` fires
exactly one ``sla_breached`` webhook the cycle it starts breaching, then stays
silent while it persists, then fires exactly one ``sla_resolved`` the cycle the
underlying drift clears. The set of already-alerted breaches lives in the
``sla_breach_state`` table so the edge survives across cycles and process
restarts. Before v4.5 a persisting breach refired every cycle — twelve pages an
hour at a 5-minute poll.

Suppression (acknowledged drift, an active maintenance window, an unreachable
device) **mutes** alerting without fabricating a resolution or dropping the
tracked state: a muted breach fires neither ``sla_breached`` nor ``sla_resolved``
and its row is left intact, so when the mute lifts the breach is not re-alerted as
if new.

Kept out of scheduler.py — like auto_apply.py — so it stays a pure, unit-testable
function. The only timing input is the injected ``now``; there is no sleep and no
wall-clock read in the hot path, so tests drive it with manufactured timestamps
(the CI-flake lesson from the syslog cooldown).
"""

import logging
from datetime import datetime, timedelta, timezone

from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.storage.repository import (
    active_maintenance_windows,
    clear_sla_breach,
    device_last_collected,
    get_drifts_older_than,
    is_acknowledged,
    list_alert_rules,
    record_sla_breach,
    sla_breach_severity_map,
)

logger = logging.getLogger("netdrift.sla")


def _is_unreachable(session, device, stale_before):
    """True if the device has had no successful collection since ``stale_before``
    (or never). A null last_collected_at means it has never reported in."""
    last = device_last_collected(session, device)
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last < stale_before


def evaluate_sla(session, dispatcher, *, now=None, unreachable_after_minutes=None):
    """Evaluate enabled alert rules; dispatch one webhook per breach *edge*.

    Args:
        session: a database session.
        dispatcher: anything with ``fire(event_type, payload)`` (the process
            WebhookDispatcher in production; a fake in tests).
        now: evaluation instant (injectable). Defaults to UTC now.
        unreachable_after_minutes: if set, a device with no successful collection
            in this many minutes is treated as unreachable — its breaches fire a
            single ``device_unreachable`` alert instead of ``sla_breached``, since
            a breach computed from stale drift would be a false positive. None
            (the default) skips the reachability check entirely.

    Returns the list of alert payloads dispatched this cycle (for logging and
    tests): the newly-started breaches, the newly-resolved breaches, and any
    unreachable-device alerts. A persisting breach contributes nothing — that is
    the point. Writes breach-state rows; the caller commits.

    Breach and resolve payloads carry a structured ``severity`` (plus
    ``fingerprint``, ``object``, ``field``, ``window_minutes``) so a receiver can
    route by tier — critical to PagerDuty, warning to Slack — instead of parsing
    the human-readable ``detail`` string.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    stale_before = (
        now - timedelta(minutes=unreachable_after_minutes)
        if unreachable_after_minutes is not None else None
    )

    # A planned maintenance window mutes all alerting on its device (or every
    # device, for a global window). Computed once here, not per event.
    windows = active_maintenance_windows(session, now=now)
    maintenance_global = any(w.device is None for w in windows)
    maintenance_devices = {w.device for w in windows if w.device is not None}

    def in_maintenance(device):
        return maintenance_global or device in maintenance_devices

    dispatched: list[dict] = []
    unreachable_fired: set[str] = set()
    unreachable_devices: set[str] = set()
    # All (device, fp) breaching the window this cycle, regardless of muting —
    # so a muted-but-present breach is never mistaken for resolved.
    breaching: set[tuple[str, str]] = set()
    # The subset eligible to fire/track: breaching and not muted. Maps to the
    # payload detail captured at first sighting.
    firable: dict[tuple[str, str], dict] = {}

    for rule in list_alert_rules(session):
        if not rule.enabled:
            continue
        cutoff = now - timedelta(minutes=rule.window_minutes)
        events = get_drifts_older_than(
            session, severity=rule.severity, older_than=cutoff, device=rule.device
        )
        for event in events:
            # Per-object-type scoping: a rule with object_type set only watches
            # drift of that type (interface vs vlan vs bgp_neighbor …). The type
            # is the prefix of the object ref ("interface:Ethernet1" -> interface;
            # "config"/"device" have no prefix and match as-is).
            if rule.object_type and event.object_ref.split(":")[0] != rule.object_type:
                continue
            fp = make_fingerprint({
                "object": event.object_ref,
                "field": event.field,
                "drift_kind": event.drift_kind,
            })
            device = event.device
            key = (device, fp)
            breaching.add(key)

            # A breach on a device the collector can't reach is stale — alert on
            # the unreachability instead, once per device, and mute the breach.
            if stale_before is not None and _is_unreachable(session, device, stale_before):
                unreachable_devices.add(device)
                if device not in unreachable_fired:
                    unreachable_fired.add(device)
                    payload = {
                        "device": device,
                        "timestamp": now.isoformat(),
                        "detail": (
                            f"Device {device} has had no successful collection "
                            "in the last 2 poll cycles"
                        ),
                    }
                    dispatcher.fire("device_unreachable", payload)
                    logger.warning("Device unreachable: device=%r", device)
                    dispatched.append({"fingerprint": None, **payload})
                continue

            if in_maintenance(device) or is_acknowledged(session, device, fp, now=now):
                continue

            # Structured payload so a receiver can route on severity (escalation
            # tiers) without parsing the prose detail string.
            firable.setdefault(key, {
                "device": device,
                "fingerprint": fp,
                "severity": event.severity,
                "object": event.object_ref,
                "field": event.field,
                "window_minutes": rule.window_minutes,
                "timestamp": now.isoformat(),
                "detail": (
                    f"SLA breach: {event.severity} drift on {event.object_ref} "
                    f"({event.field}) unresolved for over {rule.window_minutes} minutes"
                ),
            })

    prev_active = sla_breach_severity_map(session)

    # Rising edge: a firable breach we were not already tracking.
    for key, payload in firable.items():
        if key in prev_active:
            continue
        device, fp = key
        dispatcher.fire("sla_breached", payload)
        record_sla_breach(session, device, fp, now, severity=payload["severity"])
        logger.info(
            "SLA breach (new): device=%r fingerprint=%r severity=%r",
            device, fp, payload["severity"],
        )
        dispatched.append(dict(payload))

    # Falling edge: a tracked breach whose drift is gone this cycle and is not
    # merely muted (an acked / maintenance / unreachable breach stays tracked).
    for key in prev_active.keys() - breaching:
        device, fp = key
        if in_maintenance(device) or device in unreachable_devices:
            continue
        if is_acknowledged(session, device, fp, now=now):
            continue
        severity = prev_active[key]
        payload = {
            "device": device,
            "fingerprint": fp,
            "severity": severity,
            "timestamp": now.isoformat(),
            "detail": f"SLA resolved: {severity or 'drift'} on {device} ({fp}) cleared",
        }
        dispatcher.fire("sla_resolved", payload)
        clear_sla_breach(session, device, fp)
        logger.info(
            "SLA resolved: device=%r fingerprint=%r severity=%r", device, fp, severity
        )
        dispatched.append({"resolved": True, **payload})

    return dispatched
