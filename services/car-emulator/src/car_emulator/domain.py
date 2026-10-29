"""Virtual car state, command rules and the fleet's background life. No HTTP, no I/O."""

import math
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Rough bounding box of Minsk inside the ring road.
MINSK_LAT_RANGE = (53.84, 53.97)
MINSK_LON_RANGE = (27.40, 27.70)

_EARTH_RADIUS_KM = 6371.0088
_COORDINATE_DIGITS = 6
_SECONDS_PER_HOUR = 3600
# A shorter hop would look like a car standing still with its ignition on.
MIN_TRIP_KM = 1.0
# A client's hop starts at least this far: a shorter one is a car rolling out of its spot.
MIN_CLIENT_TRIP_KM = 0.3

# Car ids are a contract with the API seed (apps/api, flagman.fleet): it derives the same ids from
# EMULATOR_SEED and EMULATOR_CAR_COUNT, so telemetry lands on the cars the API knows. Changing the
# namespace or the name format changes every id on both sides.
CAR_ID_NAMESPACE = uuid.UUID("b3dbc708-f7ca-4013-88f6-d3564a1f0b2c")


class CommandType(StrEnum):
    OPEN = "open"
    CLOSE = "close"
    LOCK_ENGINE = "lock_engine"
    UNLOCK_ENGINE = "unlock_engine"


class CommandRejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CarMode(StrEnum):
    PARKED = "parked"
    DRIVING = "driving"
    CHARGING = "charging"


@dataclass(frozen=True, slots=True)
class Destination:
    lat: float
    lon: float


@dataclass(slots=True)
class Car:
    id: uuid.UUID
    lat: float
    lon: float
    # Fractional, so a slow drain adds up over many short steps instead of rounding away.
    charge_pct: float
    doors_locked: bool
    ignition_on: bool
    engine_locked: bool
    updated_at: datetime
    mode: CarMode = CarMode.PARKED
    destination: Destination | None = None
    # The current drive is a client's, in a rented car: it ends with the doors left open.
    driven_by_client: bool = False
    # A rented car stands until then: the client gets in, stops for a coffee, finishes.
    rest_until: datetime | None = None

    @property
    def battery_pct(self) -> int:
        # Rounded down, like a dashboard: the car never reports charge it does not have.
        return math.floor(self.charge_pct)


@dataclass(frozen=True, slots=True)
class SimulationParams:
    """How the fleet lives between commands; rates are per hour of simulated time."""

    trip_share: float
    speed_kmh: float
    idle_drain_pct_per_h: float
    drive_drain_pct_per_km: float
    charge_below_pct: float
    charge_until_pct: float
    charge_rate_pct_per_h: float
    # A rented car drives short hops around where it is, with a pause before each.
    rental_trip_km: float = 1.5
    rental_rest: timedelta = timedelta(minutes=2)


def apply_command(car: Car, command: CommandType, now: datetime) -> None:
    match command:
        case CommandType.OPEN:
            car.doors_locked = False
            car.rest_until = None
        case CommandType.CLOSE:
            # Locking a running car would strand the driver outside it.
            if car.ignition_on:
                raise CommandRejectedError("ignition_on")
            car.doors_locked = True
            car.rest_until = None
        case CommandType.LOCK_ENGINE:
            # Like real immobilizers, this blocks the next start instead of stopping a moving car.
            car.engine_locked = True
        case CommandType.UNLOCK_ENGINE:
            car.engine_locked = False
    car.updated_at = now


def car_id(seed: int, index: int) -> uuid.UUID:
    return uuid.uuid5(CAR_ID_NAMESPACE, f"{seed}:{index}")


def seed_fleet(count: int, seed: int, now: datetime) -> list[Car]:
    # A fixed seed gives every developer the same cars. Ids do not depend on the random state,
    # so the API can derive them without repeating how positions and charge are drawn.
    rng = random.Random(seed)  # noqa: S311 - not used for security
    return [
        Car(
            id=car_id(seed, index),
            lat=round(rng.uniform(*MINSK_LAT_RANGE), 6),
            lon=round(rng.uniform(*MINSK_LON_RANGE), 6),
            charge_pct=float(rng.randint(20, 100)),
            doors_locked=True,
            ignition_on=False,
            engine_locked=False,
            updated_at=now,
        )
        for index in range(count)
    ]


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Haversine on a sphere: within a city the error against the spheroid is a few meters.
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _in_minsk(lat: float, lon: float) -> tuple[float, float]:
    lat = min(max(lat, MINSK_LAT_RANGE[0]), MINSK_LAT_RANGE[1])
    lon = min(max(lon, MINSK_LON_RANGE[0]), MINSK_LON_RANGE[1])
    return round(lat, _COORDINATE_DIGITS), round(lon, _COORDINATE_DIGITS)


def random_point_in_minsk(rng: random.Random) -> Destination:
    lat, lon = _in_minsk(rng.uniform(*MINSK_LAT_RANGE), rng.uniform(*MINSK_LON_RANGE))
    return Destination(lat=lat, lon=lon)


def pick_destination(car: Car, rng: random.Random) -> Destination:
    # The city is about 14 by 20 km, so a draw this close to the car is rare and the loop is short.
    while True:
        destination = random_point_in_minsk(rng)
        if distance_km(car.lat, car.lon, destination.lat, destination.lon) >= MIN_TRIP_KM:
            return destination


