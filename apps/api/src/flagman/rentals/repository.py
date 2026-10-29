from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.pricing.domain import IntervalKind, Quote, QuoteLine
from flagman.rentals.domain import (
    ACTIVE_STATUSES,
    CancelReason,
    ChargeStatus,
    Damage,
    DamageSide,
    Inspection,
    InspectionPhoto,
    InspectionStage,
    Receipt,
    ReceiptCard,
    Rental,
    RentalStatus,
    TripInterval,
)
from flagman.rentals.models import Inspection as InspectionRow
from flagman.rentals.models import InspectionDamage as DamageRow
from flagman.rentals.models import InspectionPhoto as PhotoRow
from flagman.rentals.models import Receipt as ReceiptRow
from flagman.rentals.models import ReceiptLine as ReceiptLineRow
from flagman.rentals.models import Rental as RentalRow
from flagman.rentals.models import TripInterval as IntervalRow


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
        hold_id=row.hold_id,
        charge_status=ChargeStatus(row.charge_status) if row.charge_status else None,
        charge_attempts=row.charge_attempts,
        charge_next_at=row.charge_next_at,
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


async def get_rental_by_id(
    db: AsyncSession, rental_id: UUID, *, for_update: bool = False
) -> Rental | None:
    """For the jobs that act on any client's rental; requests use `get_rental`."""
    query = select(RentalRow).where(RentalRow.id == rental_id)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_rental(row) if row else None


async def get_rentals(db: AsyncSession, rental_ids: Collection[UUID]) -> list[Rental]:
    rows = await db.scalars(select(RentalRow).where(RentalRow.id.in_(rental_ids)))
    return [_to_rental(row) for row in rows]


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


