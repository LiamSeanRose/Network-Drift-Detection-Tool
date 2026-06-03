"""alert_rules.object_type — per-object-type SLA windows (v4.5)

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-06-03 03:00:00.000000

Changes:
- Add a nullable object_type column to alert_rules. NULL keeps the existing
  behaviour (the rule applies to drift of any object type); a value (e.g.
  "interface", "vlan", "bgp_neighbor") scopes the SLA window to one object type,
  so interfaces and VLANs can carry different SLAs on the same device.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'alert_rules',
        sa.Column('object_type', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('alert_rules', 'object_type')
