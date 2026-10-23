"""add client profile

Revision ID: 5d2b8e41c7f3
Revises: 9c3e1f2a7b5d
Create Date: 2026-10-23 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d2b8e41c7f3"
down_revision: str | Sequence[str] | None = "9c3e1f2a7b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column(
        "users",
        sa.Column("language", sa.String(length=2), server_default="ru", nullable=False),
    )
    op.create_check_constraint(op.f("ck_users_language_known"), "users", "language IN ('ru', 'en')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_language_known"), "users", type_="check")
    op.drop_column("users", "language")
    op.drop_column("users", "email")
    op.drop_column("users", "birth_date")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
