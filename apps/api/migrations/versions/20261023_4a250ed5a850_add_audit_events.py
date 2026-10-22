"""add audit events

Revision ID: 4a250ed5a850
Revises: 4b85581d17a7
Create Date: 2026-10-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4a250ed5a850"
down_revision: str | Sequence[str] | None = "4b85581d17a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "(actor_type = 'system') = (actor_id IS NULL)",
            name=op.f("ck_audit_events_actor_id_matches_type"),
        ),
        sa.CheckConstraint(
            "(target_type IS NULL) = (target_id IS NULL)",
            name=op.f("ck_audit_events_target_complete"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_occurred_at_id", "audit_events", ["occurred_at", "id"], unique=False
    )
    op.create_index(
        "ix_audit_events_actor_id_occurred_at",
        "audit_events",
        ["actor_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_action_occurred_at",
        "audit_events",
        ["action", "occurred_at"],
        unique=False,
    )

    # Append-only is enforced by the database, not only by the code (docs/adr/0010): the
    # triggers fire for every role, the table owner included, and also for TRUNCATE, which
    # row triggers do not see.
    op.execute(
        """
        CREATE FUNCTION audit_events_reject_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_delete
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_truncate
            BEFORE TRUNCATE ON audit_events
            FOR EACH STATEMENT EXECUTE FUNCTION audit_events_reject_change()
        """
    )


def downgrade() -> None:
    # DROP TABLE is not blocked by the triggers and removes them with the table.
    op.drop_index("ix_audit_events_action_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred_at_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION audit_events_reject_change()")
