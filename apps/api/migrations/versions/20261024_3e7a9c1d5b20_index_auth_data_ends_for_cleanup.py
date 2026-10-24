"""index auth data ends for cleanup

Revision ID: 3e7a9c1d5b20
Revises: c7d2e9f14a68
Create Date: 2026-10-24 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3e7a9c1d5b20"
down_revision: str | Sequence[str] | None = "c7d2e9f14a68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The periodic cleanup finds ended rows by these columns (#46).
_INDEXES = (
    ("pending_otps", "expires_at"),
    ("auth_sessions", "expires_at"),
    ("auth_sessions", "revoked_at"),
    ("staff_sessions", "expires_at"),
    ("staff_sessions", "revoked_at"),
)


def upgrade() -> None:
    for table, column in _INDEXES:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def downgrade() -> None:
    for table, column in reversed(_INDEXES):
        op.drop_index(op.f(f"ix_{table}_{column}"), table_name=table)
