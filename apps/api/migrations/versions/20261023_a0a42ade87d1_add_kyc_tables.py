"""add kyc tables

Revision ID: a0a42ade87d1
Revises: 4a250ed5a850
Create Date: 2026-10-23 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a0a42ade87d1"
down_revision: str | Sequence[str] | None = "4a250ed5a850"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kyc_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('not_submitted', 'pending', 'approved', 'rejected')",
            name=op.f("ck_kyc_applications_status_known"),
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name=op.f("ck_kyc_applications_rejection_reason_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_kyc_applications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kyc_applications")),
        sa.UniqueConstraint("user_id", name=op.f("uq_kyc_applications_user_id")),
    )
    op.create_table(
        "kyc_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('license_front', 'license_back', 'passport', 'selfie_with_license')",
            name=op.f("ck_kyc_documents_kind_known"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["kyc_applications.id"],
            name=op.f("fk_kyc_documents_application_id_kyc_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kyc_documents")),
        sa.UniqueConstraint("application_id", "kind", name=op.f("uq_kyc_documents_application_id")),
        sa.UniqueConstraint("object_key", name=op.f("uq_kyc_documents_object_key")),
    )


def downgrade() -> None:
    op.drop_table("kyc_documents")
    op.drop_table("kyc_applications")
