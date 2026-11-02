"""add staff rental actions: adjustments, forced finish, trip ends, client block reason

Revision ID: a64e4c1ab129
Revises: a7d3f5c19e42
Create Date: 2026-11-02 15:00:00.000000
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "a64e4c1ab129"
down_revision: str | Sequence[str] | None = "a7d3f5c19e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _point() -> geoalchemy2.types.Geography:
    return geoalchemy2.types.Geography(
        geometry_type="POINT", srid=4326, spatial_index=False, from_text="ST_GeogFromText"
    )


def upgrade() -> None:
    op.add_column("rentals", sa.Column("start_location", _point(), nullable=True))
    op.add_column("rentals", sa.Column("end_location", _point(), nullable=True))
    op.add_column("rentals", sa.Column("force_finish_reason", sa.String(length=500), nullable=True))
    op.add_column("rentals", sa.Column("force_finished_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_rentals_force_finished_by_staff_members"),
        "rentals",
        "staff_members",
        ["force_finished_by"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_rentals_force_finish_has_author"),
        "rentals",
        "(force_finish_reason IS NULL) = (force_finished_by IS NULL)",
    )
    op.create_index("ix_rentals_reserved_at_id", "rentals", ["reserved_at", "id"])

    op.create_table(
        "rental_adjustments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_rental_adjustments_amount_not_zero")),
        sa.CheckConstraint("seq > 0", name=op.f("ck_rental_adjustments_seq_positive")),
        sa.ForeignKeyConstraint(
            ["rental_id"], ["rentals.id"], name=op.f("fk_rental_adjustments_rental_id_rentals")
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff_members.id"],
            name=op.f("fk_rental_adjustments_staff_id_staff_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rental_adjustments")),
        sa.UniqueConstraint("rental_id", "seq", name=op.f("uq_rental_adjustments_rental_id")),
        sa.UniqueConstraint(
            "rental_id",
            "idempotency_key",
            name=op.f("uq_rental_adjustments_rental_id_idempotency_key"),
        ),
    )
    # An adjustment is part of what the client was charged, like the receipt (docs/adr/0026).
    op.execute(
        """
        CREATE TRIGGER rental_adjustments_no_update_delete
            BEFORE UPDATE OR DELETE ON rental_adjustments
            FOR EACH ROW EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER rental_adjustments_no_truncate
            BEFORE TRUNCATE ON rental_adjustments
            FOR EACH STATEMENT EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )

    op.add_column("users", sa.Column("blocked_reason", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("blocked_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_users_blocked_by_staff_members"), "users", "staff_members", ["blocked_by"], ["id"]
    )
    # Nothing could block a client before, so a blocked row is one set by hand.
    op.execute(
        "UPDATE users SET blocked_reason = 'blocked before #112'"
        " WHERE blocked_at IS NOT NULL AND blocked_reason IS NULL"
    )
    op.create_check_constraint(
        op.f("ck_users_block_has_reason"),
        "users",
        "(blocked_at IS NULL) = (blocked_reason IS NULL)",
    )
    op.create_table(
        "client_block_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_client_block_events_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff_members.id"],
            name=op.f("fk_client_block_events_staff_id_staff_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_block_events")),
    )
    op.create_index(
        "ix_client_block_events_user_id_created_at",
        "client_block_events",
        ["user_id", "created_at"],
    )
    # The reasons behind the audit log's block events: as unchangeable as the log itself.
    # The receipts' function says only which table and which operation was refused.
    op.execute(
        """
        CREATE TRIGGER client_block_events_no_update_delete
            BEFORE UPDATE OR DELETE ON client_block_events
            FOR EACH ROW EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER client_block_events_no_truncate
            BEFORE TRUNCATE ON client_block_events
            FOR EACH STATEMENT EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )


def downgrade() -> None:
    op.drop_table("client_block_events")
    op.drop_constraint(op.f("ck_users_block_has_reason"), "users", type_="check")
    op.drop_constraint(op.f("fk_users_blocked_by_staff_members"), "users", type_="foreignkey")
    op.drop_column("users", "blocked_by")
    op.drop_column("users", "blocked_reason")
    # DROP TABLE is not blocked by the triggers and removes them with the table.
    op.drop_table("rental_adjustments")
    op.drop_index("ix_rentals_reserved_at_id", table_name="rentals")
    op.drop_constraint(op.f("ck_rentals_force_finish_has_author"), "rentals", type_="check")
    op.drop_constraint(
        op.f("fk_rentals_force_finished_by_staff_members"), "rentals", type_="foreignkey"
    )
    op.drop_column("rentals", "force_finished_by")
    op.drop_column("rentals", "force_finish_reason")
    op.drop_column("rentals", "end_location")
    op.drop_column("rentals", "start_location")
