"""add support client message id

Revision ID: b3f6d8a1c5e7
Revises: c4e8a2f6b1d9
Create Date: 2026-11-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3f6d8a1c5e7"
down_revision: str | Sequence[str] | None = "c4e8a2f6b1d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("support_messages", sa.Column("client_message_id", sa.Uuid(), nullable=True))
    op.create_index(
        "uq_support_messages_client_message",
        "support_messages",
        ["conversation_id", "client_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_support_messages_client_message", table_name="support_messages")
    op.drop_column("support_messages", "client_message_id")
