"""add zones

Revision ID: 5b8e2f6a9d14
Revises: c31eb0ae5e1d
Create Date: 2026-10-25 12:00:00.000000
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "5b8e2f6a9d14"
down_revision: str | Sequence[str] | None = "c31eb0ae5e1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "area",
            geoalchemy2.types.Geography(
                geometry_type="POLYGON", srid=4326, spatial_index=False, from_text="ST_GeogFromText"
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('allowed', 'forbidden')", name=op.f("ck_zones_kind_known")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zones")),
    )
    op.create_index("ix_zones_area", "zones", ["area"], unique=False, postgresql_using="gist")


def downgrade() -> None:
    op.drop_index("ix_zones_area", table_name="zones", postgresql_using="gist")
    op.drop_table("zones")
