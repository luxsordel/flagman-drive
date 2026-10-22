"""audit events clock timestamp

Revision ID: 9f5ccc74d90b
Revises: 4a250ed5a850
Create Date: 2026-10-23 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f5ccc74d90b"
down_revision: str | Sequence[str] | None = "4a250ed5a850"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # now() is the start of the transaction: events recorded in one transaction would share it
    # and their order in the log would come down to random ids (docs/adr/0010).
    op.alter_column("audit_events", "occurred_at", server_default=sa.text("clock_timestamp()"))


def downgrade() -> None:
    op.alter_column("audit_events", "occurred_at", server_default=sa.text("now()"))
