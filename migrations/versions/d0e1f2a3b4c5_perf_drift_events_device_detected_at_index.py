"""drift_events (device, detected_at) index — history/since query hot path

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-02 00:00:00.000000

Changes:
- Add a composite index on drift_events(device, detected_at). get_drifts and
  get_drift_history filter by device and range/sort over detected_at *without*
  severity, so they cannot use the leading-device prefix of the existing
  (device, severity, detected_at) SLA index efficiently — and the all-devices
  history query ranges over detected_at alone. This index serves both the
  device-scoped and the detected_at-range access patterns directly (PERF1).
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_drift_events_device_detected_at',
        'drift_events',
        ['device', 'detected_at'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_drift_events_device_detected_at', table_name='drift_events'
    )
