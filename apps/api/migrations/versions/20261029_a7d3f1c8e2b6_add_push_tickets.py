"""add push tickets

Revision ID: a7d3f1c8e2b6
Revises: d7a3f9c1e2b8
Create Date: 2026-10-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3f1c8e2b6"
down_revision: str | Sequence[str] | None = "d7a3f9c1e2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_tickets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["push_devices.id"],
            name=op.f("fk_push_tickets_device_id_push_devices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_tickets")),
    )
    op.create_index(op.f("ix_push_tickets_device_id"), "push_tickets", ["device_id"], unique=False)
    op.create_index("ix_push_tickets_sent_at_id", "push_tickets", ["sent_at", "id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_push_tickets_sent_at_id", table_name="push_tickets")
    op.drop_index(op.f("ix_push_tickets_device_id"), table_name="push_tickets")
    op.drop_table("push_tickets")
