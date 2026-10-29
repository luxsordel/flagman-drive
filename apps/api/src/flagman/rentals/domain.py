"""Rentals: the status machine, reservation terms, inspections and car commands. No I/O here.

The machine is drawn in docs/02-architecture.md; the reasons behind the reservation timeout are
in docs/adr/0022.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus

from flagman.core.errors import AppError


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

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def is_busy(self, now: datetime) -> bool:
        return self.command_until is not None and now < self.command_until

    def is_expired(self, now: datetime) -> bool:
        """A reservation past its end that no command holds; the timeout cancels it."""
        return (
            self.status is RentalStatus.RESERVED
            and now >= self.expires_at
            and not self.is_busy(now)
        )


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


def normalize_damage_comment(comment: str) -> str:
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
