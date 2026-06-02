"""AI1 drift_explanations — per-fingerprint natural-language explanations

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-02 00:00:00.000000

Changes:
- Add the drift_explanations table: one row per unique drift fingerprint holding
  an opt-in AI-generated explanation (explain.py / AI1). Keyed by fingerprint —
  the same v2.0 key known_issues and acknowledgements use — so an explanation is
  stored once per structural drift, not per drift_events row. The table is empty
  unless an LLM provider is configured; the API falls back to diagnose() causes
  when a fingerprint has no row.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'drift_explanations',
        sa.Column('fingerprint', sa.String(), primary_key=True),
        sa.Column('explanation', sa.String(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('drift_explanations')
