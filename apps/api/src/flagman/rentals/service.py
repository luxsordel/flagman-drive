"""Rentals for clients: reserve, inspect, open, drive, finish and pay (docs/02-architecture.md).

Every change runs in one transaction with the car's status (fleet) and holds the rental row, so
a request, the timeout job and a parallel request of the same client never act on one rental
at once. A command to the car and a payment are the exceptions: nothing is held while the car
or the provider thinks, the rental is marked busy instead (docs/adr/0022, docs/adr/0024).
"""

import asyncio
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings, TripSettings
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
    CarCommandRejectedError,
    CarNotFoundError,
    CarTransition,
    Gear,
    range_left_km,
)
from flagman.geo import service as geo
from flagman.identity import service as identity
from flagman.kyc import service as kyc
from flagman.kyc.domain import KycStatus
from flagman.notifications import service as notifications
from flagman.notifications.domain import Notification, NotificationEvent
from flagman.payments import service as payments
from flagman.payments.domain import (
    NoUsableCardError,
    OperationKind,
    OperationStatus,
    PaymentOperation,
    PaymentsUnavailableError,
)
from flagman.pricing import service as pricing
from flagman.pricing.domain import (
    IntervalKind,
    Quote,
    TariffNotFoundError,
    TariffPrices,
    TariffVersion,
    calculate,
)
from flagman.rentals import repository
from flagman.rentals.domain import (
    SIGNED_URL_TTL,
    ActiveRentalExistsError,
    CancelReason,
    CardRequiredError,
    ChargeRetryRules,
    ChargeStatus,
    ClientBlockedError,
    CommandRules,
    DamageSide,
    FinishCheck,
    FinishRules,
    Inspection,
    InspectionPhotoNotFoundError,
    InspectionPhotoTooLargeError,
    InspectionPhotoTypeUnsupportedError,
    InspectionStage,
    KycNotApprovedError,
    OutstandingDebtError,
    ParkCheck,
    PaymentHoldDeclinedError,
    PaymentOutcomeUnknownError,
    PlannedPayment,
    Receipt,
    ReceiptCard,
    ReceiptNotFoundError,
    Rental,
    RentalBusyError,
    RentalCommand,
    RentalNotFoundError,
    RentalStatus,
    RentalTransition,
    RentalTransitionNotAllowedError,
    ReservationExpiredError,
    ReservationTerms,
    SettlementStep,
    TripWarning,
    TripWarningRules,
    apply_transition,
    debt_key,
    ensure_command_allowed,
    ensure_finish_allowed,
    ensure_near_car,
    ensure_toggle_allowed,
    hold_key,
    lock_refusal,
    minutes_left,
    next_charge_attempt,
    normalize_damage_comment,
    payment_key,
    plan_settlement,
    rental_intervals,
    reservation_deadlines,
    trip_warnings,
)
from flagman.rentals.repository import (
    ActiveRentalTakenError,
    ChargeState,
    InspectionRecord,
    NewRental,
)

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
# A client paying a debt by hand after changing the card; more is a script hammering the bank.
PAY_DEBT_LIMIT = Limit("rental-pay-debt-user", 10, _HOUR)

# The command holds the rental a little longer than telematics may take to answer it.
_COMMAND_HOLD_MARGIN = timedelta(seconds=5)
# The first opening holds the trip's money before the command: the provider's time on top.
_PAYMENT_HOLD_MARGIN = timedelta(seconds=15)
# An unknown payment outcome is tried again this soon, with the same keys (docs/adr/0024).
_PENDING_RETRY = timedelta(minutes=1)
_CHARGE_BATCH = 50
_PARKED_BATCH = 200
_EXPIRY_BATCH = 100
# A reminder still waiting for a retry must not reach a client whose reservation is over.
_RESERVATION_REMINDERS = (NotificationEvent.RESERVATION_EXPIRING,)


@dataclass(frozen=True)
class SettlementRules:
    retry: ChargeRetryRules
    # An unknown outcome is retried this soon, a declined debt by `retry`.
    pending_retry: timedelta = _PENDING_RETRY


def settlement_rules(settings: TripSettings) -> SettlementRules:
    return SettlementRules(
        retry=ChargeRetryRules(
            max_attempts=settings.rental_charge_max_attempts,
            interval=timedelta(seconds=settings.rental_charge_retry_interval_seconds),
        )
    )


