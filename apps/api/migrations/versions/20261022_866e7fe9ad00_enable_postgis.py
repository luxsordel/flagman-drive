"""enable postgis

Revision ID: 866e7fe9ad00
Revises:
Create Date: 2026-10-22 02:06:58.697867
"""

from collections.abc import Sequence

from alembic import op

revision: str = "866e7fe9ad00"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The postgis/postgis image creates the extension itself, a managed database does not.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # Intentionally kept: the extension may predate this migration (created by the image),
    # and dropping it would also drop every geometry column in the database.
    pass
