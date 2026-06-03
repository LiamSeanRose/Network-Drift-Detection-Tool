"""sla_breach_state.severity — severity-aware SLA alert routing (v4.5)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-03 02:00:00.000000

Changes:
- Add a nullable severity column to sla_breach_state. The breach severity (the
  alert rule's severity) is recorded when a breach is first tracked so the
  sla_resolved event, which fires after the underlying drift is gone, can be
  routed to the same escalation tier as the sla_breached event that opened it.
  Nullable: rows written before this column existed simply have no severity.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sla_breach_state',
        sa.Column('severity', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('sla_breach_state', 'severity')
