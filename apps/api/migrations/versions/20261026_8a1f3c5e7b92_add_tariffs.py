"""add tariffs

Revision ID: 8a1f3c5e7b92
Revises: 5b8e2f6a9d14
Create Date: 2026-10-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a1f3c5e7b92"
down_revision: str | Sequence[str] | None = "5b8e2f6a9d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRICE_COLUMNS = (
    "driving_per_minute",
    "parked_per_minute",
    "waiting_per_minute",
    "free_waiting_minutes",
    "hold_amount",
)


def upgrade() -> None:
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_id"], ["car_models.id"], name=op.f("fk_tariffs_model_id_car_models")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariffs")),
        sa.UniqueConstraint("model_id", name=op.f("uq_tariffs_model_id")),
    )
    op.create_table(
        "tariff_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tariff_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        *(sa.Column(column, sa.Integer(), nullable=False) for column in _PRICE_COLUMNS),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("number > 0", name=op.f("ck_tariff_versions_number_positive")),
        *(
            sa.CheckConstraint(
                f"{column} >= 0", name=op.f(f"ck_tariff_versions_{column}_not_negative")
            )
            for column in _PRICE_COLUMNS
        ),
        sa.ForeignKeyConstraint(
            ["tariff_id"], ["tariffs.id"], name=op.f("fk_tariff_versions_tariff_id_tariffs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariff_versions")),
        sa.UniqueConstraint(
            "tariff_id", "number", name=op.f("uq_tariff_versions_tariff_id_number")
        ),
    )


def downgrade() -> None:
    op.drop_table("tariff_versions")
    op.drop_table("tariffs")
