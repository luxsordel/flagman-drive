"""add rentals and inspections

Revision ID: b4c8e2a61f37
Revises: 9a1f3c5e7b20
Create Date: 2026-10-26 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c8e2a61f37"
down_revision: str | Sequence[str] | None = "9a1f3c5e7b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE = "status IN ('in_trip', 'parked', 'reserved')"


def upgrade() -> None:
    op.create_table(
        "rentals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("car_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("tariff_version_id", sa.Uuid(), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("free_wait_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trip_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=16), nullable=True),
        sa.Column("command_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('cancelled', 'finished', 'in_trip', 'paid', 'parked', "
            "'payment_failed', 'reserved')",
            name=op.f("ck_rentals_status_known"),
        ),
        sa.CheckConstraint(
            "cancel_reason IS NULL OR cancel_reason IN ('client', 'expired')",
            name=op.f("ck_rentals_cancel_reason_known"),
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancel_reason IS NOT NULL)",
            name=op.f("ck_rentals_cancel_reason_matches_status"),
        ),
        sa.CheckConstraint(
            "reserved_at <= free_wait_until AND free_wait_until <= expires_at",
            name=op.f("ck_rentals_reservation_deadlines_ordered"),
        ),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], name=op.f("fk_rentals_car_id_cars")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_rentals_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rentals")),
    )
    op.create_index(op.f("ix_rentals_car_id"), "rentals", ["car_id"])
    op.create_index(op.f("ix_rentals_user_id"), "rentals", ["user_id"])
    op.create_index(
        "uq_rentals_active_user",
        "rentals",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
    )
    op.create_index(
        "uq_rentals_active_car",
        "rentals",
        ["car_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
    )
    op.create_index(
        "ix_rentals_reserved_expires_at",
        "rentals",
        ["expires_at"],
        postgresql_where=sa.text("status = 'reserved'"),
    )

    op.create_table(
        "rental_inspections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('after', 'before')", name=op.f("ck_rental_inspections_stage_known")
        ),
        sa.ForeignKeyConstraint(
            ["rental_id"], ["rentals.id"], name=op.f("fk_rental_inspections_rental_id_rentals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rental_inspections")),
        sa.UniqueConstraint("rental_id", "stage", name=op.f("uq_rental_inspections_rental_id")),
    )

    op.create_table(
        "rental_inspection_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["inspection_id"],
            ["rental_inspections.id"],
            name=op.f("fk_rental_inspection_photos_inspection_id_rental_inspections"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rental_inspection_photos")),
        sa.UniqueConstraint("object_key", name=op.f("uq_rental_inspection_photos_object_key")),
    )
    op.create_index(
        op.f("ix_rental_inspection_photos_inspection_id"),
        "rental_inspection_photos",
        ["inspection_id"],
    )

    op.create_table(
        "rental_inspection_damages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "side IN ('front', 'interior', 'left', 'rear', 'right', 'roof')",
            name=op.f("ck_rental_inspection_damages_side_known"),
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id"],
            ["rental_inspections.id"],
            name=op.f("fk_rental_inspection_damages_inspection_id_rental_inspections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rental_inspection_damages")),
    )
    op.create_index(
        op.f("ix_rental_inspection_damages_inspection_id"),
        "rental_inspection_damages",
        ["inspection_id"],
    )


def downgrade() -> None:
    op.drop_table("rental_inspection_damages")
    op.drop_table("rental_inspection_photos")
    op.drop_table("rental_inspections")
    op.drop_table("rentals")
