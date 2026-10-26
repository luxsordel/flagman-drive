"""Public interface of the fleet for other modules.

Reads join the caller's transaction if one is open; changes run only inside it, so a rental
(stage 3) changes the car status and its own rows in one transaction. The demo seed and the
telemetry intake are the exceptions: each is a command of its own and opens its own transaction.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.core.geo import GeoPoint
from flagman.fleet import domain, repository
from flagman.fleet.domain import (
    AvailabilityRules,
    Car,
    CarNotFoundError,
    CarOnMap,
    CarState,
    CarStatus,
    CarTransition,
    MapQuery,
    SnapshotFromFutureError,
    SnapshotOutcome,
    StoredCarModel,
    TelemetryReport,
    TelemetrySnapshot,
    apply_transition,
    car_on_map,
    check_snapshot_time,
    emulator_car_id,
    normalize_plate,
    normalize_vin,
)
from flagman.fleet.seed import DEMO_MODELS, MAX_DEMO_CARS, demo_plate, demo_vin

logger = structlog.get_logger(__name__)

# Enough to find the cars in the emulator or the seed; a wrong seed would list every car.
_LOGGED_UNKNOWN_CARS = 10


def telemetry_clock_skew(settings: Settings) -> timedelta:
    return timedelta(seconds=settings.telematics_signature_tolerance_seconds)


def availability_rules(settings: Settings) -> AvailabilityRules:
    return AvailabilityRules(
        min_battery_pct=settings.fleet_min_battery_pct,
        max_state_age=timedelta(seconds=settings.fleet_telemetry_max_age_seconds),
    )


async def _read[T](db: AsyncSession, read: Callable[[], Awaitable[T]]) -> T:
    if db.in_transaction():
        return await read()
    async with db.begin():
        return await read()


def is_rentable(car: Car, rules: AvailabilityRules, now: datetime) -> bool:
    """Whether the car may be offered to a client now; the rule is in fleet.domain."""
    return domain.is_rentable(car.status, car.state, rules, now)


async def find_cars_on_map(
    db: AsyncSession, rules: AvailabilityRules, now: datetime, query: MapQuery
) -> list[CarOnMap]:
    """Rentable cars for the client map; `query.near` is used for this query only, never kept."""
    if query.area is None and query.near is None:
        raise ValueError("give an area, a point or both: the whole fleet is not a map")
    rows = await _read(db, lambda: repository.find_rentable_cars(db, rules, now, query))
    return [
        car_on_map(car, distance)
        for car, distance in rows
        if domain.is_rentable(car.status, car.state, rules, now)
    ]


async def get_car_on_map(
    db: AsyncSession, car_id: UUID, rules: AvailabilityRules, now: datetime, near: GeoPoint | None
) -> CarOnMap:
    """Raises CarNotFoundError for a car that is not rentable now as well as for a missing one."""
    query = MapQuery(area=None, near=near, limit=1)
    rows = await _read(db, lambda: repository.find_rentable_cars(db, rules, now, query, car_id))
    for car, distance in rows:
        if domain.is_rentable(car.status, car.state, rules, now):
            return car_on_map(car, distance)
    raise CarNotFoundError


async def get_car(db: AsyncSession, car_id: UUID) -> Car:
    if db.in_transaction():
        car = await repository.get_car(db, car_id)
    else:
        async with db.begin():
            car = await repository.get_car(db, car_id)
    if car is None:
        raise CarNotFoundError
    return car


async def list_cars(db: AsyncSession) -> list[Car]:
    if db.in_transaction():
        return await repository.list_cars(db)
    async with db.begin():
        return await repository.list_cars(db)


async def list_models(db: AsyncSession) -> list[StoredCarModel]:
    """Car models by brand and name; the pricing module lists a tariff for each."""
    return await _read(db, lambda: repository.list_models(db))


async def get_model(db: AsyncSession, model_id: UUID) -> StoredCarModel | None:
    return await _read(db, lambda: repository.get_model(db, model_id))


async def change_status(
    db: AsyncSession, car_id: UUID, transition: CarTransition, now: datetime
) -> CarStatus:
    """Moves the car along the status machine (fleet.domain) and holds its row until commit."""
    _require_transaction(db, "change_status")
    status = await repository.get_status_for_update(db, car_id)
    if status is None:
        raise CarNotFoundError
    new_status = apply_transition(status, transition)
    await repository.set_status(db, car_id, new_status, now)
    return new_status


async def apply_state_snapshot(
    db: AsyncSession, telematics_id: UUID, state: CarState, now: datetime, max_skew: timedelta
) -> SnapshotOutcome:
    """Stores a telematics snapshot unless a newer one is stored already.

    Raises SnapshotFromFutureError for a snapshot more than `max_skew` ahead of `now`.
    """
    _require_transaction(db, "apply_state_snapshot")
    check_snapshot_time(state.recorded_at, now, max_skew)
    return await repository.apply_state(db, telematics_id, state, now)


async def receive_telemetry(
    db: AsyncSession, snapshots: Sequence[TelemetrySnapshot], now: datetime, max_skew: timedelta
) -> TelemetryReport:
    """Applies a telemetry batch in one transaction of its own.

    A snapshot that cannot be applied is counted and skipped: one unknown car or one skewed clock
    must not hold back the state of the rest of the fleet.
    """
    counts = dict.fromkeys(SnapshotOutcome, 0)
    unknown: list[UUID] = []
    from_future = 0
    async with db.begin():
        for snapshot in snapshots:
            try:
                outcome = await apply_state_snapshot(
                    db, snapshot.telematics_id, snapshot.state, now, max_skew
                )
            except SnapshotFromFutureError:
                from_future += 1
                continue
            counts[outcome] += 1
            if outcome is SnapshotOutcome.UNKNOWN_CAR:
                unknown.append(snapshot.telematics_id)
    report = TelemetryReport(
        applied=counts[SnapshotOutcome.APPLIED],
        stale=counts[SnapshotOutcome.STALE],
        unknown_car=counts[SnapshotOutcome.UNKNOWN_CAR],
        from_future=from_future,
    )
    if unknown or from_future:
        # Telematics ids only: the coordinates in the snapshots show where clients drive.
        logger.warning(
            "telemetry_partly_rejected",
            unknown_car=report.unknown_car,
            unknown_telematics_ids=[str(car_id) for car_id in unknown[:_LOGGED_UNKNOWN_CARS]],
            from_future=from_future,
        )
    return report


@dataclass(frozen=True)
class SeedResult:
    models_created: int
    cars_created: int
    cars_existing: int


async def seed_demo_fleet(
    db: AsyncSession, emulator_seed: int, car_count: int, now: datetime
) -> SeedResult:
    """Adds the demo models and cars; safe to run again, existing rows stay as they are.

    Cars start `available` without state: they become rentable once the emulator's telemetry
    arrives for them. Runs in a transaction of its own, so nothing is added on a failure.
    Raises CarIdentityTakenError if the demo cars exist under other telematics ids, i.e. they were
    seeded for another EMULATOR_SEED.
    """
    if not 0 < car_count <= MAX_DEMO_CARS:
        raise ValueError(f"car_count must be within 1..{MAX_DEMO_CARS}")
    async with db.begin():
        model_ids: dict[int, UUID] = {}
        models_created = 0
        cars_created = 0
        for index in range(car_count):
            key = index % len(DEMO_MODELS)
            if key not in model_ids:
                spec = DEMO_MODELS[key].spec
                model_ids[key], created = await repository.ensure_model(db, spec, now)
                models_created += created
            car = repository.NewCar(
                model_id=model_ids[key],
                plate=normalize_plate(demo_plate(index)),
                vin=normalize_vin(demo_vin(index)),
                telematics_id=emulator_car_id(emulator_seed, index),
                status=CarStatus.AVAILABLE,
            )
            cars_created += await repository.add_car_if_absent(db, car, now)
    return SeedResult(models_created, cars_created, car_count - cars_created)


def _require_transaction(db: AsyncSession, name: str) -> None:
    if not db.in_transaction():
        raise RuntimeError(f"fleet.service.{name} must run inside the caller's transaction")
