"""storage/models.py — SQLAlchemy models for the drift database (v0.2 / v2.5).

Defines the tables as Python classes. The diff engine (differ.py) produces
drift records as plain dicts (schema.md Section 5); this module is where
one of those dicts becomes a persistent database row.

v2.5 additions:
- DriftEvent: platform column (nullable for backward compat with existing rows)
- KnownIssue: remediation JSON, auto_apply_enabled bool; confirmed_count removed
  (now derived from RemediationEvent as COUNT(*) WHERE result='success')
- RemediationEvent: append-only audit log for every dry-run and apply attempt
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class all models inherit from."""


class DriftEvent(Base):
    """One persisted drift record — one row in the drift_events table."""

    __tablename__ = "drift_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device: Mapped[str] = mapped_column(String)
    object_ref: Mapped[str] = mapped_column("object", String)
    field: Mapped[str] = mapped_column(String)
    drift_kind: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    intent: Mapped[object] = mapped_column(JSON, nullable=True)
    reality: Mapped[object] = mapped_column(JSON, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # v2.5: platform stored so remediate endpoints can dispatch without calling NetBox.
    # Nullable for backward compat — rows created before v2.5 have no platform.
    platform: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self):
        return (
            f"<DriftEvent id={self.id} device={self.device!r} "
            f"object={self.object_ref!r} field={self.field!r} "
            f"severity={self.severity!r}>"
        )


class KnownIssue(Base):
    """A stored known-issue record — one row per unique drift fingerprint.

    When an engineer records a cause and fix for a drift event, a row is
    inserted here keyed by the event's fingerprint. On subsequent polls, any
    drift event whose fingerprint matches a row here surfaces the stored fix
    automatically.

    v2.5: confirmed_count is no longer a stored column. It is derived as
    COUNT(*) WHERE known_issue_id = ? AND result = 'success' on the
    remediation_events table. An append-only log cannot be manipulated to
    bypass the confirm-N gate; a mutable counter can.
    """

    __tablename__ = "known_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, unique=True, index=True)
    cause: Mapped[str] = mapped_column(String)
    fix: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Discriminated union per schema.md §9: restore_intent | raw_snippet | null.
    # null means diagnosis-only (no executable fix recorded yet).
    remediation: Mapped[object] = mapped_column(JSON, nullable=True)
    # Per-issue opt-in auto-apply gate. Forbidden for raw_snippet/null kinds;
    # requires confirmed_count >= CONFIRM_THRESHOLD; respects global kill-switch.
    auto_apply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self):
        return f"<KnownIssue id={self.id} fingerprint={self.fingerprint!r}>"


class DeviceSetting(Base):
    """Per-device operational settings — one row per device, keyed by name.

    Backs the per-device auto-apply kill-switch (v3.0 Feature 3). The key,
    ``device_name``, is the device's name in devices.yml (the same value
    pipeline.run_drift_check passes as ``device["name"]``), not the NetBox slug
    — confirmed against auto_apply.run_auto_apply's is_device_paused_fn seam.

    Absence of a row means "not paused": the safe default. A row is created
    lazily the first time a device is paused.
    """

    __tablename__ = "device_settings"

    device_name: Mapped[str] = mapped_column(String, primary_key=True)
    auto_remediation_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self):
        return (
            f"<DeviceSetting device_name={self.device_name!r} "
            f"paused={self.auto_remediation_paused}>"
        )


class ApiKey(Base):
    """One API key — one row in the api_keys table (v3.5 Feature 5).

    Authenticates mutating endpoints via the ``X-API-Key`` header. Only the
    SHA-256 hash of the full key is stored (see auth.hash_api_key); the raw key
    is returned to the operator exactly once at creation and never persisted, so
    a database leak cannot yield usable keys.

    key_hint holds the first 8 characters of the random token (after the
    ``sk-netdrift-`` prefix) so operators can tell keys apart in listings
    without exposing anything usable. Revocation is a hard delete of the row.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    key_hint: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # null = never expires.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self):
        return f"<ApiKey id={self.id} name={self.name!r} hint={self.key_hint!r}>"


class AlertRule(Base):
    """One SLA alert rule — one row in the alert_rules table (v3.5 Feature 4).

    "If a drift of this severity stays unresolved longer than window_minutes,
    alert." ``device`` is nullable: null means the rule applies to every device;
    a value scopes it to one device. Evaluation runs each scheduler cycle and
    dispatches via the WebhookDispatcher; acknowledged drift is suppressed.
    """

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # null = applies to all devices.
    device: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(String)
    window_minutes: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self):
        return (
            f"<AlertRule id={self.id} device={self.device!r} "
            f"severity={self.severity!r} window_minutes={self.window_minutes}>"
        )


class Acknowledgement(Base):
    """One drift acknowledgement — "this drift is intentional, stop alerting".

    Keyed by (device, fingerprint) rather than a drift_event id: DriftEvent rows
    are immutable and recreated on every poll cycle, so an ack tied to a row id
    would be silently lost on the next poll. The fingerprint is the same v2.0
    signature (object_type|field|drift_kind) used for known-issue matching, so an
    ack persists across cycles and follows the drift pattern, not a single row.

    An ack is *active* when ``acknowledged_until`` is null (permanent) or in the
    future. Active acks suppress webhook dispatch, SLA evaluation, and auto-apply
    for the matching (device, fingerprint).
    """

    __tablename__ = "acknowledgements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device: Mapped[str] = mapped_column(String, index=True)
    fingerprint: Mapped[str] = mapped_column(String, index=True)
    # null = permanent (never expires).
    acknowledged_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self):
        return (
            f"<Acknowledgement id={self.id} device={self.device!r} "
            f"fingerprint={self.fingerprint!r} until={self.acknowledged_until}>"
        )


class RemediationEvent(Base):
    """One apply or dry-run attempt — one row in the remediation_events table.

    Append-only. Never updated or deleted. confirmed_count on KnownIssue is
    derived as COUNT(*) WHERE known_issue_id = ? AND result = 'success'.
    """

    __tablename__ = "remediation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    known_issue_id: Mapped[int] = mapped_column(Integer, ForeignKey("known_issues.id"))
    # null when the apply was triggered without a specific drift event reference
    drift_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("drift_events.id"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String)
    rendered_commands: Mapped[str] = mapped_column(String)
    dry_run_diff: Mapped[str] = mapped_column(String)
    # "success" | "failure" | "dry_run_only"
    result: Mapped[str] = mapped_column(String)
    # "user:<id>" | "scheduler" | "api"
    applied_by: Mapped[str] = mapped_column(String)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self):
        return (
            f"<RemediationEvent id={self.id} known_issue_id={self.known_issue_id} "
            f"result={self.result!r}>"
        )
