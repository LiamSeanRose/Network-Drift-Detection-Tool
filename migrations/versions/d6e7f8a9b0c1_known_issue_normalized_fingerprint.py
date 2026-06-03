"""known_issues.normalized_fingerprint — v5.0 fuzzy matching key

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-06-03 04:00:00.000000

Changes:
- Add a nullable normalized_fingerprint column to known_issues and backfill it
  from each row's exact fingerprint. This is the template-normalized key the
  fuzzy matcher compares against (FUZZY_MATCHING_ENABLED). The exact fingerprint
  column stays the primary match key, so the default (fuzzy off) path is
  unchanged. Reversible: downgrade drops the column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from netdrift.fingerprint import normalize_fingerprint


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'known_issues',
        sa.Column('normalized_fingerprint', sa.String(), nullable=True),
    )
    # Backfill from the existing exact fingerprint.
    known = sa.table(
        'known_issues',
        sa.column('id', sa.Integer),
        sa.column('fingerprint', sa.String),
        sa.column('normalized_fingerprint', sa.String),
    )
    conn = op.get_bind()
    for row_id, exact in conn.execute(sa.select(known.c.id, known.c.fingerprint)):
        conn.execute(
            known.update()
            .where(known.c.id == row_id)
            .values(normalized_fingerprint=normalize_fingerprint(exact))
        )


def downgrade() -> None:
    op.drop_column('known_issues', 'normalized_fingerprint')
