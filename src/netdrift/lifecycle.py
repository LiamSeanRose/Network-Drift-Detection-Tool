"""lifecycle.py — device lifecycle (warranty / end-of-life) status logic.

Pure date arithmetic, no I/O: given a warranty-expiry and an end-of-life date,
classify each as expired / expiring / ok and report days remaining. The dates
themselves come from NetBox custom fields (netbox_client.list_lifecycle), are
persisted by the scheduler's lifecycle sync, and are served read-only by the
API — so this module stays a testable pure function the API and tests share.

This is the asset-management capability Lighthouse had ("is this device still
under warranty / approaching end of life?") expressed against NetBox as the
record of truth.
"""

from datetime import date, datetime, timezone

# Default horizon for "expiring soon". A warranty/EoL date within this many days
# (and not already past) is flagged as expiring rather than ok.
DEFAULT_WITHIN_DAYS = 30


def _parse_date(value):
    """Coerce a NetBox custom-field value to a ``date``, or None.

    Accepts a date, a datetime, or an ISO-8601 string (NetBox date custom fields
    serialize as ``YYYY-MM-DD``). Anything unparseable becomes None — a missing
    or malformed lifecycle date is "unknown", never an error.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso(value):
    """Normalize a date-ish value to a ``YYYY-MM-DD`` string, or None."""
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else None


def _today(today=None):
    return today or datetime.now(tz=timezone.utc).date()


def days_until(value, *, today=None):
    """Whole days from today until ``value`` (negative if past), or None if the
    date is missing/unparseable."""
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return (parsed - _today(today)).days


def classify(value, *, within_days=DEFAULT_WITHIN_DAYS, today=None):
    """Classify a lifecycle date: 'expired', 'expiring', 'ok', or None (unknown)."""
    days = days_until(value, today=today)
    if days is None:
        return None
    if days < 0:
        return "expired"
    if days <= within_days:
        return "expiring"
    return "ok"


def lifecycle_summary(warranty_expiry, end_of_life, *,
                      within_days=DEFAULT_WITHIN_DAYS, today=None):
    """Return the lifecycle view for one device: each date normalized to an ISO
    string, its status, and days remaining. ``today`` is injectable for tests."""
    return {
        "warranty_expiry": _iso(warranty_expiry),
        "warranty_status": classify(warranty_expiry, within_days=within_days, today=today),
        "warranty_days_left": days_until(warranty_expiry, today=today),
        "end_of_life": _iso(end_of_life),
        "eol_status": classify(end_of_life, within_days=within_days, today=today),
        "eol_days_left": days_until(end_of_life, today=today),
    }
