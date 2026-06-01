"""v3.5 acknowledgements — suppress alerting on intentional drift

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-01 00:00:00.000000

Changes:
- acknowledgements: new table recording that a (device, fingerprint) drift is
  intentional. Keyed by (device, fingerprint) — not a drift_event id — because
  DriftEvent rows are recreated every poll cycle. acknowledged_until null means
  permanent. device and fingerprint are indexed for the suppression lookup that
  runs on every webhook/SLA/auto-apply decision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'acknowledgements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device', sa.String(), nullable=False),
        sa.Column('fingerprint', sa.String(), nullable=False),
        sa.Column('acknowledged_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_acknowledgements_device'), 'acknowledgements', ['device']
    )
    op.create_index(
        op.f('ix_acknowledgements_fingerprint'), 'acknowledgements', ['fingerprint']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_acknowledgements_fingerprint'), table_name='acknowledgements')
    op.drop_index(op.f('ix_acknowledgements_device'), table_name='acknowledgements')
    op.drop_table('acknowledgements')
