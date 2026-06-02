"""maintenance_windows — planned change windows that suppress alerting

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-02 00:00:00.000000

Changes:
- Add the maintenance_windows table. While a window is active
  (starts_at <= now < ends_at), drift on the matching device is suppressed from
  SLA-breach alerting and auto-apply — the same suppression points an
  acknowledgement uses. device is nullable: NULL means the window applies to
  every device (a global change freeze).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'maintenance_windows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device', sa.String(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        'ix_maintenance_windows_device', 'maintenance_windows', ['device']
    )


def downgrade() -> None:
    op.drop_index('ix_maintenance_windows_device', table_name='maintenance_windows')
    op.drop_table('maintenance_windows')
