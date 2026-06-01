"""v3.0 device_settings — per-device auto-apply kill-switch

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a1b2
Create Date: 2026-05-31 00:00:00.000000

Changes:
- device_settings: new table backing the per-device auto-apply kill-switch.
  Keyed by device_name (the devices.yml name, not the NetBox slug). Absence of
  a row means "not paused".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'device_settings',
        sa.Column('device_name', sa.String(), nullable=False),
        sa.Column(
            'auto_remediation_paused', sa.Boolean(),
            nullable=False, server_default='false',
        ),
        sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paused_reason', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('device_name'),
    )


def downgrade() -> None:
    op.drop_table('device_settings')