@dataclass(frozen=True)
class RentalRules:
    max_wait: timedelta
    availability: AvailabilityRules
    commands: CommandRules
    command_hold: timedelta
    finish: FinishRules
    warnings: TripWarningRules
    settlement: SettlementRules


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
        + _COMMAND_HOLD_MARGIN
        + _PAYMENT_HOLD_MARGIN,
        finish=FinishRules(
            max_state_age=timedelta(seconds=settings.rental_finish_max_state_age_seconds)
        ),
        warnings=TripWarningRules(
            low_battery_pct=settings.rental_low_battery_pct,
            low_range_km=settings.rental_low_range_km,
        ),
        settlement=settlement_rules(settings),
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
    # The cost so far by the server's clock; the receipt's once the rental has one.
    cost: Quote = field(default_factory=lambda: Quote(lines=(), total=0))
    warnings: tuple[TripWarning, ...] = ()
    receipt: Receipt | None = None
    after_inspection_submitted: bool = False


@dataclass(frozen=True)
class Debt:
    rental_id: UUID
    # Kopecks still owed: the receipt's total less what has been taken already.
    amount: int
    ended_at: datetime | None


async def _current_tariff(db: AsyncSession, car: Car) -> TariffVersion:
    """Raises CarNotFoundError for a model without a tariff: such a car is not on the map either."""
    try:
        return await pricing.current_version(db, car.model_id)
    except TariffNotFoundError as error:
        raise CarNotFoundError from error


async def _issue_receipt(
    db: AsyncSession, ended: Rental, now: datetime, settle_at: datetime, *, receipt_always: bool
) -> Receipt | None:
    """Issues the receipt of a rental that has just ended and leaves its money to settle.

    A cancelled reservation gets a receipt only for a paid wait; a hold is released either way.
    """
    tariff = await pricing.get_version(db, ended.tariff_version_id)
    intervals = await repository.get_intervals(db, ended.id)
    quote = calculate(rental_intervals(ended, intervals, now), tariff.prices)
    receipt = None
    if quote.total or receipt_always:
        card = (
            await payments.card_of(db, ended.hold_id)
            if ended.hold_id
            else await payments.default_card(db, ended.user_id)
        )
        receipt = Receipt(
            rental_id=ended.id,
            tariff_version_id=tariff.id,
            quote=quote,
            card=ReceiptCard(brand=card.brand.value, last4=card.last4) if card else None,
            issued_at=now,
        )
        await repository.add_receipt(db, receipt)
    if quote.total or ended.hold_id:
        await repository.set_charge(
            db, ended.id, ChargeState(ChargeStatus.PENDING, 0, settle_at), now
        )
    return receipt


async def _expire(db: AsyncSession, rental: Rental, now: datetime) -> Rental:
    status = apply_transition(rental.status, RentalTransition.EXPIRE)
    # The wait is billed until the deadline: a late job or a late look must not cost the client.
    ended = replace(
        rental, status=status, cancel_reason=CancelReason.EXPIRED, ended_at=rental.expires_at
    )
    await repository.cancel(db, ended, now)
    await fleet.change_status(db, rental.car_id, CarTransition.CANCEL_RESERVATION, now)
    # The charge job takes the money within a minute: an expiry happens in the timeout job and
    # inside the transaction of any request to the rental, and none of them can pay after it.
    receipt = await _issue_receipt(db, ended, now, now, receipt_always=False)
    logger.info("rental_reservation_expired", rental_id=str(rental.id))
    await notifications.withdraw(db, rental.id, _RESERVATION_REMINDERS, now)
    amount = receipt.quote.total if receipt else 0
    notification = Notification(
        NotificationEvent.RESERVATION_EXPIRED, rental.id, {"amount": amount}
    )
    await notifications.notify_client(db, rental.user_id, notification, now)
    if receipt is None and rental.hold_id is None:
        return ended
    # The caller answers with this rental: it must show the money as owed, as the row does.
    return replace(ended, charge_status=ChargeStatus.PENDING, charge_attempts=0, charge_next_at=now)


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


async def get_client_rental(db: AsyncSession, user_id: UUID, rental_id: UUID) -> Rental | None:
    """For the support module: the client's own rental, None for another client's or a missing one.

    Runs in the caller's transaction.
    """
    return await repository.get_rental(db, rental_id, user_id)


