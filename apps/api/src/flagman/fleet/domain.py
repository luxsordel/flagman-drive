"""Car statuses, the availability rule and car identifiers. No I/O here."""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus

from flagman.core.errors import AppError
from flagman.core.geo import GeoPoint

PLATE_MAX_LENGTH = 16
VIN_LENGTH = 17
BRAND_MAX_LENGTH = 64
MODEL_NAME_MAX_LENGTH = 64
PHOTO_URL_MAX_LENGTH = 512

# Belarusian plates use only the Latin letters that look like Cyrillic ones: "1234 AB-7".
# [0-9] rather than \d: \d also matches non-ASCII digits.
PLATE_PATTERN = re.compile(r"[0-9]{4} [ABEIKMHOPCTX]{2}-[1-8]")
# I, O and Q never appear in a VIN: they are too easy to mistake for 1 and 0 (ISO 3779).
VIN_PATTERN = re.compile(r"[A-HJ-NPR-Z0-9]{17}")

# Car ids of the emulator (services/car-emulator, car_emulator.domain.car_id): the seed derives
# the same telematics ids, so telemetry lands on the seeded cars. Both sides test one vector.
EMULATOR_CAR_ID_NAMESPACE = uuid.UUID("b3dbc708-f7ca-4013-88f6-d3564a1f0b2c")


