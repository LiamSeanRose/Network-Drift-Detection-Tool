"""sla.py — per-device drift SLA evaluation (v3.5 Feature 4).

evaluate_sla() runs once per scheduler cycle. For each enabled alert rule it
finds drift of the rule's severity (on the rule's device, or any device when the
rule's device is null) older than the rule's window, and fires one
``sla_breached`` webhook per breaching (device, fingerprint). Acknowledged drift
is suppressed.

Kept out of scheduler.py — like auto_apply.py — so it stays a pure, unit-testable
function. The only timing input is the injected ``now``; there is no sleep and no
wall-clock read in the hot path, so tests drive it with manufactured timestamps
(the CI-flake lesson from the syslog cooldown).
"""

import logging
from datetime import datetime, timedelta, timezone

from netdrift.fingerprint import fingerprint as make_fingerprint
from netdrift.storage.repository import (
    device_last_collected,
    get_drifts_older_than,
    is_acknowledged,
    list_alert_rules,
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
    """Evaluate enabled alert rules and dispatch one webhook per breach.

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

    Returns the list of alert payloads dispatched (for logging and tests).
    A (device, fingerprint) breaches at most once per call; an unreachable device
    alerts at most once per call.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    stale_before = (
        now - timedelta(minutes=unreachable_after_minutes)
        if unreachable_after_minutes is not None else None
    )

    breaches: list[dict] = []
    seen: set[tuple[str, str]] = set()
    unreachable_fired: set[str] = set()

    for rule in list_alert_rules(session):
        if not rule.enabled:
            continue
        cutoff = now - timedelta(minutes=rule.window_minutes)
        events = get_drifts_older_than(
            session, severity=rule.severity, older_than=cutoff, device=rule.device
        )
        for event in events:
            fp = make_fingerprint({
                "object": event.object_ref,
                "field": event.field,
                "drift_kind": event.drift_kind,
            })
            device = event.device
            if is_acknowledged(session, device, fp, now=now):
                continue

            # A breach on a device the collector can't reach is stale — alert on
            # the unreachability instead, once per device.
            if stale_before is not None and _is_unreachable(session, device, stale_before):
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
                    breaches.append({"fingerprint": None, **payload})
                continue

            key = (device, fp)
            if key in seen:
                continue
            seen.add(key)
            payload = {
                "device": device,
                "timestamp": now.isoformat(),
                "detail": (
                    f"SLA breach: {event.severity} drift on {event.object_ref} "
                    f"({event.field}) unresolved for over {rule.window_minutes} minutes"
                ),
            }
            dispatcher.fire("sla_breached", payload)
            logger.info(
                "SLA breach: device=%r fingerprint=%r window=%dm",
                device, fp, rule.window_minutes,
            )
            breaches.append({"fingerprint": fp, **payload})

    return breaches
