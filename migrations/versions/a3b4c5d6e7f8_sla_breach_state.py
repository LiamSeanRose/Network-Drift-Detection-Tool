"""sla_breach_state — edge-triggered SLA alerting (v4.5)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-03 00:00:00.000000

Changes:
- Add the sla_breach_state table. It records which (device, fingerprint) breaches
  have already fired an sla_breached webhook, so evaluate_sla alerts once when a
  breach starts and once (sla_resolved) when it clears — instead of refiring every
  scheduler cycle for as long as the drift persists. Composite primary key
  (device, fingerprint), mirroring the acknowledgement identity.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sla_breach_state',
        sa.Column('device', sa.String(), primary_key=True, nullable=False),
        sa.Column('fingerprint', sa.String(), primary_key=True, nullable=False),
        sa.Column('first_fired_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('sla_breach_state')
