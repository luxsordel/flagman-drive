"""add rental trips, settlement and receipts

Revision ID: d7a3f9c1e2b8
Revises: e5b7c2d9a4f1
Create Date: 2026-10-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3f9c1e2b8"
down_revision: str | Sequence[str] | None = "e5b7c2d9a4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rentals", sa.Column("hold_id", sa.Uuid(), nullable=True))
    op.add_column("rentals", sa.Column("charge_status", sa.String(length=16), nullable=True))
    op.add_column(
        "rentals",
        sa.Column("charge_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("rentals", sa.Column("charge_next_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_rentals_hold_id_payment_operations"),
        "rentals",
        "payment_operations",
        ["hold_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_rentals_charge_status_known"),
        "rentals",
        "charge_status IS NULL OR charge_status IN ('failed', 'paid', 'pending')",
    )
    op.create_check_constraint(
        op.f("ck_rentals_charge_attempts_not_negative"), "rentals", "charge_attempts >= 0"
    )
    op.create_index(
        "ix_rentals_charge_due",
        "rentals",
        ["charge_next_at"],
        unique=False,
        postgresql_where=sa.text("charge_status IN ('failed', 'pending')"),
    )
    op.create_index(
        "ix_rentals_debt_user",
        "rentals",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("charge_status = 'failed'"),
    )

    op.create_table(
        "rental_intervals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_rental_intervals_ends_after_start"),
        ),
        sa.CheckConstraint(
            "kind IN ('driving', 'parked')", name=op.f("ck_rental_intervals_kind_known")
        ),
        sa.ForeignKeyConstraint(
            ["rental_id"], ["rentals.id"], name=op.f("fk_rental_intervals_rental_id_rentals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rental_intervals")),
    )
    op.create_index(
        op.f("ix_rental_intervals_rental_id"), "rental_intervals", ["rental_id"], unique=False
    )
    op.create_index(
        "uq_rental_intervals_open",
        "rental_intervals",
        ["rental_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "rental_receipts",
        sa.Column("rental_id", sa.Uuid(), nullable=False),
        sa.Column("tariff_version_id", sa.Uuid(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("card_brand", sa.String(length=16), nullable=True),
        sa.Column("card_last4", sa.String(length=4), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total >= 0", name=op.f("ck_rental_receipts_total_not_negative")),
        sa.ForeignKeyConstraint(
            ["rental_id"], ["rentals.id"], name=op.f("fk_rental_receipts_rental_id_rentals")
        ),
        sa.ForeignKeyConstraint(
            ["tariff_version_id"],
            ["tariff_versions.id"],
            name=op.f("fk_rental_receipts_tariff_version_id_tariff_versions"),
        ),
        sa.PrimaryKeyConstraint("rental_id", name=op.f("pk_rental_receipts")),
    )
    op.create_table(
        "rental_receipt_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rental_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("free_minutes", sa.Integer(), nullable=False),
        sa.Column("per_minute", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "minutes >= 0 AND free_minutes >= 0 AND per_minute >= 0 AND amount >= 0",
            name=op.f("ck_rental_receipt_lines_amounts_not_negative"),
        ),
        sa.CheckConstraint(
            "kind IN ('driving', 'parked', 'waiting')",
            name=op.f("ck_rental_receipt_lines_kind_known"),
        ),
        sa.ForeignKeyConstraint(
            ["rental_id"],
            ["rental_receipts.rental_id"],
            name=op.f("fk_rental_receipt_lines_rental_id_rental_receipts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rental_receipt_lines")),
        sa.UniqueConstraint(
            "rental_id", "kind", name=op.f("uq_rental_receipt_lines_rental_id")
        ),
    )

    # A receipt the client was shown never changes, like the audit log (docs/adr/0010, 0023).
    op.execute(
        """
        CREATE FUNCTION rental_receipts_reject_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER rental_receipts_no_update_delete
            BEFORE UPDATE OR DELETE ON rental_receipts
            FOR EACH ROW EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER rental_receipts_no_truncate
            BEFORE TRUNCATE ON rental_receipts
            FOR EACH STATEMENT EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER rental_receipt_lines_no_update_delete
            BEFORE UPDATE OR DELETE ON rental_receipt_lines
            FOR EACH ROW EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER rental_receipt_lines_no_truncate
            BEFORE TRUNCATE ON rental_receipt_lines
            FOR EACH STATEMENT EXECUTE FUNCTION rental_receipts_reject_change()
        """
    )


def downgrade() -> None:
    # DROP TABLE is not blocked by the triggers and removes them with the table.
    op.drop_table("rental_receipt_lines")
    op.drop_table("rental_receipts")
    op.execute("DROP FUNCTION rental_receipts_reject_change()")
    op.drop_index("uq_rental_intervals_open", table_name="rental_intervals")
    op.drop_index(op.f("ix_rental_intervals_rental_id"), table_name="rental_intervals")
    op.drop_table("rental_intervals")
    op.drop_index("ix_rentals_debt_user", table_name="rentals")
    op.drop_index("ix_rentals_charge_due", table_name="rentals")
    op.drop_constraint(op.f("ck_rentals_charge_attempts_not_negative"), "rentals", type_="check")
    op.drop_constraint(op.f("ck_rentals_charge_status_known"), "rentals", type_="check")
    op.drop_constraint(op.f("fk_rentals_hold_id_payment_operations"), "rentals", type_="foreignkey")
    op.drop_column("rentals", "charge_next_at")
    op.drop_column("rentals", "charge_attempts")
    op.drop_column("rentals", "charge_status")
    op.drop_column("rentals", "hold_id")