async def get_client_active_rental(db: AsyncSession, user_id: UUID, now: datetime) -> Rental | None:
    """For the support module: the client's current rental, if any; in the caller's transaction.

    A reservation past its end is cancelled here, as on any other access (docs/adr/0022): an
    incident must not get a car the client no longer holds.
    """
    rental = await repository.get_active_rental(db, user_id, for_update=True)
    if rental is None:
        return None
    rental = await _expire_if_due(db, rental, now)
    return rental if rental.is_active else None


async def get_rentals(db: AsyncSession, rental_ids: Collection[UUID]) -> dict[UUID, Rental]:
    """For the support module: the rentals a page of conversations is about, in one query."""
    if not rental_ids:
        return {}
    return {rental.id: rental for rental in await repository.get_rentals(db, rental_ids)}


async def remind_expiring_reservations(db: AsyncSession, now: datetime, ahead: timedelta) -> int:
    """Queues «the reservation ends soon» for every reservation ending within `ahead`; runs every
    minute in the worker. A reservation is reminded once however often this runs (the dedup key).
    Returns how many reservations it looked at."""
    async with db.begin():
        rentals = await repository.get_reservations_ending(db, now, now + ahead)
        for rental in rentals:
            notification = Notification(
                NotificationEvent.RESERVATION_EXPIRING,
                rental.id,
                {"minutes_left": minutes_left(rental.expires_at, now)},
            )
            await notifications.notify_client(
                db, rental.user_id, notification, now, until=rental.expires_at
            )
    return len(rentals)


def _was_expired(rental: Rental) -> bool:
    return rental.cancel_reason is CancelReason.EXPIRED


_DRIVING = (RentalStatus.IN_TRIP, RentalStatus.PARKED)
_ENDED_TRIP = (RentalStatus.FINISHED, RentalStatus.PAID, RentalStatus.PAYMENT_FAILED)
# A finished trip waits for its money; a failed payment is a debt until it is paid.
_OWING = (RentalStatus.FINISHED, RentalStatus.PAYMENT_FAILED)
_TOGGLES = {
    RentalTransition.PARK: (RentalStatus.PARKED, IntervalKind.PARKED),
    RentalTransition.RESUME: (RentalStatus.IN_TRIP, IntervalKind.DRIVING),
}


# Settlement: what a rental cost is taken from the card (docs/adr/0024). Payments run outside
# any transaction of ours, each with its own key, so a repeat never takes the money twice.


@dataclass(frozen=True)
class _Settled:
    status: ChargeStatus
    attempts: int
    # Kopecks still owed after this try.
    owed: int
    # The provider did not say: the same key goes again soon.
    unknown: bool = False


def _taken(operation: PaymentOperation | None) -> int:
    if operation is None or operation.status is not OperationStatus.SUCCEEDED:
        return 0
    return 0 if operation.kind is OperationKind.VOID else operation.amount


def _hold_of(rental: Rental) -> UUID:
    if rental.hold_id is None:
        raise RuntimeError("a hold step for a rental without a hold")
    return rental.hold_id


async def _pay(
    db: AsyncSession, gw: payments.Gateway, rental: Rental, planned: PlannedPayment, now: datetime
) -> PaymentOperation | None:
    """None when the client has no usable card left: as good as a decline."""
    key = payment_key(rental.id, planned.step)
    try:
        if planned.step is SettlementStep.VOID:
            return await payments.void(db, gw, _hold_of(rental), key, now)
        amount = planned.amount or 0
        if planned.step is SettlementStep.CAPTURE:
            return await payments.capture(
                db, gw, _hold_of(rental), payments.Payment(amount, key), now
            )
        return await payments.charge(db, gw, rental.user_id, payments.Payment(amount, key), now)
    except NoUsableCardError:
        return None


async def _known_hold(
    db: AsyncSession, gw: payments.Gateway, rental: Rental, now: datetime
) -> PaymentOperation | None:
    """The opening's hold, asked again while its outcome is unknown: the client may have cancelled
    after a 503, and a hold the provider did make must still be released."""
    if rental.hold_id is None:
        return None
    hold = await payments.get_operation(db, rental.hold_id)
    if hold.is_final:
        return hold
    # The same key: a hold the provider made is found, never made twice.
    again = payments.Payment(hold.amount, hold.idempotency_key)
    return await payments.hold(db, gw, hold.user_id, again, now)


