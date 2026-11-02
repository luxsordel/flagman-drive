"""add support client report id

Revision ID: d2a7c9e4b816
Revises: e9c2b7d4a6f3
Create Date: 2026-11-02 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2a7c9e4b816"
down_revision: str | Sequence[str] | None = "e9c2b7d4a6f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("support_incidents", sa.Column("client_report_id", sa.Uuid(), nullable=True))
    op.create_index(
        "uq_support_incidents_client_report",
        "support_incidents",
        ["user_id", "client_report_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_support_incidents_client_report", table_name="support_incidents")
    op.drop_column("support_incidents", "client_report_id")
