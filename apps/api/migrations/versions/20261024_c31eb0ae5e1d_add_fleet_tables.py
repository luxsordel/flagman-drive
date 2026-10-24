"""add fleet tables

Revision ID: c31eb0ae5e1d
Revises: c7d2e9f14a68
Create Date: 2026-10-24 12:00:00.000000
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "c31eb0ae5e1d"
down_revision: str | Sequence[str] | None = "c7d2e9f14a68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATE_COLUMNS = ("location", "battery_pct", "doors_locked", "ignition_on", "state_at")


def upgrade() -> None:
    op.create_table(
        "car_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("seats", sa.SmallInteger(), nullable=False),
        sa.Column("battery_wh", sa.Integer(), nullable=False),
        sa.Column("range_km", sa.Integer(), nullable=False),
        sa.Column("photo_url", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("seats > 0", name=op.f("ck_car_models_seats_positive")),
        sa.CheckConstraint("battery_wh > 0", name=op.f("ck_car_models_battery_wh_positive")),
        sa.CheckConstraint("range_km > 0", name=op.f("ck_car_models_range_km_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_car_models")),
        sa.UniqueConstraint("brand", "name", name="uq_car_models_brand_name"),
    )
    op.create_table(
        "cars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("plate", sa.String(length=16), nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("telematics_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "location",
            geoalchemy2.types.Geography(
                geometry_type="POINT", srid=4326, spatial_index=False, from_text="ST_GeogFromText"
            ),
            nullable=True,
        ),
        sa.Column("battery_pct", sa.SmallInteger(), nullable=True),
        sa.Column("doors_locked", sa.Boolean(), nullable=True),
        sa.Column("ignition_on", sa.Boolean(), nullable=True),
        sa.Column("state_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('available', 'reserved', 'in_rental', 'service', 'offline')",
            name=op.f("ck_cars_status_known"),
        ),
        sa.CheckConstraint("battery_pct BETWEEN 0 AND 100", name=op.f("ck_cars_battery_pct_range")),
        sa.CheckConstraint(
            "("
            + " AND ".join(f"{column} IS NULL" for column in _STATE_COLUMNS)
            + ") OR ("
            + " AND ".join(f"{column} IS NOT NULL" for column in _STATE_COLUMNS)
            + ")",
            name=op.f("ck_cars_state_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["car_models.id"], name=op.f("fk_cars_model_id_car_models")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cars")),
        sa.UniqueConstraint("plate", name=op.f("uq_cars_plate")),
        sa.UniqueConstraint("telematics_id", name=op.f("uq_cars_telematics_id")),
        sa.UniqueConstraint("vin", name=op.f("uq_cars_vin")),
    )
    op.create_index(op.f("ix_cars_model_id"), "cars", ["model_id"], unique=False)
    op.create_index("ix_cars_location", "cars", ["location"], postgresql_using="gist")


def downgrade() -> None:
    op.drop_index("ix_cars_location", table_name="cars", postgresql_using="gist")
    op.drop_index(op.f("ix_cars_model_id"), table_name="cars")
    op.drop_table("cars")
    op.drop_table("car_models")