class CarStatus(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    AVAILABLE = "available"
    RESERVED = "reserved"
    IN_RENTAL = "in_rental"
    SERVICE = "service"
    OFFLINE = "offline"


class CarTransition(StrEnum):
    RESERVE = "reserve"
    CANCEL_RESERVATION = "cancel_reservation"
    START_RENTAL = "start_rental"
    END_RENTAL = "end_rental"
    SEND_TO_SERVICE = "send_to_service"
    RETURN_FROM_SERVICE = "return_from_service"
    GO_OFFLINE = "go_offline"
    COME_ONLINE = "come_online"


@dataclass(frozen=True)
class _Rule:
    sources: frozenset[CarStatus]
    target: CarStatus


# A reserved or rented car is never pulled out by fleet transitions: the rental would be left
# pointing at a car in service. Stage 3 ends or cancels the rental first.
_RULES: dict[CarTransition, _Rule] = {
    CarTransition.RESERVE: _Rule(frozenset({CarStatus.AVAILABLE}), CarStatus.RESERVED),
    CarTransition.CANCEL_RESERVATION: _Rule(frozenset({CarStatus.RESERVED}), CarStatus.AVAILABLE),
    CarTransition.START_RENTAL: _Rule(frozenset({CarStatus.RESERVED}), CarStatus.IN_RENTAL),
    CarTransition.END_RENTAL: _Rule(frozenset({CarStatus.IN_RENTAL}), CarStatus.AVAILABLE),
    CarTransition.SEND_TO_SERVICE: _Rule(
        frozenset({CarStatus.AVAILABLE, CarStatus.OFFLINE}), CarStatus.SERVICE
    ),
    CarTransition.RETURN_FROM_SERVICE: _Rule(frozenset({CarStatus.SERVICE}), CarStatus.AVAILABLE),
    CarTransition.GO_OFFLINE: _Rule(
        frozenset({CarStatus.AVAILABLE, CarStatus.SERVICE}), CarStatus.OFFLINE
    ),
    CarTransition.COME_ONLINE: _Rule(frozenset({CarStatus.OFFLINE}), CarStatus.AVAILABLE),
}

# Only declared here: the rentals module (stage 3) calls them, the fleet never does on its own.
RENTAL_TRANSITIONS = frozenset(
    {
        CarTransition.RESERVE,
        CarTransition.CANCEL_RESERVATION,
        CarTransition.START_RENTAL,
        CarTransition.END_RENTAL,
    }
)


class CarNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "car-not-found"
    title = "Car not found"


class CarTransitionNotAllowedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "car-transition-not-allowed"
    title = "The car cannot change to this status now"


class CarIdentityTakenError(Exception):
    """The plate or the VIN is already used by a car with another telematics id."""


class SnapshotFromFutureError(ValueError):
    """The telematics clock is ahead of ours by more than the skew window."""


class TelemetrySignatureInvalidError(AppError):
    """The reason stays in the server log: telling a caller which check failed helps a forger."""

    status = HTTPStatus.UNAUTHORIZED
    slug = "invalid-signature"
    title = "Invalid signature"


class TelemetryTooLargeError(AppError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    slug = "telemetry-too-large"
    title = "The telemetry batch is too large"


class TelemetryUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "telemetry-unavailable"
    title = "Telemetry is not accepted now"


class CarCommand(StrEnum):
    """What staff may ask a car to do; values are stored in the audit log as is."""

    OPEN = "open"
    CLOSE = "close"
    LOCK_ENGINE = "lock_engine"
    UNLOCK_ENGINE = "unlock_engine"


class CommandOutcome(StrEnum):
    """How a command ended; values are stored in the audit log as is."""

    DONE = "done"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    UNAVAILABLE = "unavailable"
    # No answer in time: the car may or may not have done it.
    TIMED_OUT = "timed_out"


COMMENT_MIN_LENGTH = 3
COMMENT_MAX_LENGTH = 300
# Reason codes come from telematics: anything else is replaced, as it would reach the client.
REASON_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
UNKNOWN_REASON = "unknown"

# A client holds or drives the car: a command could lock them out or in (docs/01, staff scenarios).
_CLIENT_STATUSES = frozenset({CarStatus.RESERVED, CarStatus.IN_RENTAL})


def needs_rental_confirmation(status: CarStatus) -> bool:
    # By status until rentals exist (stage 3): then the active rental itself is the reason.
    return status in _CLIENT_STATUSES


def normalize_reason(reason: str) -> str:
    return reason if REASON_PATTERN.fullmatch(reason) else UNKNOWN_REASON


class CarInActiveRentalError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "car-in-active-rental"
    title = "A client holds the car: confirm the command"


class CarCommandRejectedError(AppError):
    """The car refused; `detail` is its reason code, e.g. `ignition_on`."""

    status = HTTPStatus.CONFLICT
    slug = "car-command-rejected"
    title = "The car refused the command"

    def __init__(self, reason: str) -> None:
        super().__init__(normalize_reason(reason))
        self.reason = normalize_reason(reason)


class CarCommandDuplicateError(AppError):
    """Telematics has seen the command id: a replay, never a retry of ours (ids are fresh)."""

    status = HTTPStatus.CONFLICT
    slug = "car-command-duplicate"
    title = "The command was already sent"


class TelematicsUnavailableError(AppError):
    """The reason stays in the server log: a wrong secret is not the caller's business."""

    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "telematics-unavailable"
    title = "The car cannot be reached now"


class TelematicsTimeoutError(AppError):
    status = HTTPStatus.GATEWAY_TIMEOUT
    slug = "telematics-timeout"
    title = "The car did not answer in time; check its state before trying again"


def apply_transition(status: CarStatus, transition: CarTransition) -> CarStatus:
    rule = _RULES[transition]
    if status not in rule.sources:
        raise CarTransitionNotAllowedError
    return rule.target


@dataclass(frozen=True)
class CarState:
    """The last snapshot from telematics; `recorded_at` is the car's time, not the arrival time."""

    location: GeoPoint
    battery_pct: int
    doors_locked: bool
    ignition_on: bool
    # The immobilizer: a locked engine does not start, a running one is not stopped by it.
    engine_locked: bool
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.battery_pct <= 100:  # noqa: PLR2004
            raise ValueError("battery_pct must be within 0..100")
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")


@dataclass(frozen=True)
class CarModelSpec:
    brand: str
    name: str
    seats: int
    battery_wh: int
    range_km: int
    photo_url: str | None


@dataclass(frozen=True)
class StoredCarModel:
    id: uuid.UUID
    spec: CarModelSpec


@dataclass(frozen=True)
class Car:
    id: uuid.UUID
    model_id: uuid.UUID
    model: CarModelSpec
    plate: str
    vin: str
    telematics_id: uuid.UUID
    status: CarStatus
    state: CarState | None


class SnapshotOutcome(StrEnum):
    APPLIED = "applied"
    # Same or older than the stored one: a repeated or late webhook (docs/adr/0007).
    STALE = "stale"
    UNKNOWN_CAR = "unknown_car"


@dataclass(frozen=True)
class AvailabilityRules:
    min_battery_pct: int
    max_state_age: timedelta


@dataclass(frozen=True)
class TelemetrySnapshot:
    telematics_id: uuid.UUID
    state: CarState


@dataclass(frozen=True)
class TelemetryReport:
    """How a batch went, car by car; the counts add up to the number of snapshots in it."""

    applied: int = 0
    stale: int = 0
    unknown_car: int = 0
    from_future: int = 0


def check_snapshot_time(recorded_at: datetime, now: datetime, max_skew: timedelta) -> None:
    # The skew is the signature window of telematics requests (docs/adr/0007): a snapshot further
    # ahead would count as fresh for longer than the threshold allows. Within the window it still
    # may, by up to `max_skew` (docs/02-architecture.md, the fleet section).
    if recorded_at > now + max_skew:
        raise SnapshotFromFutureError


def is_rentable(
    status: CarStatus, state: CarState | None, rules: AvailabilityRules, now: datetime
) -> bool:
    # Without fresh telemetry the car may be anywhere and with any charge, so it is not offered.
    if status is not CarStatus.AVAILABLE or state is None:
        return False
    if now - state.recorded_at > rules.max_state_age:
        return False
    # Ignition on means someone drives it: a client would walk to a car that is already gone.
    if state.ignition_on:
        return False
    return state.battery_pct >= rules.min_battery_pct


@dataclass(frozen=True)
class MapArea:
    """The part of the map a client looks at; edges are inclusive."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def __post_init__(self) -> None:
        # Through the corners: a reversed box is a mistake of the caller, not an empty map.
        GeoPoint(self.min_lat, self.min_lon)
        GeoPoint(self.max_lat, self.max_lon)
        if self.min_lat >= self.max_lat or self.min_lon >= self.max_lon:
            raise ValueError("the area must have min below max on both axes")


@dataclass(frozen=True)
class MapQuery:
    """Which rentable cars to show: those in `area`, nearest to `near` first, at most `limit`."""

    area: MapArea | None
    near: GeoPoint | None
    limit: int
    # Only cars of these models, None for any: a model without a tariff is not rented out, and
    # the tariffs are pricing's, so the caller names the models.
    models: frozenset[uuid.UUID] | None = None


@dataclass(frozen=True)
class CarOnMap:
    """What a client sees of a rentable car: nothing that helps to find or spoof it otherwise."""

    id: uuid.UUID
    # The tariff is the model's (pricing): the map shows its prices next to the car.
    model_id: uuid.UUID
    brand: str
    model: str
    photo_url: str | None
    location: GeoPoint
    battery_pct: int
    range_km: int
    # Only when the client shared where they are; the point itself is never kept.
    distance_m: int | None


@dataclass(frozen=True)
class StaffCarFilter:
    """Which cars a staff member lists; no statuses means every status."""

    statuses: frozenset[CarStatus] = frozenset()
    low_battery: bool = False


def is_low_battery(state: CarState | None, rules: AvailabilityRules) -> bool:
    # Below the charge a car is offered to clients at: it waits for a charge, not for a client.
    return state is not None and state.battery_pct < rules.min_battery_pct


def matches_staff_filter(car: Car, car_filter: StaffCarFilter, rules: AvailabilityRules) -> bool:
    if car_filter.statuses and car.status not in car_filter.statuses:
        return False
    return not car_filter.low_battery or is_low_battery(car.state, rules)


def range_left_km(full_range_km: int, battery_pct: int) -> int:
    # Proportional to the charge and rounded down: promising more than the car can do strands
    # the client, promising a little less costs nothing.
    return full_range_km * battery_pct // 100


def car_on_map(car: Car, distance_m: float | None) -> CarOnMap:
    if car.state is None:
        raise ValueError("a car without telemetry is never on the map")
    return CarOnMap(
        id=car.id,
        model_id=car.model_id,
        brand=car.model.brand,
        model=car.model.name,
        photo_url=car.model.photo_url,
        location=car.state.location,
        battery_pct=car.state.battery_pct,
        range_km=range_left_km(car.model.range_km, car.state.battery_pct),
        distance_m=round(distance_m) if distance_m is not None else None,
    )


def normalize_plate(value: str) -> str:
    plate = value.strip().upper()
    if not PLATE_PATTERN.fullmatch(plate):
        raise ValueError("not a Belarusian plate, expected e.g. 1234 AB-7")
    return plate


def normalize_vin(value: str) -> str:
    vin = value.strip().upper()
    if not VIN_PATTERN.fullmatch(vin):
        raise ValueError(f"a VIN is {VIN_LENGTH} letters and digits without I, O and Q")
    return vin


def emulator_car_id(seed: int, index: int) -> uuid.UUID:
    return uuid.uuid5(EMULATOR_CAR_ID_NAMESPACE, f"{seed}:{index}")
