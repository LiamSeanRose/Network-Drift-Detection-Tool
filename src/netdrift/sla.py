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
    get_drifts_older_than,
    is_acknowledged,
    list_alert_rules,
)

logger = logging.getLogger("netdrift.sla")


def evaluate_sla(session, dispatcher, *, now=None):
    """Evaluate enabled alert rules and dispatch one webhook per breach.

    Args:
        session: a database session.
        dispatcher: anything with ``fire(event_type, payload)`` (the process
            WebhookDispatcher in production; a fake in tests).
        now: evaluation instant (injectable). Defaults to UTC now.

    Returns the list of breach payloads dispatched (for logging and tests).
    A (device, fingerprint) breaches at most once per call, even though the same
    logical drift is re-persisted as a new row every poll cycle.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    breaches: list[dict] = []
    seen: set[tuple[str, str]] = set()

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
            key = (event.device, fp)
            if key in seen:
                continue
            if is_acknowledged(session, event.device, fp, now=now):
                continue
            seen.add(key)
            payload = {
                "device": event.device,
                "timestamp": now.isoformat(),
                "detail": (
                    f"SLA breach: {event.severity} drift on {event.object_ref} "
                    f"({event.field}) unresolved for over {rule.window_minutes} minutes"
                ),
            }
            dispatcher.fire("sla_breached", payload)
            logger.info(
                "SLA breach: device=%r fingerprint=%r window=%dm",
                event.device, fp, rule.window_minutes,
            )
            breaches.append({"fingerprint": fp, **payload})

    return breaches
