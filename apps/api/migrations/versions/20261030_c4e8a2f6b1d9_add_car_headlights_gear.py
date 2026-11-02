"""add car headlights and gear

Revision ID: c4e8a2f6b1d9
Revises: a7d3f1c8e2b6
Create Date: 2026-10-30 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a2f6b1d9"
down_revision: str | Sequence[str] | None = "a7d3f1c8e2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill: stored snapshots did not tell these, and "unknown" must not pass the checks.
    op.add_column("cars", sa.Column("headlights_on", sa.Boolean(), nullable=True))
    op.add_column("cars", sa.Column("gear", sa.String(length=1), nullable=True))
    op.create_check_constraint(op.f("ck_cars_gear_known"), "cars", "gear IN ('P', 'R', 'N', 'D')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_cars_gear_known"), "cars", type_="check")
    op.drop_column("cars", "gear")
    op.drop_column("cars", "headlights_on")