async def get_reservations_ending(db: AsyncSession, now: datetime, until: datetime) -> list[Rental]:
    """Reservations still running whose end comes by `until`."""
    query = select(RentalRow).where(
        RentalRow.status == RentalStatus.RESERVED,
        RentalRow.expires_at > now,
        RentalRow.expires_at <= until,
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


async def release_command(
    db: AsyncSession, rental_id: UUID, held_until: datetime, now: datetime
) -> None:
    """Ends the hold of one command; a later command that took over the hold keeps it."""
    await db.execute(
        update(RentalRow)
        .where(RentalRow.id == rental_id, RentalRow.command_until == held_until)
        .values(command_until=None, updated_at=now)
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


# The trip and its settlement (docs/adr/0024)

_DRIVING = (RentalStatus.IN_TRIP, RentalStatus.PARKED)


async def set_hold(db: AsyncSession, rental_id: UUID, hold_id: UUID, now: datetime) -> None:
    await db.execute(
        update(RentalRow).where(RentalRow.id == rental_id).values(hold_id=hold_id, updated_at=now)
    )


async def set_status(
    db: AsyncSession, rental_id: UUID, status: RentalStatus, now: datetime
) -> None:
    await db.execute(
        update(RentalRow).where(RentalRow.id == rental_id).values(status=status, updated_at=now)
    )


async def finish(db: AsyncSession, rental_id: UUID, status: RentalStatus, now: datetime) -> None:
    await db.execute(
        update(RentalRow)
        .where(RentalRow.id == rental_id)
        .values(status=status, ended_at=now, command_until=None, updated_at=now)
    )


@dataclass(frozen=True)
class ChargeState:
    status: ChargeStatus | None
    attempts: int = 0
    # When the worker tries again; None when nothing is left to try.
    next_at: datetime | None = None


async def set_charge(db: AsyncSession, rental_id: UUID, state: ChargeState, now: datetime) -> None:
    await db.execute(
        update(RentalRow)
        .where(RentalRow.id == rental_id)
        .values(
            charge_status=state.status,
            charge_attempts=state.attempts,
            charge_next_at=state.next_at,
            updated_at=now,
        )
    )


async def has_debt(db: AsyncSession, user_id: UUID) -> bool:
    query = select(RentalRow.id).where(
        RentalRow.user_id == user_id, RentalRow.charge_status == ChargeStatus.FAILED
    )
    return await db.scalar(query.limit(1)) is not None


async def get_debts(db: AsyncSession, user_id: UUID) -> list[Rental]:
    query = (
        select(RentalRow)
        .where(RentalRow.user_id == user_id, RentalRow.charge_status == ChargeStatus.FAILED)
        .order_by(RentalRow.ended_at)
    )
    return [_to_rental(row) for row in await db.scalars(query)]


async def lock_due_charges(db: AsyncSession, now: datetime, limit: int) -> list[Rental]:
    """Charges the retry job owes a try: an unknown outcome or a declined debt whose time came.

    SKIP LOCKED: a rental a request settles right now is left to that request.
    """
    query = (
        select(RentalRow)
        .where(
            RentalRow.charge_status.in_((ChargeStatus.PENDING, ChargeStatus.FAILED)),
            RentalRow.charge_next_at <= now,
        )
        .order_by(RentalRow.charge_next_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return [_to_rental(row) for row in await db.scalars(query)]


async def get_trips(db: AsyncSession) -> list[Rental]:
    """Rentals on the road now, for the low battery watch."""
    query = select(RentalRow).where(RentalRow.status.in_(_DRIVING))
    return [_to_rental(row) for row in await db.scalars(query)]


async def open_interval(
    db: AsyncSession, rental_id: UUID, kind: IntervalKind, now: datetime
) -> None:
    db.add(IntervalRow(rental_id=rental_id, kind=kind, started_at=now))
    await db.flush()


async def close_interval(db: AsyncSession, rental_id: UUID, now: datetime) -> None:
    await db.execute(
        update(IntervalRow)
        .where(IntervalRow.rental_id == rental_id, IntervalRow.ended_at.is_(None))
        .values(ended_at=now)
    )


async def get_intervals(db: AsyncSession, rental_id: UUID) -> tuple[TripInterval, ...]:
    query = (
        select(IntervalRow)
        .where(IntervalRow.rental_id == rental_id)
        .order_by(IntervalRow.started_at, IntervalRow.id)
    )
    return tuple(
        TripInterval(kind=IntervalKind(row.kind), start=row.started_at, end=row.ended_at)
        for row in await db.scalars(query)
    )


async def add_receipt(db: AsyncSession, receipt: Receipt) -> None:
    card = receipt.card
    db.add(
        ReceiptRow(
            rental_id=receipt.rental_id,
            tariff_version_id=receipt.tariff_version_id,
            total=receipt.quote.total,
            card_brand=card.brand if card else None,
            card_last4=card.last4 if card else None,
            issued_at=receipt.issued_at,
        )
    )
    # The lines refer to the receipt: it goes in first.
    await db.flush()
    db.add_all(
        ReceiptLineRow(
            rental_id=receipt.rental_id,
            kind=line.kind,
            minutes=line.minutes,
            free_minutes=line.free_minutes,
            per_minute=line.per_minute,
            amount=line.amount,
        )
        for line in receipt.quote.lines
    )
    await db.flush()


async def get_receipt(db: AsyncSession, rental_id: UUID) -> Receipt | None:
    row = await db.get(ReceiptRow, rental_id)
    if row is None:
        return None
    lines = await db.scalars(select(ReceiptLineRow).where(ReceiptLineRow.rental_id == rental_id))
    order = list(IntervalKind)
    quote_lines = sorted(
        (
            QuoteLine(
                kind=IntervalKind(line.kind),
                minutes=line.minutes,
                free_minutes=line.free_minutes,
                per_minute=line.per_minute,
                amount=line.amount,
            )
            for line in lines
        ),
        key=lambda line: order.index(line.kind),
    )
    card = (
        ReceiptCard(brand=row.card_brand, last4=row.card_last4)
        if row.card_brand and row.card_last4
        else None
    )
    return Receipt(
        rental_id=row.rental_id,
        tariff_version_id=row.tariff_version_id,
        quote=Quote(lines=tuple(quote_lines), total=row.total),
        card=card,
        issued_at=row.issued_at,
    )
