"""facility catalog fields: min_spacing_km, existing_facilities_considered

Revision ID: 0002_facility_catalog_fields
Revises: 0001_initial_schema
Create Date: 2026-08-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_facility_catalog_fields"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both columns are nullable with no server_default: additive, backward
    # compatible with existing rows (which simply read back as NULL — no
    # spacing constraint metadata was recorded for facilities created before
    # the facility-catalog merge).
    op.add_column("facilities", sa.Column("min_spacing_km", sa.Float(), nullable=True))
    op.add_column("facilities", sa.Column("existing_facilities_considered", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("facilities", "existing_facilities_considered")
    op.drop_column("facilities", "min_spacing_km")
