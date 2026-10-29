"""Rentals: the status machine, reservation terms, inspections and car commands. No I/O here.

The machine is drawn in docs/02-architecture.md; the reasons behind the reservation timeout are
in docs/adr/0022.
"""

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus

from flagman.core.errors import AppError
from flagman.pricing.domain import Interval, IntervalKind, Quote


class RentalStatus(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    RESERVED = "reserved"
    IN_TRIP = "in_trip"
    PARKED = "parked"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    PAID = "paid"
    PAYMENT_FAILED = "payment_failed"


class RentalTransition(StrEnum):
    CANCEL = "cancel"
    EXPIRE = "expire"
    START_TRIP = "start_trip"
    PARK = "park"
    RESUME = "resume"
    FINISH = "finish"
    FORCE_FINISH = "force_finish"
    MARK_PAID = "mark_paid"
    MARK_PAYMENT_FAILED = "mark_payment_failed"


class ChargeStatus(StrEnum):
    """Where the money of a rental stands; values are stored as is (docs/adr/0024).

    A rental with nothing to charge has none. `failed` is a debt whatever the rental's status:
    a cancelled reservation whose paid wait was declined owes as much as a finished trip.
    """

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class CancelReason(StrEnum):
    """Why a reservation ended without a trip; values are stored as is."""

    CLIENT = "client"
    EXPIRED = "expired"


@dataclass(frozen=True)
class _Rule:
    sources: frozenset[RentalStatus]
    target: RentalStatus


_DRIVING = frozenset({RentalStatus.IN_TRIP, RentalStatus.PARKED})

_RULES: dict[RentalTransition, _Rule] = {
    RentalTransition.CANCEL: _Rule(frozenset({RentalStatus.RESERVED}), RentalStatus.CANCELLED),
    RentalTransition.EXPIRE: _Rule(frozenset({RentalStatus.RESERVED}), RentalStatus.CANCELLED),
    RentalTransition.START_TRIP: _Rule(frozenset({RentalStatus.RESERVED}), RentalStatus.IN_TRIP),
    RentalTransition.PARK: _Rule(frozenset({RentalStatus.IN_TRIP}), RentalStatus.PARKED),
    RentalTransition.RESUME: _Rule(frozenset({RentalStatus.PARKED}), RentalStatus.IN_TRIP),
    RentalTransition.FINISH: _Rule(_DRIVING, RentalStatus.FINISHED),
    # By an operator, with a reason and an audit event (#112): no client checks behind it.
    RentalTransition.FORCE_FINISH: _Rule(_DRIVING, RentalStatus.FINISHED),
    # A debt is paid later too, so a failed payment may still turn into a paid one.
    RentalTransition.MARK_PAID: _Rule(
        frozenset({RentalStatus.FINISHED, RentalStatus.PAYMENT_FAILED}), RentalStatus.PAID
    ),
    RentalTransition.MARK_PAYMENT_FAILED: _Rule(
        frozenset({RentalStatus.FINISHED}), RentalStatus.PAYMENT_FAILED
    ),
}

# A client has at most one of these at a time, a car too: the database enforces both.
ACTIVE_STATUSES = frozenset({RentalStatus.RESERVED, *_DRIVING})


class RentalNotFoundError(AppError):
    """Also for another client's rental: its existence is not told."""

    status = HTTPStatus.NOT_FOUND
    slug = "rental-not-found"
    title = "Rental not found"


class RentalTransitionNotAllowedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "rental-transition-not-allowed"
    title = "The rental cannot do this in its current status"


class ActiveRentalExistsError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "active-rental-exists"
    title = "Finish or cancel the current rental first"


class KycNotApprovedError(AppError):
    status = HTTPStatus.FORBIDDEN
    slug = "kyc-not-approved"
    title = "Rentals need approved documents"


class ClientBlockedError(AppError):
    status = HTTPStatus.FORBIDDEN
    slug = "client-blocked"
    title = "The account is blocked; contact support"


class ReservationExpiredError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "reservation-expired"
    title = "The reservation has expired"


class RentalBusyError(AppError):
    """Another command of this rental is still waiting for the car."""

    status = HTTPStatus.CONFLICT
    slug = "rental-command-in-progress"
    title = "The car is still answering the previous command"


class InspectionRequiredError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "inspection-required"
    title = "Submit the inspection of the car first"


class CarLocationUnknownError(AppError):
    """No fresh telemetry: the distance to the car cannot be checked."""

    status = HTTPStatus.CONFLICT
    slug = "car-location-unknown"
    title = "The car's location is not known right now; try again in a minute"


class TooFarFromCarError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "too-far-from-car"
    title = "Come closer to the car"


def apply_transition(status: RentalStatus, transition: RentalTransition) -> RentalStatus:
    rule = _RULES[transition]
    if status not in rule.sources:
        raise RentalTransitionNotAllowedError
    return rule.target


@dataclass(frozen=True)
class ReservationTerms:
    """The tariff's free wait, then the paid one until `max_wait` from the reservation (docs/01)."""

    free_wait: timedelta
    max_wait: timedelta

    def __post_init__(self) -> None:
        if self.free_wait < timedelta(0) or self.max_wait <= timedelta(0):
            raise ValueError("the waits must not be negative and a reservation must last")


@dataclass(frozen=True)
class Rental:
    id: uuid.UUID
    user_id: uuid.UUID
    car_id: uuid.UUID
    status: RentalStatus
    # Fixed at the reservation: a tariff changed later does not change this rental's price.
    tariff_version_id: uuid.UUID
    reserved_at: datetime
    free_wait_until: datetime
    expires_at: datetime
    trip_started_at: datetime | None
    ended_at: datetime | None
    cancel_reason: CancelReason | None
    # Set while a command waits for the car: the reservation is neither expired nor cancelled
    # under it, and a second command is refused (docs/adr/0022).
    command_until: datetime | None
    # The trip's hold, set by the first opening; a reservation that never opened has none.
    hold_id: uuid.UUID | None = None
    charge_status: ChargeStatus | None = None
    # Retries of a declined debt so far, and when the worker tries the next one.
    charge_attempts: int = 0
    charge_next_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_driving(self) -> bool:
        """The client has the car: a reservation is active too, but nobody is on the road yet."""
        return self.status in _DRIVING

    def is_busy(self, now: datetime) -> bool:
        return self.command_until is not None and now < self.command_until

    def is_expired(self, now: datetime) -> bool:
        """A reservation past its end that no command holds; the timeout cancels it."""
        return (
            self.status is RentalStatus.RESERVED
            and now >= self.expires_at
            and not self.is_busy(now)
        )


def minutes_left(expires_at: datetime, now: datetime) -> int:
    """Whole minutes until the end, rounded up: «1 minute» while any of it is left."""
    seconds = (expires_at - now).total_seconds()
    return max(0, -int(-seconds // 60))


def reservation_deadlines(
    reserved_at: datetime, terms: ReservationTerms
) -> tuple[datetime, datetime]:
    """When the free wait ends and when the reservation is cancelled by the timeout.

    A tariff may give more free minutes than a reservation lasts: then all of it is free.
    """
    expires_at = reserved_at + terms.max_wait
    return min(reserved_at + terms.free_wait, expires_at), expires_at


class InspectionStage(StrEnum):
    """Values are stored as is: before the first opening and after the trip (#111)."""

    BEFORE = "before"
    AFTER = "after"


class DamageSide(StrEnum):
    FRONT = "front"
    REAR = "rear"
    LEFT = "left"
    RIGHT = "right"
    ROOF = "roof"
    INTERIOR = "interior"


MIN_INSPECTION_PHOTOS = 4
MAX_INSPECTION_PHOTOS = 8
# Enough for a car that is scratched all over; more is a mistake or abuse of the form.
MAX_DAMAGES = 20
DAMAGE_COMMENT_MAX_LENGTH = 300
SIGNED_URL_TTL = timedelta(minutes=5)

# The inspection before is taken at the reserved car, the one after at the end of the trip.
_INSPECTION_STATUSES = {
    InspectionStage.BEFORE: frozenset({RentalStatus.RESERVED}),
    InspectionStage.AFTER: _DRIVING,
}


class InspectionClosedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "inspection-closed"
    title = "This inspection cannot be changed now"


class InspectionPhotoLimitError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "inspection-photo-limit"
    title = f"An inspection takes at most {MAX_INSPECTION_PHOTOS} photos"


class InspectionDamageLimitError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "inspection-damage-limit"
    title = f"An inspection takes at most {MAX_DAMAGES} damages"


class InspectionIncompleteError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "inspection-incomplete"
    title = f"Take at least {MIN_INSPECTION_PHOTOS} photos of the car"


class InspectionPhotoNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "inspection-photo-not-found"
    title = "Photo not found"


class InspectionPhotoTooLargeError(AppError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    slug = "inspection-photo-too-large"
    title = "The photo is too large"


class InspectionPhotoTypeUnsupportedError(AppError):
    status = HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    slug = "inspection-photo-type-unsupported"
    title = "The photo must be a JPEG, PNG or WebP image"


class PhotoStorageUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "photo-storage-unavailable"
    title = "Photo storage is unavailable"


@dataclass(frozen=True)
class Damage:
    id: uuid.UUID
    side: DamageSide
    comment: str
    created_at: datetime


@dataclass(frozen=True)
class InspectionPhoto:
    id: uuid.UUID
    uploaded_at: datetime


@dataclass(frozen=True)
class Inspection:
    """Photos are evidence: once uploaded, the client neither deletes nor replaces them."""

    stage: InspectionStage
    photos: tuple[InspectionPhoto, ...] = ()
    damages: tuple[Damage, ...] = ()
    submitted_at: datetime | None = None

    @property
    def is_submitted(self) -> bool:
        return self.submitted_at is not None

    def ensure_editable(self, status: RentalStatus) -> None:
        if self.is_submitted or status not in _INSPECTION_STATUSES[self.stage]:
            raise InspectionClosedError

    def ensure_photo_fits(self, status: RentalStatus) -> None:
        self.ensure_editable(status)
        if len(self.photos) >= MAX_INSPECTION_PHOTOS:
            raise InspectionPhotoLimitError

    def ensure_damage_fits(self, status: RentalStatus) -> None:
        self.ensure_editable(status)
        if len(self.damages) >= MAX_DAMAGES:
            raise InspectionDamageLimitError

    def ensure_submittable(self, status: RentalStatus) -> None:
        self.ensure_editable(status)
        if len(self.photos) < MIN_INSPECTION_PHOTOS:
            raise InspectionIncompleteError


def _is_refused_in_comment(char: str) -> bool:
    return char not in {"\n", "\r", "\t"} and unicodedata.category(char).startswith("C")


def normalize_damage_comment(comment: str) -> str:
    """Squeezes the spaces; raises ValueError for control and format characters.

    Operators read the comment next to the photos: invisible characters (bidi overrides,
    zero-width) could make it read differently there, and NUL is refused by Postgres.
    """
    # Line breaks and tabs from a multiline field are spaces like any other.
    if any(_is_refused_in_comment(char) for char in comment):
        raise ValueError("the comment must not contain control or format characters")
    return " ".join(comment.split())


class RentalCommand(StrEnum):
    OPEN = "open"
    CLOSE = "close"


@dataclass(frozen=True)
class CommandRules:
    max_distance_m: float
    # The car's last snapshot must be this recent, or its location is not known.
    max_state_age: timedelta


def ensure_command_allowed(rental: Rental, inspection_submitted: bool, now: datetime) -> None:
    """What the rental itself decides about a command; the car's distance is checked after.

    The rental is not expired here: the caller has cancelled it by the timeout before.
    """
    if not rental.is_active:
        raise RentalTransitionNotAllowedError
    if rental.is_busy(now):
        raise RentalBusyError
    if not inspection_submitted:
        raise InspectionRequiredError


def ensure_near_car(
    state_recorded_at: datetime | None, distance_m: float | None, rules: CommandRules, now: datetime
) -> None:
    """Checks the client's distance to where the car last reported itself.

    The boundary is allowed: exactly `max_distance_m` away still opens the car.
    """
    if state_recorded_at is None or distance_m is None:
        raise CarLocationUnknownError
    if now - state_recorded_at > rules.max_state_age:
        raise CarLocationUnknownError
    if distance_m > rules.max_distance_m:
        raise TooFarFromCarError


# The trip and its end (docs/adr/0024)


class CardRequiredError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "card-required"
    title = "Bind a payment card before renting"


class OutstandingDebtError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "outstanding-debt"
    title = "Pay the debt of an earlier rental first"


class PaymentHoldDeclinedError(AppError):
    """The bank refused the trip's hold: the car stays closed."""

    status = HTTPStatus.PAYMENT_REQUIRED
    slug = "payment-hold-declined"
    title = "The bank declined the hold for the trip; try another card"


class PaymentOutcomeUnknownError(AppError):
    """The provider did not answer; a retry with the same request is safe."""

    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "payment-outcome-unknown"
    title = "The payment provider did not answer; try again in a minute"


class CarStateStaleError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "car-state-stale"
    title = "The car has not reported itself recently; try again in a minute"


class OutsideEndZoneError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "outside-end-zone"
    title = "The rental cannot be finished here; park the car within the zone"


class IgnitionOnError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "ignition-on"
    title = "Turn the ignition off first"


class DoorsOpenError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "doors-open"
    title = "Close the doors first"


class CarLockFailedError(AppError):
    """The car refused to lock for a reason the client cannot fix; `detail` has it."""

    status = HTTPStatus.CONFLICT
    slug = "car-lock-failed"
    title = "The car did not lock; contact support"


class ReceiptNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "receipt-not-found"
    title = "The rental has no receipt"


def is_debt(charge_status: ChargeStatus | None) -> bool:
    return charge_status is ChargeStatus.FAILED


@dataclass(frozen=True)
class TripInterval:
    """A stored driving or parked interval; the current one has no end yet."""

    kind: IntervalKind
    start: datetime
    end: datetime | None


def rental_intervals(
    rental: Rental, stored: tuple[TripInterval, ...], now: datetime
) -> tuple[Interval, ...]:
    """What the rental is billed for: the wait for the client, then the trip's intervals. The
    open one runs until `now`, or until the end of a rental that has ended."""
    end = rental.ended_at or now
    waited_until = max(rental.reserved_at, rental.trip_started_at or end)
    billed = [Interval(IntervalKind.WAITING, rental.reserved_at, waited_until)]
    billed.extend(
        Interval(trip.kind, trip.start, trip.end or max(trip.start, end)) for trip in stored
    )
    return tuple(billed)


@dataclass(frozen=True)
class FinishRules:
    # Stricter than for opening: the car must be where it reports now, not five minutes ago.
    max_state_age: timedelta


@dataclass(frozen=True)
class FinishCheck:
    """What the car told in its last snapshot and where that is."""

    recorded_at: datetime | None
    ignition_on: bool
    in_end_zone: bool


def ensure_finish_allowed(
    rental: Rental,
    inspection_submitted: bool,
    check: FinishCheck,
    rules: FinishRules,
    now: datetime,
) -> None:
    """The client's checks before the car is locked; the doors are told by the car when it locks.

    One reason at a time, in the order the client fixes them: the photos, then the car itself.
    """
    if rental.status not in _DRIVING:
        raise RentalTransitionNotAllowedError
    if rental.is_busy(now):
        raise RentalBusyError
    if not inspection_submitted:
        raise InspectionRequiredError
    if check.recorded_at is None or now - check.recorded_at > rules.max_state_age:
        raise CarStateStaleError
    if not check.in_end_zone:
        raise OutsideEndZoneError
    if check.ignition_on:
        raise IgnitionOnError


@dataclass(frozen=True)
class ParkCheck:
    """What the car told in its last snapshot when the client asks for the parked price."""

    recorded_at: datetime | None
    ignition_on: bool


def ensure_toggle_allowed(
    rental: Rental,
    transition: RentalTransition,
    check: ParkCheck,
    rules: FinishRules,
    now: datetime,
) -> None:
    """Parking is the cheaper price, so the car must be standing with its ignition off: otherwise
    the client would set it and drive on. Driving on again needs nothing but a free rental."""
    if rental.is_busy(now):
        raise RentalBusyError
    if transition is not RentalTransition.PARK:
        return
    if check.recorded_at is None or now - check.recorded_at > rules.max_state_age:
        raise CarStateStaleError
    if check.ignition_on:
        raise IgnitionOnError


# The emulator's reasons, the ones a real provider's adapter maps its own to.
_DOORS_OPEN_REASON = "doors_open"
_IGNITION_ON_REASON = "ignition_on"


def lock_refusal(reason: str) -> AppError:
    """The client-facing error for a car that refused to lock at the end of the trip."""
    if reason == _DOORS_OPEN_REASON:
        return DoorsOpenError()
    if reason == _IGNITION_ON_REASON:
        return IgnitionOnError()
    return CarLockFailedError(reason)


class SettlementStep(StrEnum):
    """One payment of a rental; the value is also part of its idempotency key."""

    CAPTURE = "capture"
    VOID = "void"
    EXTRA = "extra"
    CHARGE = "charge"


@dataclass(frozen=True)
class PlannedPayment:
    step: SettlementStep
    # Kopecks; None for a void, which takes nothing.
    amount: int | None


def plan_settlement(total: int, hold_amount: int | None) -> tuple[PlannedPayment, ...]:
    """How a total is paid: from the hold first, the rest above it charged to the card.

    A rental without a hold (a cancelled reservation) is charged directly; a hold of a trip that
    cost nothing is released.
    """
    if total < 0:
        raise ValueError("a total cannot be negative")
    if hold_amount is None:
        return (PlannedPayment(SettlementStep.CHARGE, total),) if total else ()
    if total == 0:
        return (PlannedPayment(SettlementStep.VOID, None),)
    captured = min(total, hold_amount)
    steps = [PlannedPayment(SettlementStep.CAPTURE, captured)]
    if total > captured:
        steps.append(PlannedPayment(SettlementStep.EXTRA, total - captured))
    return tuple(steps)


def payment_key(rental_id: uuid.UUID, step: SettlementStep | str) -> str:
    return f"rental:{rental_id}:{step}"


def debt_key(rental_id: uuid.UUID, attempt: int) -> str:
    return f"rental:{rental_id}:debt:{attempt}"


def hold_key(rental_id: uuid.UUID, attempt: int) -> str:
    return f"rental:{rental_id}:hold:{attempt}"


@dataclass(frozen=True)
class ChargeRetryRules:
    max_attempts: int
    interval: timedelta


def next_charge_attempt(attempts: int, rules: ChargeRetryRules, now: datetime) -> datetime | None:
    """When the worker retries a declined debt next; None once the attempts are spent.

    A debt left after them waits for the client, or for an operator (#112).
    """
    if attempts >= rules.max_attempts:
        return None
    return now + rules.interval


class TripWarning(StrEnum):
    LOW_BATTERY = "low_battery"
    LOW_RANGE = "low_range"


@dataclass(frozen=True)
class TripWarningRules:
    low_battery_pct: int
    low_range_km: int


def trip_warnings(
    battery_pct: int | None, range_km: int | None, rules: TripWarningRules
) -> tuple[TripWarning, ...]:
    """What the trip screen warns about; nothing without telemetry rather than a false alarm."""
    warnings = []
    if battery_pct is not None and battery_pct < rules.low_battery_pct:
        warnings.append(TripWarning.LOW_BATTERY)
    if range_km is not None and range_km < rules.low_range_km:
        warnings.append(TripWarning.LOW_RANGE)
    return tuple(warnings)


@dataclass(frozen=True)
class ReceiptCard:
    brand: str
    last4: str


@dataclass(frozen=True)
class Receipt:
    """Issued once, when the rental ends; the database refuses to change it later."""

    rental_id: uuid.UUID
    tariff_version_id: uuid.UUID
    quote: Quote
    # The card the hold or the charge went to; None if the client had no card left.
    card: ReceiptCard | None
    issued_at: datetime
