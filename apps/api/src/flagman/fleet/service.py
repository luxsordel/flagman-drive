"""Public interface of the fleet for other modules.

Reads join the caller's transaction if one is open; changes run only inside it, so a rental
(stage 3) changes the car status and its own rows in one transaction. The demo seed is the
exception: it is a command of its own and opens its own transaction.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.fleet import domain, repository
from flagman.fleet.domain import (
    AvailabilityRules,
    Car,
    CarNotFoundError,
    CarState,
    CarStatus,
    CarTransition,
    SnapshotOutcome,
    apply_transition,
    check_snapshot_time,
    emulator_car_id,
    normalize_plate,
    normalize_vin,
)
from flagman.fleet.seed import DEMO_MODELS, MAX_DEMO_CARS, demo_plate, demo_vin


def availability_rules(settings: Settings) -> AvailabilityRules:
    return AvailabilityRules(
        min_battery_pct=settings.fleet_min_battery_pct,
        max_state_age=timedelta(seconds=settings.fleet_telemetry_max_age_seconds),
    )


def is_rentable(car: Car, rules: AvailabilityRules, now: datetime) -> bool:
    """Whether the car may be offered to a client now; the rule is in fleet.domain."""
    return domain.is_rentable(car.status, car.state, rules, now)


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
    db: AsyncSession, telematics_id: UUID, state: CarState, now: datetime
) -> SnapshotOutcome:
    """Stores a telematics snapshot unless a newer one is stored already.

    Raises SnapshotFromFutureError for a snapshot too far ahead of `now`.
    """
    _require_transaction(db, "apply_state_snapshot")
    check_snapshot_time(state.recorded_at, now)
    return await repository.apply_state(db, telematics_id, state, now)


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
