"""add kyc review

Revision ID: c7d2e9f14a68
Revises: 5d2b8e41c7f3
Create Date: 2026-10-23 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2e9f14a68"
down_revision: str | Sequence[str] | None = "5d2b8e41c7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kyc_applications", sa.Column("license_issued_on", sa.Date(), nullable=True))
    # No application could be approved before this revision, so the check holds for every row.
    op.create_check_constraint(
        op.f("ck_kyc_applications_approved_has_license_issue_date"),
        "kyc_applications",
        "status <> 'approved' OR license_issued_on IS NOT NULL",
    )
    op.create_index(
        "ix_kyc_applications_pending_submitted_at",
        "kyc_applications",
        ["submitted_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_kyc_applications_pending_submitted_at", table_name="kyc_applications")
    op.drop_constraint(
        op.f("ck_kyc_applications_approved_has_license_issue_date"),
        "kyc_applications",
        type_="check",
    )
    op.drop_column("kyc_applications", "license_issued_on")
