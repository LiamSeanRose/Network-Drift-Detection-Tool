"""v3.5 alert_rules — per-device drift SLA rules

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-01 00:00:00.000000

Changes:
- alert_rules: new table holding SLA rules evaluated each scheduler cycle.
  device is nullable (null = all devices); severity and window_minutes define
  the breach condition; enabled toggles the rule without deleting it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device', sa.String(), nullable=True),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('window_minutes', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('alert_rules')
