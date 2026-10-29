"""add user blocked_at

Revision ID: 9a1f3c5e7b20
Revises: c4d9e2a1f6b3
Create Date: 2026-10-26 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a1f3c5e7b20"
down_revision: str | Sequence[str] | None = "c4d9e2a1f6b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "blocked_at")