def drive_toward(
    car: Car, destination: Destination, max_km: float, drain_pct_per_km: float
) -> bool:
    """Moves the car up to `max_km` straight to `destination`; True once it is there or empty.

    Rentals of stage 3 drive the same way: only who picks the destination differs.
    """
    remaining_km = distance_km(car.lat, car.lon, destination.lat, destination.lon)
    reach_km = car.charge_pct / drain_pct_per_km if drain_pct_per_km > 0 else math.inf
    step_km = min(max_km, remaining_km, reach_km)
    arrived = step_km >= remaining_km
    if arrived:
        car.lat, car.lon = destination.lat, destination.lon
    elif step_km > 0:
        fraction = step_km / remaining_km
        car.lat, car.lon = _in_minsk(
            car.lat + (destination.lat - car.lat) * fraction,
            car.lon + (destination.lon - car.lon) * fraction,
        )
    car.charge_pct = max(0.0, car.charge_pct - step_km * drain_pct_per_km)
    return arrived or car.charge_pct <= 0


def pick_nearby(car: Car, rng: random.Random, max_km: float) -> Destination:
    """A point a short hop away, so a rented car stays in the part of town it was taken in."""
    km = rng.uniform(MIN_CLIENT_TRIP_KM, max(MIN_CLIENT_TRIP_KM, max_km))
    bearing = rng.uniform(0, 2 * math.pi)
    d_lat = math.degrees(km * math.cos(bearing) / _EARTH_RADIUS_KM)
    d_lon = math.degrees(km * math.sin(bearing) / _EARTH_RADIUS_KM) / math.cos(
        math.radians(car.lat)
    )
    lat, lon = _in_minsk(car.lat + d_lat, car.lon + d_lon)
    return Destination(lat=lat, lon=lon)


def is_rented(car: Car) -> bool:
    """Opened and free to start: a client has it. The emulator knows nothing else of rentals."""
    return not car.doors_locked and not car.engine_locked


def stop_for_client(car: Car, rest: timedelta, now: datetime) -> None:
    """Ends a client's drive where the car is: ignition off, the doors still open."""
    car.mode = CarMode.PARKED
    car.destination = None
    car.ignition_on = False
    car.driven_by_client = False
    car.rest_until = now + rest
    car.updated_at = now


def _drive_rented(car: Car, params: SimulationParams, rng: random.Random, now: datetime) -> None:
    if not is_rented(car) or car.mode is not CarMode.PARKED:
        return
    if car.rest_until is None:
        # Just opened: the client takes a seat and looks around first.
        car.rest_until = now + params.rental_rest
    elif now >= car.rest_until and car.charge_pct > 0:
        start_trip(car, pick_nearby(car, rng, params.rental_trip_km), now)
        car.driven_by_client = True
        car.rest_until = None


def can_start_trip(car: Car, params: SimulationParams) -> bool:
    # Someone opened or immobilized this car and waits for it to stay put; a low car would strand.
    return (
        car.mode is CarMode.PARKED
        and car.doors_locked
        and not car.ignition_on
        and not car.engine_locked
        and car.charge_pct >= params.charge_below_pct
    )


def start_trip(car: Car, destination: Destination, now: datetime) -> None:
    car.mode = CarMode.DRIVING
    car.destination = destination
    car.ignition_on = True
    car.updated_at = now


def _park(car: Car) -> None:
    car.mode = CarMode.PARKED
    car.destination = None
    car.ignition_on = False
    car.doors_locked = True


def _visible_state(car: Car) -> tuple[float, float, int, bool, bool]:
    return car.lat, car.lon, car.battery_pct, car.doors_locked, car.ignition_on


def step_car(car: Car, elapsed: timedelta, params: SimulationParams, now: datetime) -> None:
    """Lives `elapsed` of simulated time: drains while parked, drives on, or charges."""
    hours = elapsed.total_seconds() / _SECONDS_PER_HOUR
    if hours <= 0:
        return
    before = _visible_state(car)
    match car.mode:
        case CarMode.DRIVING if car.destination is not None:
            max_km = params.speed_kmh * hours
            if drive_toward(car, car.destination, max_km, params.drive_drain_pct_per_km):
                if car.driven_by_client:
                    stop_for_client(car, params.rental_rest, now)
                else:
                    _park(car)
        case CarMode.CHARGING:
            gained = car.charge_pct + params.charge_rate_pct_per_h * hours
            car.charge_pct = min(gained, params.charge_until_pct, 100.0)
            if car.charge_pct >= params.charge_until_pct:
                car.mode = CarMode.PARKED
        case _:
            car.charge_pct = max(0.0, car.charge_pct - params.idle_drain_pct_per_h * hours)
    # A rented car is the client's to charge or to finish with; it never leaves for a charger.
    if (
        car.mode is CarMode.PARKED
        and car.charge_pct < params.charge_below_pct
        and not is_rented(car)
    ):
        car.mode = CarMode.CHARGING
    # Like a tracker: the time moves only when something a client or the API sees has changed.
    if _visible_state(car) != before:
        car.updated_at = now


def step_fleet(
    cars: Sequence[Car],
    elapsed: timedelta,
    params: SimulationParams,
    rng: random.Random,
    now: datetime,
) -> None:
    """Lives `elapsed` for every car, then sends parked ones off until `trip_share` is driving."""
    if elapsed <= timedelta(0):
        return
    for car in cars:
        step_car(car, elapsed, params, now)
        _drive_rented(car, params, rng, now)
    driving = sum(car.mode is CarMode.DRIVING for car in cars)
    free_slots = max(0, math.floor(params.trip_share * len(cars)) - driving)
    candidates = [car for car in cars if can_start_trip(car, params)]
    for car in rng.sample(candidates, min(free_slots, len(candidates))):
        start_trip(car, pick_destination(car, rng), now)
