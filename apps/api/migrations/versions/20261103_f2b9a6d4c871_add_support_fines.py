"""add support fines

Revision ID: f2b9a6d4c871
Revises: c5d1e8f3a274
Create Date: 2026-11-03 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b9a6d4c871"
down_revision: str | Sequence[str] | None = "c5d1e8f3a274"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENTS = (
    "'payment_failed', 'reservation_expired', 'reservation_expiring', "
    "'support_reply', 'trip_finished', 'trip_low_battery'"
)


def _replace_event_check(events: str) -> None:
    op.drop_constraint(
        op.f("ck_notification_outbox_event_known"), "notification_outbox", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notification_outbox_event_known"), "notification_outbox", f"event IN ({events})"
    )


def upgrade() -> None:
    _replace_event_check(f"'fine_charged', {_EVENTS}")
    op.add_column("rentals", sa.Column("end_battery_pct", sa.SmallInteger(), nullable=True))
    op.create_table(
        "support_fines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("fine_type_id", sa.Uuid(), nullable=False),
        sa.Column("fine_type_version_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("charge_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_status", sa.String(length=16), nullable=True),
        sa.Column("refund_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('cancelled', 'paid', 'pending', 'unpaid')",
            name=op.f("ck_support_fines_status_known"),
        ),
        sa.CheckConstraint(
            "refund_status IS NULL OR refund_status IN ('failed', 'pending', 'succeeded')",
            name=op.f("ck_support_fines_refund_status_known"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_support_fines_amount_positive")),
        sa.CheckConstraint(
            "(created_by IS NULL) = (comment IS NULL)",
            name=op.f("ck_support_fines_manual_has_comment"),
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancel_reason IS NOT NULL AND cancelled_at IS NOT NULL)",
            name=op.f("ck_support_fines_cancelled_has_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["rental_id"], ["rentals.id"], name=op.f("fk_support_fines_rental_id_rentals")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_support_fines_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["fine_type_id"],
            ["fine_types.id"],
            name=op.f("fk_support_fines_fine_type_id_fine_types"),
        ),
        sa.ForeignKeyConstraint(
            ["fine_type_version_id"],
            ["fine_type_versions.id"],
            name=op.f("fk_support_fines_fine_type_version_id_fine_type_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["staff_members.id"],
            name=op.f("fk_support_fines_created_by_staff_members"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["staff_members.id"],
            name=op.f("fk_support_fines_cancelled_by_staff_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_fines")),
    )
    op.create_index(
        op.f("ix_support_fines_rental_id"), "support_fines", ["rental_id"], unique=False
    )
    op.create_index(op.f("ix_support_fines_user_id"), "support_fines", ["user_id"], unique=False)
    op.create_index(
        "uq_support_fines_automatic",
        "support_fines",
        ["rental_id", "fine_type_id"],
        unique=True,
        postgresql_where=sa.text("created_by IS NULL"),
    )
    op.create_index(
        "ix_support_fines_due",
        "support_fines",
        ["next_at"],
        unique=False,
        postgresql_where=sa.text("next_at IS NOT NULL"),
    )
    op.create_index(
        "ix_support_fines_unpaid_user",
        "support_fines",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("status = 'unpaid'"),
    )
    op.create_index(
        "ix_support_fines_created_at_id", "support_fines", ["created_at", "id"], unique=False
    )
    op.create_table(
        "support_fine_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fine_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["fine_id"],
            ["support_fines.id"],
            name=op.f("fk_support_fine_photos_fine_id_support_fines"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_fine_photos")),
        sa.UniqueConstraint("object_key", name=op.f("uq_support_fine_photos_object_key")),
    )
    op.create_index(
        op.f("ix_support_fine_photos_fine_id"), "support_fine_photos", ["fine_id"], unique=False
    )
    op.create_table(
        "support_fine_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("fine_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('cancelled', 'charged', 'declined', 'paid', 'refund_failed', 'refunded')",
            name=op.f("ck_support_fine_events_action_known"),
        ),
        sa.ForeignKeyConstraint(
            ["fine_id"],
            ["support_fines.id"],
            name=op.f("fk_support_fine_events_fine_id_support_fines"),
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff_members.id"],
            name=op.f("fk_support_fine_events_staff_id_staff_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_fine_events")),
        sa.UniqueConstraint("seq", name=op.f("uq_support_fine_events_seq")),
    )
    op.create_index(
        op.f("ix_support_fine_events_fine_id"), "support_fine_events", ["fine_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("support_fine_events")
    op.drop_table("support_fine_photos")
    op.drop_table("support_fines")
    op.drop_column("rentals", "end_battery_pct")
    op.execute("DELETE FROM notification_outbox WHERE event = 'fine_charged'")
    _replace_event_check(_EVENTS)
