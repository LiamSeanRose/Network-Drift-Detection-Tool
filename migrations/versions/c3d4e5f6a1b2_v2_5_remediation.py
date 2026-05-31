"""v2.5 remediation — known_issues remediation payload, remediation_events table

Revision ID: c3d4e5f6a1b2
Revises: b8e1f3a9c2d0
Create Date: 2026-05-31 00:00:00.000000

Changes:
- drift_events: add nullable `platform` column (backward-compat; new rows carry platform)
- known_issues: drop `confirmed_count` (now derived from remediation_events)
- known_issues: add `remediation` JSON (discriminated union per schema.md §9)
- known_issues: add `auto_apply_enabled` bool (default false)
- remediation_events: new append-only audit log table
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a1b2'
down_revision: Union[str, Sequence[str], None] = 'b8e1f3a9c2d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # drift_events: add platform for applier dispatch
    op.add_column('drift_events', sa.Column('platform', sa.String(), nullable=True))

    # known_issues: transition to derived confirmed_count
    op.drop_column('known_issues', 'confirmed_count')
    op.add_column('known_issues', sa.Column('remediation', sa.JSON(), nullable=True))
    op.add_column(
        'known_issues',
        sa.Column('auto_apply_enabled', sa.Boolean(), nullable=False, server_default='false'),
    )

    # remediation_events: append-only audit log
    op.create_table(
        'remediation_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('known_issue_id', sa.Integer(), nullable=False),
        sa.Column('drift_event_id', sa.Integer(), nullable=True),
        sa.Column('platform', sa.String(), nullable=False),
        sa.Column('rendered_commands', sa.String(), nullable=False),
        sa.Column('dry_run_diff', sa.String(), nullable=False),
        sa.Column('result', sa.String(), nullable=False),
        sa.Column('applied_by', sa.String(), nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['known_issue_id'], ['known_issues.id']),
        sa.ForeignKeyConstraint(['drift_event_id'], ['drift_events.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_remediation_events_known_issue_id',
        'remediation_events',
        ['known_issue_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_remediation_events_known_issue_id', table_name='remediation_events')
    op.drop_table('remediation_events')

    op.drop_column('known_issues', 'auto_apply_enabled')
    op.drop_column('known_issues', 'remediation')
    op.add_column(
        'known_issues',
        sa.Column('confirmed_count', sa.Integer(), nullable=False, server_default='1'),
    )

    op.drop_column('drift_events', 'platform')
