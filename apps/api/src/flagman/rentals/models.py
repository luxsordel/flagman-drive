from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.pricing.domain import IntervalKind
from flagman.rentals.domain import (
    DAMAGE_COMMENT_MAX_LENGTH,
    CancelReason,
    ChargeStatus,
    DamageSide,
    InspectionStage,
    RentalStatus,
)

STATUS_LENGTH = 16
OBJECT_KEY_LENGTH = 128


def _listed(values: type[StrEnum] | frozenset[RentalStatus]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


# A literal, not built from ACTIVE_STATUSES: Semgrep refuses text() over a computed string.
# test_models.py keeps the two in step.
_ACTIVE = "status IN ('in_trip', 'parked', 'reserved')"


class Rental(Base):
    __tablename__ = "rentals"
    __table_args__ = (
        CheckConstraint(f"status IN ({_listed(RentalStatus)})", name="status_known"),
        CheckConstraint(
            f"cancel_reason IS NULL OR cancel_reason IN ({_listed(CancelReason)})",
            name="cancel_reason_known",
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancel_reason IS NOT NULL)",
            name="cancel_reason_matches_status",
        ),
        CheckConstraint(
            "reserved_at <= free_wait_until AND free_wait_until <= expires_at",
            name="reservation_deadlines_ordered",
        ),
        CheckConstraint(
            f"charge_status IS NULL OR charge_status IN ({_listed(ChargeStatus)})",
            name="charge_status_known",
        ),
        CheckConstraint("charge_attempts >= 0", name="charge_attempts_not_negative"),
        # Parallel requests cannot give one client two rentals or one car two clients: the
        # second insert fails here whatever the service checked before.
        Index("uq_rentals_active_user", "user_id", unique=True, postgresql_where=text(_ACTIVE)),
        Index("uq_rentals_active_car", "car_id", unique=True, postgresql_where=text(_ACTIVE)),
        # The timeout job looks for reservations past their end.
        Index(
            "ix_rentals_reserved_expires_at",
            "expires_at",
            postgresql_where=text("status = 'reserved'"),
        ),
        # The charge retry job and the debt check at a reservation.
        Index(
            "ix_rentals_charge_due",
            "charge_next_at",
            postgresql_where=text("charge_status IN ('failed', 'pending')"),
        ),
        Index(
            "ix_rentals_debt_user",
            "user_id",
            postgresql_where=text("charge_status = 'failed'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    car_id: Mapped[UUID] = mapped_column(ForeignKey("cars.id"), index=True)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    # The version current at the reservation; versions are never changed or deleted (pricing).
    tariff_version_id: Mapped[UUID] = mapped_column(ForeignKey("tariff_versions.id"))
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    free_wait_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trip_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    command_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The payment operation id of the trip's hold; payments keeps the operation itself.
    hold_id: Mapped[UUID | None] = mapped_column(ForeignKey("payment_operations.id"))
    charge_status: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    charge_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    charge_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Inspection(Base):
    """One per rental and stage, created with its first photo or damage."""

    __tablename__ = "rental_inspections"
    __table_args__ = (
        UniqueConstraint("rental_id", "stage"),
        CheckConstraint(f"stage IN ({_listed(InspectionStage)})", name="stage_known"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # No ON DELETE CASCADE: the photos are in the storage, which the database cannot remove.
    rental_id: Mapped[UUID] = mapped_column(ForeignKey("rentals.id"))
    stage: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InspectionPhoto(Base):
    __tablename__ = "rental_inspection_photos"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("rental_inspections.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(OBJECT_KEY_LENGTH), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InspectionDamage(Base):
    __tablename__ = "rental_inspection_damages"
    __table_args__ = (CheckConstraint(f"side IN ({_listed(DamageSide)})", name="side_known"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    inspection_id: Mapped[UUID] = mapped_column(
        ForeignKey("rental_inspections.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    comment: Mapped[str] = mapped_column(String(DAMAGE_COMMENT_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TripInterval(Base):
    """Driving and parked time; the wait before the trip comes from the rental itself."""

    __tablename__ = "rental_intervals"
    __table_args__ = (
        CheckConstraint("kind IN ('driving', 'parked')", name="kind_known"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ends_after_start"),
        # At most one open interval: a park and a resume racing cannot leave two running.
        Index(
            "uq_rental_intervals_open",
            "rental_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    rental_id: Mapped[UUID] = mapped_column(ForeignKey("rentals.id"), index=True)
    kind: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Receipt(Base):
    """Append-only: a trigger refuses updates and deletes (docs/adr/0023)."""

    __tablename__ = "rental_receipts"
    __table_args__ = (CheckConstraint("total >= 0", name="total_not_negative"),)

    rental_id: Mapped[UUID] = mapped_column(ForeignKey("rentals.id"), primary_key=True)
    tariff_version_id: Mapped[UUID] = mapped_column(ForeignKey("tariff_versions.id"))
    total: Mapped[int] = mapped_column(Integer)
    card_brand: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    card_last4: Mapped[str | None] = mapped_column(String(4))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReceiptLine(Base):
    __tablename__ = "rental_receipt_lines"
    __table_args__ = (
        UniqueConstraint("rental_id", "kind"),
        CheckConstraint(f"kind IN ({_listed(IntervalKind)})", name="kind_known"),
        CheckConstraint(
            "minutes >= 0 AND free_minutes >= 0 AND per_minute >= 0 AND amount >= 0",
            name="amounts_not_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    rental_id: Mapped[UUID] = mapped_column(ForeignKey("rental_receipts.rental_id"))
    kind: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    minutes: Mapped[int] = mapped_column(Integer)
    free_minutes: Mapped[int] = mapped_column(Integer)
    per_minute: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