async def _settle_first(
    db: AsyncSession, gw: payments.Gateway, rental: Rental, total: int, now: datetime
) -> _Settled:
    hold = await _known_hold(db, gw, rental, now)
    if hold is not None and not hold.is_final:
        return _Settled(ChargeStatus.PENDING, rental.charge_attempts, total, unknown=True)
    held = hold.amount if hold is not None and hold.status is OperationStatus.SUCCEEDED else None
    taken = 0
    for planned in plan_settlement(total, held):
        operation = await _pay(db, gw, rental, planned, now)
        taken += _taken(operation)
        if operation is None or operation.status is OperationStatus.DECLINED:
            if planned.step is SettlementStep.VOID:
                # Nothing is owed: the provider lets the hold lapse by itself, and a debt of zero
                # would only block the client's next reservation.
                logger.warning("rental_hold_not_released", rental_id=str(rental.id))
                return _Settled(ChargeStatus.PAID, rental.charge_attempts, 0)
            if planned.step is SettlementStep.CAPTURE:
                # The debt is charged to the card in full, so the hold must not stay on it too.
                await _pay(db, gw, rental, PlannedPayment(SettlementStep.VOID, None), now)
            return _Settled(ChargeStatus.FAILED, rental.charge_attempts, total - taken)
        if not operation.is_final:
            return _Settled(
                ChargeStatus.PENDING, rental.charge_attempts, total - taken, unknown=True
            )
    return _Settled(ChargeStatus.PAID, rental.charge_attempts, 0)


async def _taken_before(db: AsyncSession, rental: Rental, attempts: int) -> int:
    """What the rental's payments took so far, the first settlement and `attempts` debt tries."""
    first = (SettlementStep.CAPTURE, SettlementStep.EXTRA, SettlementStep.CHARGE)
    keys = [payment_key(rental.id, step) for step in first]
    keys += [debt_key(rental.id, attempt) for attempt in range(1, attempts + 1)]
    return sum([_taken(await payments.find_operation(db, key)) for key in keys])


async def _retry_debt(
    db: AsyncSession, gw: payments.Gateway, rental: Rental, total: int, now: datetime
) -> _Settled:
    attempt = rental.charge_attempts
    last = await payments.find_operation(db, debt_key(rental.id, attempt)) if attempt else None
    # A try the provider has not answered goes again with its own key; only a declined one,
    # or one that never reached the provider, makes room for the next.
    if last is None or last.status is OperationStatus.DECLINED:
        attempt += 1
    owed = total - await _taken_before(db, rental, attempt - 1)
    if owed <= 0:
        return _Settled(ChargeStatus.PAID, attempt, 0)
    try:
        operation = await payments.charge(
            db, gw, rental.user_id, payments.Payment(owed, debt_key(rental.id, attempt)), now
        )
    except NoUsableCardError:
        return _Settled(ChargeStatus.FAILED, attempt, owed)
    if operation.status is OperationStatus.SUCCEEDED:
        return _Settled(ChargeStatus.PAID, attempt, 0)
    return _Settled(
        ChargeStatus.FAILED, attempt, owed, unknown=operation.status is not OperationStatus.DECLINED
    )


async def _record(
    db: AsyncSession, rental_id: UUID, settled: _Settled, rules: SettlementRules, now: datetime
) -> None:
    async with db.begin():
        rental = await repository.get_rental_by_id(db, rental_id, for_update=True)
        if rental is None:
            return
        next_at: datetime | None = None
        if settled.unknown:
            next_at = now + rules.pending_retry
        elif settled.status is ChargeStatus.FAILED:
            next_at = next_charge_attempt(settled.attempts, rules.retry, now)
        state = ChargeState(settled.status, settled.attempts, next_at)
        await repository.set_charge(db, rental.id, state, now)
        transition = None
        if settled.status is ChargeStatus.PAID and rental.status in _OWING:
            transition = RentalTransition.MARK_PAID
        elif settled.status is ChargeStatus.FAILED and rental.status is RentalStatus.FINISHED:
            transition = RentalTransition.MARK_PAYMENT_FAILED
        if transition is not None:
            status = apply_transition(rental.status, transition)
            await repository.set_status(db, rental.id, status, now)
        if (
            settled.status is ChargeStatus.FAILED
            and rental.charge_status is not ChargeStatus.FAILED
        ):
            notification = Notification(
                NotificationEvent.PAYMENT_FAILED, rental.id, {"amount": settled.owed}
            )
            await notifications.notify_client(db, rental.user_id, notification, now)
    logger.info("rental_charge_settled", rental_id=str(rental_id), charge_status=settled.status)


