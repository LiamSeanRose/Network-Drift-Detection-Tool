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
