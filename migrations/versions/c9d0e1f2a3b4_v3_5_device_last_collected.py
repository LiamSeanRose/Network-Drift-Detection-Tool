"""v3.5 device_settings.last_collected_at — collection-success tracking

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-01 00:00:00.000000

Changes:
- device_settings: add last_collected_at (nullable). The pipeline stamps it on
  every successful drift check; the SLA evaluator reads it to tag a device with
  no recent collection as device_unreachable instead of firing a stale SLA
  breach. Nullable with no default — existing rows are treated as "never
  collected" until the next successful poll.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'device_settings',
        sa.Column('last_collected_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('device_settings', 'last_collected_at')
