"""Rentals for clients: reserve, cancel, inspect the car and open it (docs/02-architecture.md).

Every change runs in one transaction with the car's status (fleet) and holds the rental row, so
a request, the timeout job and a parallel request of the same client never act on one rental
at once. A command to the car is the exception: nothing is held while the car thinks, the
rental is marked busy instead (docs/adr/0022).
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.core.geo import GeoPoint
from flagman.core.photos import (
    OUTPUT_CONTENT_TYPE,
    PhotoTooLargeError,
    PhotoTypeUnsupportedError,
    clean_photo,
)
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.core.storage import ObjectStorage
from flagman.fleet import service as fleet
from flagman.fleet.domain import (
    AvailabilityRules,
    Car,
    CarCommand,
    CarNotFoundError,
    CarTransition,
)
from flagman.geo import service as geo
from flagman.identity import service as identity
from flagman.kyc import service as kyc
from flagman.kyc.domain import KycStatus
from flagman.pricing import service as pricing
from flagman.pricing.domain import TariffNotFoundError, TariffPrices, TariffVersion
from flagman.rentals import repository
from flagman.rentals.domain import (
    SIGNED_URL_TTL,
    ActiveRentalExistsError,
    CancelReason,
    ClientBlockedError,
    CommandRules,
    DamageSide,
    Inspection,
    InspectionPhotoNotFoundError,
    InspectionPhotoTooLargeError,
    InspectionPhotoTypeUnsupportedError,
    InspectionStage,
    KycNotApprovedError,
    Rental,
    RentalBusyError,
    RentalCommand,
    RentalNotFoundError,
    RentalStatus,
    RentalTransition,
    ReservationExpiredError,
    ReservationTerms,
    apply_transition,
    ensure_command_allowed,
    ensure_near_car,
    normalize_damage_comment,
    reservation_deadlines,
)
from flagman.rentals.repository import ActiveRentalTakenError, InspectionRecord, NewRental

logger = structlog.get_logger(__name__)

_HOUR = 3600
# A client looks around and changes their mind a few times; dozens an hour is a script that
# holds cars away from everyone else.
RESERVE_LIMIT = Limit("rental-reserve-user", 20, _HOUR)
# Open and close are a matter of seconds of walking around the car (docs/03-security.md).
COMMAND_LIMIT = Limit("rental-commands-user", 10, 60)
# Eight photos per inspection and a few retries on a bad network.
UPLOAD_LIMIT = Limit("rental-photo-upload-user", 40, _HOUR)
PHOTO_URL_LIMIT = Limit("rental-photo-url-user", 120, _HOUR)

# The command holds the rental a little longer than telematics may take to answer it.
_COMMAND_HOLD_MARGIN = timedelta(seconds=5)
_EXPIRY_BATCH = 100


@dataclass(frozen=True)
class RentalRules:
    max_wait: timedelta
    availability: AvailabilityRules
    commands: CommandRules
    command_hold: timedelta


def rental_rules(settings: Settings) -> RentalRules:
    availability = fleet.availability_rules(settings)
    return RentalRules(
        max_wait=timedelta(minutes=settings.rental_max_reservation_minutes),
        availability=availability,
        commands=CommandRules(
            max_distance_m=settings.rental_command_max_distance_m,
            max_state_age=availability.max_state_age,
        ),
        command_hold=timedelta(seconds=settings.telematics_command_timeout_seconds)
        + _COMMAND_HOLD_MARGIN,
    )


@dataclass(frozen=True)
class InspectionRef:
    user_id: UUID
    rental_id: UUID
    stage: InspectionStage


@dataclass(frozen=True)
class CommandRequest:
    user_id: UUID
    rental_id: UUID
    command: RentalCommand
    # Used for the distance only: never stored or logged.
    client_at: GeoPoint


@dataclass(frozen=True)
class RentalView:
    rental: Rental
    car: Car
    # Of the version fixed at the reservation, not the current one.
    prices: TariffPrices
    inspection_submitted: bool


async def _current_tariff(db: AsyncSession, car: Car) -> TariffVersion:
    """Raises CarNotFoundError for a model without a tariff: such a car is not on the map either."""
    try:
        return await pricing.current_version(db, car.model_id)
    except TariffNotFoundError as error:
        raise CarNotFoundError from error


async def _expire(db: AsyncSession, rental: Rental, now: datetime) -> Rental:
    status = apply_transition(rental.status, RentalTransition.EXPIRE)
    await repository.cancel(db, rental.id, status, CancelReason.EXPIRED, now)
    await fleet.change_status(db, rental.car_id, CarTransition.CANCEL_RESERVATION, now)
    logger.info("rental_reservation_expired", rental_id=str(rental.id))
    return replace(rental, status=status, cancel_reason=CancelReason.EXPIRED, ended_at=now)


async def _expire_if_due(db: AsyncSession, rental: Rental, now: datetime) -> Rental:
    """Cancels a reservation past its end; the rental row must be locked by the caller."""
    return await _expire(db, rental, now) if rental.is_expired(now) else rental


async def expire_reservations(db: AsyncSession, now: datetime) -> int:
    """The timeout job (docs/adr/0022): cancels the reservations nobody has touched since
    their end, batch by batch, each batch in a transaction of its own. Returns how many."""
    expired = 0
    while True:
        async with db.begin():
            rentals = await repository.lock_expired_reservations(db, now, _EXPIRY_BATCH)
            for rental in rentals:
                await _expire(db, rental, now)
        expired += len(rentals)
        if len(rentals) < _EXPIRY_BATCH:
            return expired


def _was_expired(rental: Rental) -> bool:
    return rental.cancel_reason is CancelReason.EXPIRED


@dataclass(frozen=True)
class RentalService:
    db: AsyncSession
    limiter: RateLimiter
    rules: RentalRules
    # The trip starts when the car has opened, which is seconds after the request came.
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    async def _ensure_admitted(self, user_id: UUID) -> None:
        if await kyc.get_admission_status(self.db, user_id) is not KycStatus.APPROVED:
            raise KycNotApprovedError
        if await identity.is_client_blocked(self.db, user_id):
            raise ClientBlockedError

    async def _lock(self, user_id: UUID, rental_id: UUID, now: datetime) -> Rental:
        rental = await repository.get_rental(self.db, rental_id, user_id, for_update=True)
        if rental is None:
            raise RentalNotFoundError
        return await _expire_if_due(self.db, rental, now)

    async def _view(self, rental: Rental) -> RentalView:
        car = await fleet.get_car(self.db, rental.car_id)
        before = await repository.get_inspection(self.db, rental.id, InspectionStage.BEFORE)
        submitted = before is not None and before.inspection.is_submitted
        tariff = await pricing.get_version(self.db, rental.tariff_version_id)
        return RentalView(
            rental=rental, car=car, prices=tariff.prices, inspection_submitted=submitted
        )

    async def reserve(self, user_id: UUID, car_id: UUID, now: datetime) -> RentalView:
        await self.limiter.hit(RESERVE_LIMIT, str(user_id))
        async with self.db.begin():
            await self._ensure_admitted(user_id)
            own = await repository.get_active_rental(self.db, user_id, for_update=True)
            if own is not None and (await _expire_if_due(self.db, own, now)).is_active:
                raise ActiveRentalExistsError
            # A reservation of this car past its end that the timeout job has not reached yet.
            held = await repository.get_active_rental_of_car(self.db, car_id, for_update=True)
            if held is not None:
                await _expire_if_due(self.db, held, now)
            car = await fleet.reserve_car(self.db, car_id, self.rules.availability, now)
            tariff = await _current_tariff(self.db, car)
            terms = ReservationTerms(
                free_wait=timedelta(minutes=tariff.prices.free_waiting_minutes),
                max_wait=self.rules.max_wait,
            )
            free_wait_until, expires_at = reservation_deadlines(now, terms)
            try:
                rental = await repository.add_rental(
                    self.db,
                    NewRental(
                        user_id=user_id,
                        car_id=car.id,
                        tariff_version_id=tariff.id,
                        reserved_at=now,
                        free_wait_until=free_wait_until,
                        expires_at=expires_at,
                    ),
                )
            except ActiveRentalTakenError as error:
                raise ActiveRentalExistsError from error
        logger.info("rental_reserved", rental_id=str(rental.id), car_id=str(car.id))
        return RentalView(rental=rental, car=car, prices=tariff.prices, inspection_submitted=False)

    async def get(self, user_id: UUID, rental_id: UUID, now: datetime) -> RentalView:
        async with self.db.begin():
            return await self._view(await self._lock(user_id, rental_id, now))

    async def current(self, user_id: UUID, now: datetime) -> RentalView | None:
        """The client's active rental, so the app can come back to it after a restart."""
        async with self.db.begin():
            rental = await repository.get_active_rental(self.db, user_id, for_update=True)
            if rental is None:
                return None
            rental = await _expire_if_due(self.db, rental, now)
            return await self._view(rental) if rental.is_active else None

    async def cancel(self, user_id: UUID, rental_id: UUID, now: datetime) -> RentalView:
        """Cancelling a cancelled reservation answers it as it is: a retry after a lost answer."""
        async with self.db.begin():
            rental = await self._lock(user_id, rental_id, now)
            if rental.status is not RentalStatus.CANCELLED:
                if rental.is_busy(now):
                    # The car may be opening for the client right now.
                    raise RentalBusyError
                status = apply_transition(rental.status, RentalTransition.CANCEL)
                await repository.cancel(self.db, rental.id, status, CancelReason.CLIENT, now)
                await fleet.change_status(
                    self.db, rental.car_id, CarTransition.CANCEL_RESERVATION, now
                )
                rental = await repository.get_rental(self.db, rental.id, user_id) or rental
                logger.info("rental_reservation_cancelled", rental_id=str(rental.id))
            return await self._view(rental)

    async def inspection(self, ref: InspectionRef, now: datetime) -> Inspection:
        async with self.db.begin():
            rental = await self._lock(ref.user_id, ref.rental_id, now)
            record = await repository.get_inspection(self.db, rental.id, ref.stage)
        return record.inspection if record else Inspection(stage=ref.stage)

    async def count_upload(self, user_id: UUID) -> None:
        """Counted before the body is read, so an exhausted limit does not cost 10 MB each time."""
        await self.limiter.hit(UPLOAD_LIMIT, str(user_id))

    async def _lock_inspection(
        self, ref: InspectionRef, now: datetime
    ) -> tuple[Rental, InspectionRecord] | None:
        """None once the reservation has expired; the expiry still commits with the caller."""
        rental = await self._lock(ref.user_id, ref.rental_id, now)
        if _was_expired(rental):
            return None
        record = await repository.lock_or_create_inspection(self.db, rental.id, ref.stage, now)
        return rental, record

    async def add_photo(
        self, storage: ObjectStorage, ref: InspectionRef, data: bytes, now: datetime
    ) -> Inspection:
        async with self.db.begin():
            locked = await self._lock_inspection(ref, now)
            if locked is not None:
                rental, record = locked
                # Checked before the photo is decoded and stored, and again under the lock below.
                record.inspection.ensure_photo_fits(rental.status)
        if locked is None:
            raise ReservationExpiredError
        try:
            photo = await asyncio.to_thread(clean_photo, data)
        except PhotoTooLargeError as error:
            raise InspectionPhotoTooLargeError(str(error) or None) from error
        except PhotoTypeUnsupportedError as error:
            raise InspectionPhotoTypeUnsupportedError from error
        # A new key for every photo, without anything about the client in it.
        key = f"inspections/{rental.id}/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)
        updated: InspectionRecord | None = None
        try:
            async with self.db.begin():
                locked = await self._lock_inspection(ref, now)
                if locked is not None:
                    rental, record = locked
                    # A parallel upload may have taken the last place meanwhile.
                    record.inspection.ensure_photo_fits(rental.status)
                    await repository.add_photo(self.db, record.id, key, len(photo), now)
                    updated = await repository.get_inspection(self.db, rental.id, ref.stage)
        except Exception:
            await _delete_quietly(storage, key)
            raise
        if updated is None:
            await _delete_quietly(storage, key)
            raise ReservationExpiredError
        logger.info("rental_inspection_photo_added", rental_id=str(rental.id), stage=ref.stage)
        return updated.inspection

    async def photo_url(self, storage: ObjectStorage, ref: InspectionRef, photo_id: UUID) -> str:
        await self.limiter.hit(PHOTO_URL_LIMIT, str(ref.user_id))
        async with self.db.begin():
            rental = await repository.get_rental(self.db, ref.rental_id, ref.user_id)
            if rental is None:
                raise RentalNotFoundError
            record = await repository.get_inspection(self.db, rental.id, ref.stage)
        key = record.object_keys.get(photo_id) if record else None
        if key is None:
            raise InspectionPhotoNotFoundError
        return await storage.signed_get_url(key, SIGNED_URL_TTL)

    async def add_damage(
        self, ref: InspectionRef, side: DamageSide, comment: str, now: datetime
    ) -> Inspection:
        updated: InspectionRecord | None = None
        async with self.db.begin():
            locked = await self._lock_inspection(ref, now)
            if locked is not None:
                rental, record = locked
                record.inspection.ensure_damage_fits(rental.status)
                await repository.add_damage(
                    self.db, record.id, side, normalize_damage_comment(comment), now
                )
                updated = await repository.get_inspection(self.db, rental.id, ref.stage)
        if updated is None:
            raise ReservationExpiredError
        return updated.inspection

    async def submit_inspection(self, ref: InspectionRef, now: datetime) -> Inspection:
        updated: InspectionRecord | None = None
        async with self.db.begin():
            locked = await self._lock_inspection(ref, now)
            if locked is not None:
                rental, record = locked
                record.inspection.ensure_submittable(rental.status)
                await repository.submit_inspection(self.db, record.id, now)
                updated = await repository.get_inspection(self.db, rental.id, ref.stage)
        if updated is None:
            raise ReservationExpiredError
        logger.info("rental_inspection_submitted", rental_id=str(rental.id), stage=ref.stage)
        return updated.inspection

    async def send_command(
        self, commander: fleet.RentalCarCommander, request: CommandRequest, now: datetime
    ) -> RentalView:
        """Checks that the client may command the car, sends the command and, on the first
        opening, starts the trip. A failed command leaves the rental as it was."""
        user_id, rental_id = request.user_id, request.rental_id
        await self.limiter.hit(COMMAND_LIMIT, str(user_id))
        async with self.db.begin():
            rental = await self._lock(user_id, rental_id, now)
            if not _was_expired(rental):
                await self._ensure_admitted(user_id)
                before = await repository.get_inspection(self.db, rental.id, InspectionStage.BEFORE)
                ensure_command_allowed(
                    rental, before is not None and before.inspection.is_submitted, now
                )
                car = await fleet.get_car(self.db, rental.car_id)
                state = car.state
                distance = (
                    await geo.distance_m(self.db, request.client_at, state.location)
                    if state
                    else None
                )
                ensure_near_car(
                    state.recorded_at if state else None, distance, self.rules.commands, now
                )
                # Holds the rental while the car thinks: the timeout and a cancel wait for it.
                hold_until = now + self.rules.command_hold
                await repository.set_command_until(self.db, rental.id, hold_until, now)
        if _was_expired(rental):
            raise ReservationExpiredError

        car_request = fleet.RentalCommandRequest(
            car_id=rental.car_id,
            command=CarCommand(request.command.value),
            client_id=user_id,
            rental_id=rental.id,
        )
        try:
            await commander.send(car_request, now)
        except Exception:
            async with self.db.begin():
                await repository.release_command(self.db, rental.id, hold_until, self.clock())
            raise

        done_at = self.clock()
        async with self.db.begin():
            # This request does not expire the reservation even if the car answered after the hold
            # ran out: it has opened for it. The timeout job may still have cancelled it meanwhile,
            # once `command_until` passed; the hold is sized so that a car answering in time
            # never meets that (docs/adr/0022).
            locked = await repository.get_rental(self.db, rental_id, user_id, for_update=True)
            if locked is None:
                raise RentalNotFoundError
            rental = locked
            if request.command is RentalCommand.OPEN and rental.status is RentalStatus.RESERVED:
                status = apply_transition(rental.status, RentalTransition.START_TRIP)
                await fleet.change_status(
                    self.db, rental.car_id, CarTransition.START_RENTAL, done_at
                )
                await repository.start_trip(self.db, rental.id, status, done_at)
                logger.info("rental_trip_started", rental_id=str(rental.id))
            else:
                await repository.release_command(self.db, rental.id, hold_until, done_at)
            rental = await repository.get_rental(self.db, rental.id, user_id) or rental
            return await self._view(rental)


async def _delete_quietly(storage: ObjectStorage, key: str) -> None:
    try:
        await storage.delete(key)
    except Exception:
        # An orphan photo is a storage cost, not a reason to fail the request.
        logger.warning("rental_inspection_photo_orphaned")
