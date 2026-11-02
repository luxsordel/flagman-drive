"""add support conversations and incidents, staff display names

Revision ID: d7e3a9c4b152
Revises: e5b7c2d9a4f1
Create Date: 2026-10-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e3a9c4b152"
down_revision: str | Sequence[str] | None = "e5b7c2d9a4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("staff_members", sa.Column("display_name", sa.String(length=100), nullable=True))
    op.create_table(
        "support_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("assigned_staff_id", sa.Uuid(), nullable=True),
        sa.Column("staff_unread", sa.Integer(), nullable=False),
        sa.Column("client_unread", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('closed', 'open')", name=op.f("ck_support_conversations_status_known")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_staff_id"],
            ["staff_members.id"],
            name=op.f("fk_support_conversations_assigned_staff_id_staff_members"),
        ),
        sa.ForeignKeyConstraint(
            ["rental_id"], ["rentals.id"], name=op.f("fk_support_conversations_rental_id_rentals")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_support_conversations_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_conversations")),
    )
    op.create_index(op.f("ix_support_conversations_user_id"), "support_conversations", ["user_id"])
    op.create_index(
        "uq_support_conversations_open_user",
        "support_conversations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_support_conversations_queue",
        "support_conversations",
        ["last_message_at"],
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "support_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.String(length=16), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=True),
        sa.Column("object_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "author IN ('client', 'staff')", name=op.f("ck_support_messages_author_known")
        ),
        sa.CheckConstraint(
            "kind IN ('photo', 'text')", name=op.f("ck_support_messages_kind_known")
        ),
        sa.CheckConstraint(
            "(kind = 'text') = (body IS NOT NULL) AND (kind = 'photo') = (object_key IS NOT NULL)",
            name=op.f("ck_support_messages_content_matches_kind"),
        ),
        sa.CheckConstraint(
            "(author = 'staff') = (staff_id IS NOT NULL)",
            name=op.f("ck_support_messages_staff_id_matches_author"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["support_conversations.id"],
            name=op.f("fk_support_messages_conversation_id_support_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff_members.id"],
            name=op.f("fk_support_messages_staff_id_staff_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_messages")),
        sa.UniqueConstraint("seq", name=op.f("uq_support_messages_seq")),
        sa.UniqueConstraint("object_key", name=op.f("uq_support_messages_object_key")),
    )
    op.create_index(
        op.f("ix_support_messages_conversation_id"), "support_messages", ["conversation_id"]
    )

    op.create_table(
        "support_incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=True),
        sa.Column("car_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("assigned_staff_id", sa.Uuid(), nullable=True),
        sa.Column("sent_to_service", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('accident', 'damage', 'malfunction', 'other')",
            name=op.f("ck_support_incidents_type_known"),
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'new', 'rejected', 'resolved')",
            name=op.f("ck_support_incidents_status_known"),
        ),
        sa.CheckConstraint(
            "(lat IS NULL) = (lon IS NULL)", name=op.f("ck_support_incidents_location_complete")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_staff_id"],
            ["staff_members.id"],
            name=op.f("fk_support_incidents_assigned_staff_id_staff_members"),
        ),
        sa.ForeignKeyConstraint(
            ["car_id"], ["cars.id"], name=op.f("fk_support_incidents_car_id_cars")
        ),
        sa.ForeignKeyConstraint(
            ["rental_id"], ["rentals.id"], name=op.f("fk_support_incidents_rental_id_rentals")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_support_incidents_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_incidents")),
    )
    op.create_index(op.f("ix_support_incidents_user_id"), "support_incidents", ["user_id"])
    op.create_index(
        "ix_support_incidents_queue",
        "support_incidents",
        ["priority", "created_at"],
        postgresql_where=sa.text("status IN ('new', 'in_progress')"),
    )

    op.create_table(
        "support_incident_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["support_incidents.id"],
            name=op.f("fk_support_incident_photos_incident_id_support_incidents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_incident_photos")),
        sa.UniqueConstraint("object_key", name=op.f("uq_support_incident_photos_object_key")),
    )
    op.create_index(
        op.f("ix_support_incident_photos_incident_id"), "support_incident_photos", ["incident_id"]
    )

    op.create_table(
        "support_incident_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('reject', 'resolve', 'sent_to_service', 'take')",
            name=op.f("ck_support_incident_events_action_known"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["support_incidents.id"],
            name=op.f("fk_support_incident_events_incident_id_support_incidents"),
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff_members.id"],
            name=op.f("fk_support_incident_events_staff_id_staff_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_incident_events")),
    )
    op.create_index(
        op.f("ix_support_incident_events_incident_id"), "support_incident_events", ["incident_id"]
    )


def downgrade() -> None:
    op.drop_table("support_incident_events")
    op.drop_table("support_incident_photos")
    op.drop_table("support_incidents")
    op.drop_table("support_messages")
    op.drop_table("support_conversations")
    op.drop_column("staff_members", "display_name")
