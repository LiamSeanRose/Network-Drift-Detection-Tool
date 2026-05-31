"""create known_issues table

Revision ID: b8e1f3a9c2d0
Revises: 133d1490bda7
Create Date: 2026-05-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e1f3a9c2d0'
down_revision: Union[str, Sequence[str], None] = '133d1490bda7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'known_issues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fingerprint', sa.String(), nullable=False),
        sa.Column('cause', sa.String(), nullable=False),
        sa.Column('fix', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('confirmed_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fingerprint'),
    )
    op.create_index('ix_known_issues_fingerprint', 'known_issues', ['fingerprint'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_known_issues_fingerprint', table_name='known_issues')
    op.drop_table('known_issues')
