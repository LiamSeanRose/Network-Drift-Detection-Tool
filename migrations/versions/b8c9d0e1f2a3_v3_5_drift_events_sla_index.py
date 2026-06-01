"""v3.5 drift_events SLA index — composite index for SLA/history queries

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-01 00:00:00.000000

Changes:
- Add a composite index on drift_events(device, severity, detected_at). The SLA
  evaluator and the history/`?since=` queries filter by device and severity and
  range over detected_at; without this index those scans degrade as the table
  grows (retention bounds the table, this keeps the hot queries fast).
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_drift_events_device_severity_detected_at',
        'drift_events',
        ['device', 'severity', 'detected_at'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_drift_events_device_severity_detected_at', table_name='drift_events'
    )
