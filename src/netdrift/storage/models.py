"""storage/models.py — SQLAlchemy models for the drift database (v0.2).

Defines the `drift_events` table as a Python class. The diff engine
(differ.py) produces drift records as plain dicts (schema.md Section 5);
this module is where one of those dicts becomes a persistent database row.

The mapping is one column per drift-record field, plus an auto-assigned
`id` primary key the dict does not carry:

    drift record dict           ->  DriftEvent column
    --------------------------------------------------
    (none)                      ->  id            (auto)
    device                      ->  device
    object                      ->  object_ref    (renamed: see note)
    field                       ->  field
    intent                      ->  intent        (JSON: value type varies)
    reality                     ->  reality       (JSON: value type varies)
    drift_kind                  ->  drift_kind
    severity                    ->  severity
    detected_at (ISO str)       ->  detected_at   (real timestamp)
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class all models inherit from. SQLAlchemy collects every table
    defined on this Base so tools (and Alembic) can discover them."""


class DriftEvent(Base):
    """One persisted drift record — one row in the drift_events table."""

    __tablename__ = "drift_events"

    # Auto-incrementing primary key. The database assigns it; we never set it.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Plain string columns — direct copies of the drift-record fields.
    device: Mapped[str] = mapped_column(String)
    # `object` is a Python builtin, so the attribute is object_ref; the actual
    # database column is still named "object" to match the schema's field name.
    object_ref: Mapped[str] = mapped_column("object", String)
    field: Mapped[str] = mapped_column(String)
    drift_kind: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)

    # intent / reality hold values whose type varies by field (list, bool,
    # int, str, None). The generic JSON type stores each value preserving its
    # real JSON shape (a list stays a list, a bool stays a bool). SQLAlchemy
    # renders it as JSONB on Postgres and as JSON-text on SQLite, so the same
    # model runs against the production database and the in-memory test one.
    intent: Mapped[object] = mapped_column(JSON, nullable=True)
    reality: Mapped[object] = mapped_column(JSON, nullable=True)

    # A real timestamp, not text — so history queries can sort and filter by
    # time. The differ emits an ISO string; the storage layer converts it to a
    # datetime before this column ever sees it (see storage.save).
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

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
    """

    __tablename__ = "known_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Unique key: object_type|field|drift_kind — strips device and identifier.
    fingerprint: Mapped[str] = mapped_column(String, unique=True, index=True)
    cause: Mapped[str] = mapped_column(String)
    fix: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Incremented each time an engineer confirms this fix resolved the drift.
    confirmed_count: Mapped[int] = mapped_column(Integer, default=1)

    def __repr__(self):
        return f"<KnownIssue id={self.id} fingerprint={self.fingerprint!r}>"