async def settle(  # noqa: PLR0913 - `debt` only tells the callers apart
    db: AsyncSession,
    gw: payments.Gateway,
    rental_id: UUID,
    rules: SettlementRules,
    now: datetime,
    *,
    debt: bool = True,
) -> None:
    """Takes what the rental owes: the first settlement with its fixed keys while its outcome is
    not known, then one more debt try once it was declined. Does nothing for a paid rental.

    Without `debt` a declined rental is left alone: a repeated finish must not be a way around
    the limit of the debt payments, nor spend the job's tries.
    """
    async with db.begin():
        rental = await repository.get_rental_by_id(db, rental_id)
        receipt = await repository.get_receipt(db, rental_id)
    owing = (ChargeStatus.PENDING, ChargeStatus.FAILED) if debt else (ChargeStatus.PENDING,)
    if rental is None or rental.charge_status not in owing:
        return
    total = receipt.quote.total if receipt else 0
    if rental.charge_status is ChargeStatus.PENDING:
        settled = await _settle_first(db, gw, rental, total, now)
    else:
        settled = await _retry_debt(db, gw, rental, total, now)
    await _record(db, rental.id, settled, rules, now)


async def retry_charges(
    db: AsyncSession, gw: payments.Gateway, rules: SettlementRules, now: datetime
) -> int:
    """The charge job: settles the rentals whose outcome is unknown and the debts whose next try
    is due. Returns how many it tried."""
    async with db.begin():
        due = await repository.lock_due_charges(db, now, _CHARGE_BATCH)
        for rental in due:
            # Claimed before paying: a crash in the middle leaves it to a later run.
            claimed = ChargeState(
                rental.charge_status, rental.charge_attempts, now + rules.pending_retry
            )
            await repository.set_charge(db, rental.id, claimed, now)
    for rental in due:
        try:
            await settle(db, gw, rental.id, rules, now)
        except Exception:
            # One rental the provider chokes on must not hold up the others. It is a bug or an
            # outage, not an answer, so it waits as long as a declined debt instead of every run.
            logger.exception("rental_charge_retry_failed", rental_id=str(rental.id))
            await _postpone(db, rental.id, now + rules.retry.interval, now)
    return len(due)


async def _postpone(db: AsyncSession, rental_id: UUID, next_at: datetime, now: datetime) -> None:
    async with db.begin():
        rental = await repository.get_rental_by_id(db, rental_id, for_update=True)
        if rental is not None and rental.charge_status in (
            ChargeStatus.PENDING,
            ChargeStatus.FAILED,
        ):
            state = ChargeState(rental.charge_status, rental.charge_attempts, next_at)
            await repository.set_charge(db, rental.id, state, now)


async def owed(db: AsyncSession, rental: Rental) -> int:
    async with db.begin():
        receipt = await repository.get_receipt(db, rental.id)
    total = receipt.quote.total if receipt else 0
    return max(0, total - await _taken_before(db, rental, rental.charge_attempts))


async def resume_driven_off(db: AsyncSession, now: datetime) -> int:
    """Puts back on the driving price the parked trips whose car has its ignition on: parking
    checks the car only when it is switched on (docs/adr/0024). Returns how many it resumed."""
    resumed = 0
    async with db.begin():
        for trip in await repository.lock_parked_trips(db, _PARKED_BATCH):
            state = (await fleet.get_car(db, trip.car_id)).state
            if state is None or not state.ignition_on:
                continue
            status = apply_transition(trip.status, RentalTransition.RESUME)
            await repository.close_interval(db, trip.id, now)
            await repository.open_interval(db, trip.id, IntervalKind.DRIVING, now)
            await repository.set_status(db, trip.id, status, now)
            logger.info("rental_trip_resumed_by_car", rental_id=str(trip.id))
            resumed += 1
    return resumed


async def watch_trip_battery(db: AsyncSession, low_battery_pct: int, now: datetime) -> int:
    """Warns the clients whose car has gone below the threshold; runs every minute in the worker.
    The dedup key gives one push per trip however often this runs. Returns how many it warned."""
    warned = 0
    async with db.begin():
        for trip in await repository.get_trips(db):
            car = await fleet.get_car(db, trip.car_id)
            pct = car.state.battery_pct if car.state else None
            if pct is None or pct >= low_battery_pct:
                continue
            notification = Notification(
                NotificationEvent.TRIP_LOW_BATTERY, trip.id, {"battery_pct": pct}
            )
            await notifications.notify_client(db, trip.user_id, notification, now)
            warned += 1
    return warned


