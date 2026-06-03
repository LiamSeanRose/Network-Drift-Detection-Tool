"""device_settings lifecycle — warranty / end-of-life dates

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-06-03 00:00:00.000000

Changes:
- device_settings: add warranty_expiry and end_of_life (nullable dates). The
  scheduler's lifecycle sync stamps them from NetBox custom fields; the /devices
  endpoint serves them and lifecycle.py classifies them. Nullable with no
  default — existing rows are "unknown" until the first sync.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('device_settings', sa.Column('warranty_expiry', sa.Date(), nullable=True))
    op.add_column('device_settings', sa.Column('end_of_life', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('device_settings', 'end_of_life')
    op.drop_column('device_settings', 'warranty_expiry')
