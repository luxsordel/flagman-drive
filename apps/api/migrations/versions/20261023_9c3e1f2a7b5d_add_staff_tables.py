"""add staff tables

Revision ID: 9c3e1f2a7b5d
Revises: 9f5ccc74d90b
Create Date: 2026-10-23 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c3e1f2a7b5d"
down_revision: str | Sequence[str] | None = "9f5ccc74d90b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("totp_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('operator', 'technician', 'admin')",
            name=op.f("ck_staff_members_role_known"),
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_staff_members_email_lowercase")),
        sa.CheckConstraint(
            "totp_enrolled_at IS NULL OR totp_secret_encrypted IS NOT NULL",
            name=op.f("ck_staff_members_enrolled_has_totp_secret"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_members")),
        sa.UniqueConstraint("email", name=op.f("uq_staff_members_email")),
    )
    op.create_table(
        "staff_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff_members.id"],
            name=op.f("fk_staff_sessions_staff_id_staff_members"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_sessions")),
    )
    op.create_index(
        op.f("ix_staff_sessions_staff_id"), "staff_sessions", ["staff_id"], unique=False
    )
    op.create_table(
        "staff_refresh_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["staff_sessions.id"],
            name=op.f("fk_staff_refresh_tokens_session_id_staff_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_staff_refresh_tokens")),
    )
    op.create_index(
        op.f("ix_staff_refresh_tokens_session_id"),
        "staff_refresh_tokens",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_staff_refresh_tokens_session_id"), table_name="staff_refresh_tokens")
    op.drop_table("staff_refresh_tokens")
    op.drop_index(op.f("ix_staff_sessions_staff_id"), table_name="staff_sessions")
    op.drop_table("staff_sessions")
    op.drop_table("staff_members")