@dataclass(frozen=True)
class RentalService:
    db: AsyncSession
    limiter: RateLimiter
    rules: RentalRules
    # The trip starts when the car has opened, which is seconds after the request came.
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    # None when PAYMENTS_PROVIDER is not set: a trip cannot start then, settling waits for the job.
    gateway: payments.Gateway | None = None

    def _gateway(self) -> payments.Gateway:
        if self.gateway is None:
            raise PaymentsUnavailableError
        return self.gateway

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

    async def _view(self, rental: Rental, now: datetime) -> RentalView:
        car = await fleet.get_car(self.db, rental.car_id)
        before = await repository.get_inspection(self.db, rental.id, InspectionStage.BEFORE)
        after = await repository.get_inspection(self.db, rental.id, InspectionStage.AFTER)
        tariff = await pricing.get_version(self.db, rental.tariff_version_id)
        receipt = await repository.get_receipt(self.db, rental.id)
        if receipt is not None:
            cost = receipt.quote
        elif rental.ended_at is not None:
            # Ended without a receipt: nothing was billed, the whole wait was free.
            cost = Quote(lines=(), total=0)
        else:
            intervals = await repository.get_intervals(self.db, rental.id)
            cost = calculate(rental_intervals(rental, intervals, now), tariff.prices)
        state = car.state
        warnings: tuple[TripWarning, ...] = ()
        if rental.status in _DRIVING and state is not None:
            range_km = range_left_km(car.model.range_km, state.battery_pct)
            warnings = trip_warnings(state.battery_pct, range_km, self.rules.warnings)
        return RentalView(
            rental=rental,
            car=car,
            prices=tariff.prices,
            inspection_submitted=before is not None and before.inspection.is_submitted,
            cost=cost,
            warnings=warnings,
            receipt=receipt,
            after_inspection_submitted=after is not None and after.inspection.is_submitted,
        )

    async def _bill(self, ended: Rental, now: datetime, *, receipt_always: bool) -> Receipt | None:
        """Settled right away by the caller; the charge job is only the fallback."""
        settle_at = now + self.rules.settlement.pending_retry
        return await _issue_receipt(self.db, ended, now, settle_at, receipt_always=receipt_always)

    async def _settle(self, rental_id: UUID) -> None:
        """Right away when payments are configured; the charge job does it otherwise."""
        if self.gateway is not None:
            await settle(
                self.db, self.gateway, rental_id, self.rules.settlement, self.clock(), debt=False
            )

    async def reserve(self, user_id: UUID, car_id: UUID, now: datetime) -> RentalView:
        await self.limiter.hit(RESERVE_LIMIT, str(user_id))
        async with self.db.begin():
            await self._ensure_admitted(user_id)
            if await repository.has_debt(self.db, user_id):
                raise OutstandingDebtError
            if not await payments.has_usable_card(self.db, user_id, now):
                raise CardRequiredError
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
            return await self._view(await self._lock(user_id, rental_id, now), now)

    async def current(self, user_id: UUID, now: datetime) -> RentalView | None:
        """The client's active rental, so the app can come back to it after a restart."""
        async with self.db.begin():
            rental = await repository.get_active_rental(self.db, user_id, for_update=True)
            if rental is None:
                return None
            rental = await _expire_if_due(self.db, rental, now)
            return await self._view(rental, now) if rental.is_active else None

    async def cancel(self, user_id: UUID, rental_id: UUID, now: datetime) -> RentalView:
        """Cancelling a cancelled reservation answers it as it is: a retry after a lost answer.

        A paid wait is charged, and a hold of a failed opening released.
        """
        to_settle = False
        async with self.db.begin():
            rental = await self._lock(user_id, rental_id, now)
            if rental.status is not RentalStatus.CANCELLED:
                if rental.is_busy(now):
                    # The car may be opening for the client right now.
                    raise RentalBusyError
                status = apply_transition(rental.status, RentalTransition.CANCEL)
                ended = replace(
                    rental, status=status, cancel_reason=CancelReason.CLIENT, ended_at=now
                )
                await repository.cancel(self.db, ended, now)
                await notifications.withdraw(self.db, rental.id, _RESERVATION_REMINDERS, now)
                await fleet.change_status(
                    self.db, rental.car_id, CarTransition.CANCEL_RESERVATION, now
                )
                await self._bill(ended, now, receipt_always=False)
                to_settle = bool(await repository.get_receipt(self.db, rental.id) or rental.hold_id)
                logger.info("rental_reservation_cancelled", rental_id=str(rental.id))
        if to_settle:
            await self._settle(rental_id)
        return await self.get(user_id, rental_id, self.clock())

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
            if request.command is RentalCommand.OPEN:
                await self._hold(rental, now)
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
                await repository.open_interval(self.db, rental.id, IntervalKind.DRIVING, done_at)
                await notifications.withdraw(self.db, rental.id, _RESERVATION_REMINDERS, done_at)
                logger.info("rental_trip_started", rental_id=str(rental.id))
            else:
                await repository.release_command(self.db, rental.id, hold_until, done_at)
            rental = await repository.get_rental(self.db, rental.id, user_id) or rental
            return await self._view(rental, done_at)

    async def _hold(self, rental: Rental, now: datetime) -> None:
        """Holds the trip's money before the car opens; raises when it is not held.

        A declined hold is final for its key, so a retry after the client changed the card takes
        the next key; an unknown outcome is retried with the same one. A hold already taken by
        an opening that then failed is kept for the next one.
        """
        if rental.hold_id is not None:
            taken = await payments.get_operation(self.db, rental.hold_id)
            if taken.status is OperationStatus.SUCCEEDED:
                return
        gw = self._gateway()
        tariff = await pricing.get_version(self.db, rental.tariff_version_id)
        attempt = 1
        while (
            previous := await payments.find_operation(self.db, hold_key(rental.id, attempt))
        ) is not None and previous.status is OperationStatus.DECLINED:
            attempt += 1
        payment = payments.Payment(tariff.prices.hold_amount, hold_key(rental.id, attempt))
        try:
            operation = await payments.hold(self.db, gw, rental.user_id, payment, now)
        except NoUsableCardError as error:
            raise CardRequiredError from error
        if operation.status is OperationStatus.DECLINED:
            raise PaymentHoldDeclinedError
        async with self.db.begin():
            # Kept while unknown too: a cancel or the expiry then settles it, and releases a hold
            # the provider did make.
            await repository.set_hold(self.db, rental.id, operation.id, self.clock())
        if operation.status is not OperationStatus.SUCCEEDED:
            raise PaymentOutcomeUnknownError
        logger.info("rental_hold_taken", rental_id=str(rental.id))

    async def park(self, user_id: UUID, rental_id: UUID, now: datetime) -> RentalView:
        return await self._toggle(user_id, rental_id, RentalTransition.PARK, now)

    async def resume(self, user_id: UUID, rental_id: UUID, now: datetime) -> RentalView:
        return await self._toggle(user_id, rental_id, RentalTransition.RESUME, now)

    async def _toggle(
        self, user_id: UUID, rental_id: UUID, transition: RentalTransition, now: datetime
    ) -> RentalView:
        """Parked time costs the parked price from now on; a repeat answers the rental as it is."""
        wanted, kind = _TOGGLES[transition]
        # Each switch adds an interval row, so it is limited like the car commands.
        await self.limiter.hit(COMMAND_LIMIT, str(user_id))
        async with self.db.begin():
            rental = await self._lock(user_id, rental_id, now)
            if rental.status is not wanted:
                status = apply_transition(rental.status, transition)
                state = (await fleet.get_car(self.db, rental.car_id)).state
                check = ParkCheck(
                    recorded_at=state.recorded_at if state else None,
                    ignition_on=state.ignition_on if state else False,
                )
                ensure_toggle_allowed(rental, transition, check, self.rules.finish, now)
                await repository.close_interval(self.db, rental.id, now)
                await repository.open_interval(self.db, rental.id, kind, now)
                await repository.set_status(self.db, rental.id, status, now)
                rental = replace(rental, status=status)
                logger.info("rental_trip_toggled", rental_id=str(rental.id), status=status)
            return await self._view(rental, now)

    async def finish(
        self, commander: fleet.RentalCarCommander, user_id: UUID, rental_id: UUID, now: datetime
    ) -> RentalView:
        """Checks the car, locks it, ends the rental with a receipt and takes the money.

        A repeat after the rental has ended checks nothing again: it settles what is left with
        the same payment keys and answers the rental, so nothing is charged twice.
        """
        await self.limiter.hit(COMMAND_LIMIT, str(user_id))
        async with self.db.begin():
            rental = await self._lock(user_id, rental_id, now)
            driving = rental.status in _DRIVING
            if driving:
                after = await repository.get_inspection(self.db, rental.id, InspectionStage.AFTER)
                state = (await fleet.get_car(self.db, rental.car_id)).state
                in_zone = state is not None and await geo.can_end_rental_at(self.db, state.location)
                gear = state.gear if state else None
                check = FinishCheck(
                    recorded_at=state.recorded_at if state else None,
                    ignition_on=state.ignition_on if state else False,
                    in_end_zone=in_zone,
                    headlights_on=state.headlights_on if state else None,
                    in_park=None if gear is None else gear is Gear.PARK,
                )
                submitted = after is not None and after.inspection.is_submitted
                ensure_finish_allowed(rental, submitted, check, self.rules.finish, now)
                hold_until = now + self.rules.command_hold
                await repository.set_command_until(self.db, rental.id, hold_until, now)
            elif rental.status not in _ENDED_TRIP:
                raise RentalTransitionNotAllowedError
        if driving:
            await self._lock_car(commander, rental, hold_until, now)
            await self._end_trip(rental, hold_until)
        await self._settle(rental_id)
        return await self.get(user_id, rental_id, self.clock())

    async def _lock_car(
        self,
        commander: fleet.RentalCarCommander,
        rental: Rental,
        hold_until: datetime,
        now: datetime,
    ) -> None:
        car_request = fleet.RentalCommandRequest(
            car_id=rental.car_id,
            command=CarCommand.CLOSE,
            client_id=rental.user_id,
            rental_id=rental.id,
        )
        try:
            await commander.send(car_request, now)
        except Exception as error:
            async with self.db.begin():
                await repository.release_command(self.db, rental.id, hold_until, self.clock())
            if isinstance(error, CarCommandRejectedError):
                raise lock_refusal(error.reason) from error
            raise

    async def _end_trip(self, rental: Rental, hold_until: datetime) -> None:
        done_at = self.clock()
        async with self.db.begin():
            locked = await repository.get_rental(
                self.db, rental.id, rental.user_id, for_update=True
            )
            if locked is None:
                raise RentalNotFoundError
            if locked.status not in _DRIVING:
                # A parallel finish got here first.
                await repository.release_command(self.db, rental.id, hold_until, done_at)
                return
            status = apply_transition(locked.status, RentalTransition.FINISH)
            await repository.close_interval(self.db, rental.id, done_at)
            await repository.finish(self.db, rental.id, status, done_at)
            await fleet.change_status(self.db, rental.car_id, CarTransition.END_RENTAL, done_at)
            ended = replace(locked, status=status, ended_at=done_at)
            receipt = await self._bill(ended, done_at, receipt_always=True)
            total = receipt.quote.total if receipt else 0
            notification = Notification(
                NotificationEvent.TRIP_FINISHED, rental.id, {"total": total}
            )
            await notifications.notify_client(self.db, rental.user_id, notification, done_at)
        logger.info("rental_finished", rental_id=str(rental.id))

    async def receipt(self, user_id: UUID, rental_id: UUID) -> Receipt:
        async with self.db.begin():
            rental = await repository.get_rental(self.db, rental_id, user_id)
            if rental is None:
                raise RentalNotFoundError
            receipt = await repository.get_receipt(self.db, rental.id)
        if receipt is None:
            raise ReceiptNotFoundError
        return receipt

    async def debts(self, user_id: UUID) -> list[Debt]:
        async with self.db.begin():
            rentals = await repository.get_debts(self.db, user_id)
        return [Debt(r.id, await owed(self.db, r), r.ended_at) for r in rentals]

    async def pay_debt(self, user_id: UUID, rental_id: UUID, now: datetime) -> RentalView:
        """One more try at the debt, e.g. after the client bound another card."""
        await self.limiter.hit(PAY_DEBT_LIMIT, str(user_id))
        gw = self._gateway()
        async with self.db.begin():
            rental = await repository.get_rental(self.db, rental_id, user_id)
        if rental is None:
            raise RentalNotFoundError
        if rental.charge_status is ChargeStatus.FAILED:
            await settle(self.db, gw, rental.id, self.rules.settlement, now)
        return await self.get(user_id, rental_id, self.clock())


async def _delete_quietly(storage: ObjectStorage, key: str) -> None:
    try:
        await storage.delete(key)
    except Exception:
        # An orphan photo is a storage cost, not a reason to fail the request.
        logger.warning("rental_inspection_photo_orphaned")
