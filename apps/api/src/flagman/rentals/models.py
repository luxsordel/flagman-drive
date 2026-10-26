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
from flagman.rentals.domain import (
    ACTIVE_STATUSES,
    DAMAGE_COMMENT_MAX_LENGTH,
    CancelReason,
    DamageSide,
    InspectionStage,
    RentalStatus,
)

STATUS_LENGTH = 16
OBJECT_KEY_LENGTH = 128


def _listed(values: type[StrEnum] | frozenset[RentalStatus]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


_ACTIVE = f"status IN ({_listed(ACTIVE_STATUSES)})"


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
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    car_id: Mapped[UUID] = mapped_column(ForeignKey("cars.id"), index=True)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    # The tariffs (#109) add their table; until then the column stays empty, without a key.
    tariff_version_id: Mapped[UUID | None]
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    free_wait_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trip_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    command_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
