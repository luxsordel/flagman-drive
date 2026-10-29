from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.rentals.domain import (
    ACTIVE_STATUSES,
    CancelReason,
    Damage,
    DamageSide,
    Inspection,
    InspectionPhoto,
    InspectionStage,
    Rental,
    RentalStatus,
)
from flagman.rentals.models import Inspection as InspectionRow
from flagman.rentals.models import InspectionDamage as DamageRow
from flagman.rentals.models import InspectionPhoto as PhotoRow
from flagman.rentals.models import Rental as RentalRow


class ActiveRentalTakenError(Exception):
    """The client or the car already has an active rental: a parallel request won."""


def _to_rental(row: RentalRow) -> Rental:
    return Rental(
        id=row.id,
        user_id=row.user_id,
        car_id=row.car_id,
        status=RentalStatus(row.status),
        tariff_version_id=row.tariff_version_id,
        reserved_at=row.reserved_at,
        free_wait_until=row.free_wait_until,
        expires_at=row.expires_at,
        trip_started_at=row.trip_started_at,
        ended_at=row.ended_at,
        cancel_reason=CancelReason(row.cancel_reason) if row.cancel_reason else None,
        command_until=row.command_until,
    )


@dataclass(frozen=True)
class NewRental:
    user_id: UUID
    car_id: UUID
    tariff_version_id: UUID
    reserved_at: datetime
    free_wait_until: datetime
    expires_at: datetime


async def add_rental(db: AsyncSession, rental: NewRental) -> Rental:
    """Raises ActiveRentalTakenError; the caller's transaction is unusable after it."""
    row = RentalRow(
        user_id=rental.user_id,
        car_id=rental.car_id,
        status=RentalStatus.RESERVED,
        tariff_version_id=rental.tariff_version_id,
        reserved_at=rental.reserved_at,
        free_wait_until=rental.free_wait_until,
        expires_at=rental.expires_at,
        updated_at=rental.reserved_at,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        raise ActiveRentalTakenError from error
    return _to_rental(row)


async def get_rental(
    db: AsyncSession, rental_id: UUID, user_id: UUID, *, for_update: bool = False
) -> Rental | None:
    # The owner is part of the query: another client's rental is simply not found.
    query = select(RentalRow).where(RentalRow.id == rental_id, RentalRow.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_rental(row) if row else None


async def get_active_rental(
    db: AsyncSession, user_id: UUID, *, for_update: bool = False
) -> Rental | None:
    query = select(RentalRow).where(
        RentalRow.user_id == user_id, RentalRow.status.in_(ACTIVE_STATUSES)
    )
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_rental(row) if row else None


async def get_active_rental_of_car(
    db: AsyncSession, car_id: UUID, *, for_update: bool = False
) -> Rental | None:
    query = select(RentalRow).where(
        RentalRow.car_id == car_id, RentalRow.status.in_(ACTIVE_STATUSES)
    )
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_rental(row) if row else None


async def lock_expired_reservations(db: AsyncSession, now: datetime, limit: int) -> list[Rental]:
    """Reservations past their end that no command holds, oldest first.

    SKIP LOCKED: a reservation a request holds right now is left to that request, which checks
    the deadline itself.
    """
    query = (
        select(RentalRow)
        .where(
            RentalRow.status == RentalStatus.RESERVED,
            RentalRow.expires_at <= now,
            (RentalRow.command_until.is_(None)) | (RentalRow.command_until <= now),
        )
        .order_by(RentalRow.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return [_to_rental(row) for row in await db.scalars(query)]


async def cancel(
    db: AsyncSession, rental_id: UUID, status: RentalStatus, reason: CancelReason, now: datetime
) -> None:
    await db.execute(
        update(RentalRow)
        .where(RentalRow.id == rental_id)
        .values(status=status, cancel_reason=reason, ended_at=now, updated_at=now)
    )


async def start_trip(
    db: AsyncSession, rental_id: UUID, status: RentalStatus, now: datetime
) -> None:
    await db.execute(
        update(RentalRow)
        .where(RentalRow.id == rental_id)
        .values(status=status, trip_started_at=now, command_until=None, updated_at=now)
    )


async def set_command_until(
    db: AsyncSession, rental_id: UUID, until: datetime | None, now: datetime
) -> None:
    await db.execute(
        update(RentalRow)
        .where(RentalRow.id == rental_id)
        .values(command_until=until, updated_at=now)
    )


@dataclass(frozen=True)
class InspectionRecord:
    id: UUID
    inspection: Inspection
    object_keys: dict[UUID, str]


async def get_inspection(
    db: AsyncSession, rental_id: UUID, stage: InspectionStage, *, for_update: bool = False
) -> InspectionRecord | None:
    query = select(InspectionRow).where(
        InspectionRow.rental_id == rental_id, InspectionRow.stage == stage
    )
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        return None
    photos: Sequence[PhotoRow] = (
        await db.scalars(
            select(PhotoRow)
            .where(PhotoRow.inspection_id == row.id)
            .order_by(PhotoRow.uploaded_at, PhotoRow.id)
        )
    ).all()
    damages = await db.scalars(
        select(DamageRow)
        .where(DamageRow.inspection_id == row.id)
        .order_by(DamageRow.created_at, DamageRow.id)
    )
    inspection = Inspection(
        stage=stage,
        photos=tuple(InspectionPhoto(photo.id, photo.uploaded_at) for photo in photos),
        damages=tuple(
            Damage(damage.id, DamageSide(damage.side), damage.comment, damage.created_at)
            for damage in damages
        ),
        submitted_at=row.submitted_at,
    )
    return InspectionRecord(row.id, inspection, {photo.id: photo.object_key for photo in photos})


async def lock_or_create_inspection(
    db: AsyncSession, rental_id: UUID, stage: InspectionStage, now: datetime
) -> InspectionRecord:
    await db.execute(
        insert(InspectionRow)
        .values(rental_id=rental_id, stage=stage, created_at=now)
        .on_conflict_do_nothing(index_elements=["rental_id", "stage"])
    )
    record = await get_inspection(db, rental_id, stage, for_update=True)
    if record is None:
        raise RuntimeError("inspection vanished right after it was created")
    return record


async def add_photo(
    db: AsyncSession, inspection_id: UUID, object_key: str, size_bytes: int, now: datetime
) -> None:
    db.add(
        PhotoRow(
            inspection_id=inspection_id,
            object_key=object_key,
            size_bytes=size_bytes,
            uploaded_at=now,
        )
    )
    await db.flush()


async def add_damage(
    db: AsyncSession, inspection_id: UUID, side: DamageSide, comment: str, now: datetime
) -> None:
    db.add(DamageRow(inspection_id=inspection_id, side=side, comment=comment, created_at=now))
    await db.flush()


async def submit_inspection(db: AsyncSession, inspection_id: UUID, now: datetime) -> None:
    await db.execute(
        update(InspectionRow).where(InspectionRow.id == inspection_id).values(submitted_at=now)
    )
