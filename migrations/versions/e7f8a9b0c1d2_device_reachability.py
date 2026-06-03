"""device_settings reachability — lightweight liveness probe state

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-06-03 00:00:00.000000

Changes:
- device_settings: add reachable (nullable bool), reachability_checked_at, and
  last_reachable_at (both nullable timestamps). The reachability probe
  (reachability.py) stamps them on each cadence; the /devices endpoint reads
  them so the dashboard can show reachable / unreachable / last-seen without a
  full drift poll. All nullable with no default — existing rows are "never
  probed" until the first probe runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, Sequence[str], None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'device_settings',
        sa.Column('reachable', sa.Boolean(), nullable=True),
    )
    op.add_column(
        'device_settings',
        sa.Column('reachability_checked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'device_settings',
        sa.Column('last_reachable_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('device_settings', 'last_reachable_at')
    op.drop_column('device_settings', 'reachability_checked_at')
    op.drop_column('device_settings', 'reachable')
