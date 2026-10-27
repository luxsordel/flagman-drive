"""add notifications

Revision ID: e5b7c2d9a4f1
Revises: c4d9e2a1f6b3
Create Date: 2026-10-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5b7c2d9a4f1"
down_revision: str | Sequence[str] | None = "c4d9e2a1f6b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "platform IN ('ios', 'android')", name=op.f("ck_push_devices_platform_known")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_push_devices_session_id_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_push_devices_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_devices")),
        sa.UniqueConstraint("session_id", name=op.f("uq_push_devices_session_id")),
        sa.UniqueConstraint("token", name=op.f("uq_push_devices_token")),
    )
    op.create_index(op.f("ix_push_devices_user_id"), "push_devices", ["user_id"], unique=False)

    op.create_table(
        "notification_opt_outs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('reservation', 'trip', 'payment')",
            name=op.f("ck_notification_opt_outs_category_known"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_opt_outs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "category", name=op.f("pk_notification_opt_outs")),
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event IN ('reservation_expiring', 'reservation_expired', 'trip_low_battery', "
            "'trip_finished', 'payment_failed')",
            name=op.f("ck_notification_outbox_event_known"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'undelivered')",
            name=op.f("ck_notification_outbox_status_known"),
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_notification_outbox_attempts_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_outbox_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_outbox")),
        sa.UniqueConstraint("dedup_key", name=op.f("uq_notification_outbox_dedup_key")),
    )
    op.create_index(
        op.f("ix_notification_outbox_user_id"), "notification_outbox", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_notification_outbox_created_at"),
        "notification_outbox",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notification_outbox_due",
        "notification_outbox",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_outbox_due",
        table_name="notification_outbox",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_notification_outbox_created_at"), table_name="notification_outbox")
    op.drop_index(op.f("ix_notification_outbox_user_id"), table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_table("notification_opt_outs")
    op.drop_index(op.f("ix_push_devices_user_id"), table_name="push_devices")
    op.drop_table("push_devices")
