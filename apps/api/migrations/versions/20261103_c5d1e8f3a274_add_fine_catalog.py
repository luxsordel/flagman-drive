"""add fine catalog

Revision ID: c5d1e8f3a274
Revises: a64e4c1ab129
Create Date: 2026-11-03 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d1e8f3a274"
down_revision: str | Sequence[str] | None = "a64e4c1ab129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fine_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fine_types")),
        sa.UniqueConstraint("code", name=op.f("uq_fine_types_code")),
    )
    op.create_table(
        "fine_type_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fine_type_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name_ru", sa.String(length=100), nullable=False),
        sa.Column("name_en", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("number > 0", name=op.f("ck_fine_type_versions_number_positive")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_fine_type_versions_amount_positive")),
        sa.ForeignKeyConstraint(
            ["fine_type_id"],
            ["fine_types.id"],
            name=op.f("fk_fine_type_versions_fine_type_id_fine_types"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fine_type_versions")),
        sa.UniqueConstraint(
            "fine_type_id", "number", name=op.f("uq_fine_type_versions_fine_type_id_number")
        ),
    )


def downgrade() -> None:
    op.drop_table("fine_type_versions")
    op.drop_table("fine_types")
