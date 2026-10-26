"""add payments

Revision ID: c4d9e2a1f6b3
Revises: 8a1f3c5e7b92
Create Date: 2026-10-26 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9e2a1f6b3"
down_revision: str | Sequence[str] | None = "8a1f3c5e7b92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DECLINE_REASON = "decline_reason IS NULL OR decline_reason IN ('declined', 'insufficient_funds')"


def upgrade() -> None:
    op.create_table(
        "payment_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("brand", sa.String(length=32), nullable=False),
        sa.Column("last4", sa.String(length=4), nullable=False),
        sa.Column("exp_month", sa.SmallInteger(), nullable=False),
        sa.Column("exp_year", sa.SmallInteger(), nullable=False),
        sa.Column("token_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "brand IN ('visa', 'mastercard', 'mir', 'belcard', 'other')",
            name=op.f("ck_payment_cards_brand_known"),
        ),
        sa.CheckConstraint(
            "exp_month BETWEEN 1 AND 12", name=op.f("ck_payment_cards_exp_month_range")
        ),
        sa.CheckConstraint(
            "(removed_at IS NULL) = (token_ciphertext IS NOT NULL)",
            name=op.f("ck_payment_cards_token_live"),
        ),
        sa.CheckConstraint(
            "NOT (is_default AND removed_at IS NOT NULL)",
            name=op.f("ck_payment_cards_removed_not_default"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_payment_cards_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_cards")),
    )
    op.create_index(op.f("ix_payment_cards_user_id"), "payment_cards", ["user_id"], unique=False)
    op.create_index(
        "uq_payment_cards_user_id_default",
        "payment_cards",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "card_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("return_url", sa.String(length=512), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=True),
        sa.Column("decline_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'declined', 'cancelled')",
            name=op.f("ck_card_bindings_status_known"),
        ),
        sa.CheckConstraint(
            "(status = 'succeeded') = (card_id IS NOT NULL)",
            name=op.f("ck_card_bindings_card_only_when_succeeded"),
        ),
        sa.CheckConstraint(_DECLINE_REASON, name=op.f("ck_card_bindings_decline_reason_known")),
        sa.ForeignKeyConstraint(
            ["card_id"], ["payment_cards.id"], name=op.f("fk_card_bindings_card_id_payment_cards")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_card_bindings_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_bindings")),
    )
    op.create_index(op.f("ix_card_bindings_user_id"), "card_bindings", ["user_id"], unique=False)

    op.create_table(
        "payment_webhook_events",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider", "event_id", name=op.f("pk_payment_webhook_events")),
    )

    op.create_table(
        "payment_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_ref", sa.String(length=128), nullable=True),
        sa.Column("decline_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('hold', 'capture', 'charge', 'void', 'refund')",
            name=op.f("ck_payment_operations_kind_known"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'declined', 'failed')",
            name=op.f("ck_payment_operations_status_known"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payment_operations_amount_positive")),
        sa.CheckConstraint(
            "(kind IN ('hold', 'charge')) = (parent_id IS NULL)",
            name=op.f("ck_payment_operations_parent_matches_kind"),
        ),
        sa.CheckConstraint(
            _DECLINE_REASON, name=op.f("ck_payment_operations_decline_reason_known")
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["payment_cards.id"],
            name=op.f("fk_payment_operations_card_id_payment_cards"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["payment_operations.id"],
            name=op.f("fk_payment_operations_parent_id_payment_operations"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_payment_operations_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_operations")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_payment_operations_idempotency_key")),
    )
    op.create_index(
        op.f("ix_payment_operations_parent_id"), "payment_operations", ["parent_id"], unique=False
    )
    op.create_index(
        op.f("ix_payment_operations_user_id"), "payment_operations", ["user_id"], unique=False
    )

    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["payment_operations.id"],
            name=op.f("fk_payment_attempts_operation_id_payment_operations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
    )
    op.create_index(
        op.f("ix_payment_attempts_operation_id"),
        "payment_attempts",
        ["operation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("payment_attempts")
    op.drop_table("payment_operations")
    op.drop_table("payment_webhook_events")
    op.drop_table("card_bindings")
    op.drop_table("payment_cards")
