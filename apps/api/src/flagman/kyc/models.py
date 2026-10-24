from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.kyc.domain import REJECTION_REASON_MAX_LENGTH, DocumentKind, KycStatus

STATUS_LENGTH = 16
KIND_LENGTH = 32
OBJECT_KEY_LENGTH = 128


def _one_of(column: str, values: type[KycStatus] | type[DocumentKind]) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({listed})"


class Application(Base):
    """One per client, created with the first upload; no row means nothing was uploaded yet."""

    __tablename__ = "kyc_applications"
    __table_args__ = (
        CheckConstraint(_one_of("status", KycStatus), name="status_known"),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="rejection_reason_matches_status",
        ),
        # Approval is checked against it, so an approved application without one is a bug.
        CheckConstraint(
            "status <> 'approved' OR license_issued_on IS NOT NULL",
            name="approved_has_license_issue_date",
        ),
        # The operators' queue: pending applications, the longest waiting first.
        Index(
            "ix_kyc_applications_pending_submitted_at",
            "submitted_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # No ON DELETE CASCADE: removing a client must also remove the photos from the storage,
    # which the database cannot do, so it is refused until the documents are gone.
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    rejection_reason: Mapped[str | None] = mapped_column(String(REJECTION_REASON_MAX_LENGTH))
    # Read by the operator from the license photo at approval; driving experience counts from it.
    license_issued_on: Mapped[date | None] = mapped_column(Date)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Document(Base):
    """The current photo of one kind; a new upload of the same kind replaces it."""

    __tablename__ = "kyc_documents"
    __table_args__ = (
        UniqueConstraint("application_id", "kind"),
        CheckConstraint(_one_of("kind", DocumentKind), name="kind_known"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("kyc_applications.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    object_key: Mapped[str] = mapped_column(String(OBJECT_KEY_LENGTH), unique=True)
    size_bytes: Mapped[int]
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
