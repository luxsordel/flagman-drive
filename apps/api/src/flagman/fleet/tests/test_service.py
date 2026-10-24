import random
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.fleet import repository, service
from flagman.fleet.domain import (
    MAX_CLOCK_SKEW,
    CarIdentityTakenError,
    CarNotFoundError,
    CarState,
    CarStatus,
    CarTransition,
    CarTransitionNotAllowedError,
    GeoPoint,
    SnapshotFromFutureError,
    SnapshotOutcome,
    emulator_car_id,
)
from flagman.fleet.seed import DEMO_MODELS

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
# Precise to a few millimeters: more digits than GPS gives, so a lossy round trip shows.
POINT = GeoPoint(lat=53.902284, lon=27.561831)
_PLATE_LETTERS = "ABEIKMHOPCTX"
_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


@pytest.fixture
async def db(integration_settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_engine(integration_settings)
    try:
        async with create_session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


async def _add_car(db: AsyncSession, status: CarStatus = CarStatus.AVAILABLE) -> uuid.UUID:
    """A car of its own per test: rows stay in the shared database between tests."""
    rng = random.SystemRandom()
    letters = "".join(rng.choices(_PLATE_LETTERS, k=2))
    telematics_id = uuid.uuid4()
    async with db.begin():
        model_id, _ = await repository.ensure_model(db, DEMO_MODELS[0].spec, NOW)
        car = repository.NewCar(
            model_id=model_id,
            # Region 7 is left to the demo seed, so the plates never collide with it.
            plate=f"{rng.randrange(10_000):04d} {letters}-{rng.randint(1, 6)}",
            vin="".join(rng.choices(_VIN_CHARS, k=17)),
            telematics_id=telematics_id,
            status=status,
        )
        assert await repository.add_car_if_absent(db, car, NOW)
    return telematics_id


async def _car_id(db: AsyncSession, telematics_id: uuid.UUID) -> uuid.UUID:
    cars = await service.list_cars(db)
    return next(car.id for car in cars if car.telematics_id == telematics_id)


def _state(at: datetime = NOW, battery_pct: int = 80, point: GeoPoint = POINT) -> CarState:
    return CarState(
        location=point,
        battery_pct=battery_pct,
        doors_locked=True,
        ignition_on=False,
        recorded_at=at,
    )


async def _apply(db: AsyncSession, telematics_id: uuid.UUID, state: CarState) -> SnapshotOutcome:
    async with db.begin():
        return await service.apply_state_snapshot(db, telematics_id, state, NOW)


async def test_seed_is_idempotent_and_matches_the_emulator(db: AsyncSession) -> None:
    await service.seed_demo_fleet(db, emulator_seed=42, car_count=30, now=NOW)
    again = await service.seed_demo_fleet(db, emulator_seed=42, car_count=30, now=NOW)

    assert again == service.SeedResult(models_created=0, cars_created=0, cars_existing=30)
    cars = {car.telematics_id: car for car in await service.list_cars(db)}
    seeded = [cars[emulator_car_id(42, index)] for index in range(30)]
    assert {car.model.name for car in seeded} == {model.spec.name for model in DEMO_MODELS}
    assert len({car.plate for car in seeded}) == len({car.vin for car in seeded}) == 30
    # No telemetry yet: the cars wait for the emulator before they are offered.
    assert all(car.status is CarStatus.AVAILABLE and car.state is None for car in seeded)


async def test_seed_for_another_emulator_seed_is_refused(db: AsyncSession) -> None:
    await service.seed_demo_fleet(db, emulator_seed=42, car_count=30, now=NOW)

    # The same plates under new telematics ids: skipping them would leave cars telemetry never
    # reaches, and adding them is impossible, so the seed stops instead.
    with pytest.raises(CarIdentityTakenError):
        await service.seed_demo_fleet(db, emulator_seed=43, car_count=30, now=NOW)

    telematics_ids = {car.telematics_id for car in await service.list_cars(db)}
    assert emulator_car_id(43, 0) not in telematics_ids


async def test_seed_refuses_a_count_beyond_the_plates(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="car_count"):
        await service.seed_demo_fleet(db, emulator_seed=42, car_count=10_000, now=NOW)


async def test_snapshot_round_trips_through_geography(db: AsyncSession) -> None:
    telematics_id = await _add_car(db)

    assert await _apply(db, telematics_id, _state()) is SnapshotOutcome.APPLIED

    car = await service.get_car(db, await _car_id(db, telematics_id))
    assert car.state == _state()


async def test_newer_snapshot_replaces_the_stored_one(db: AsyncSession) -> None:
    telematics_id = await _add_car(db)
    await _apply(db, telematics_id, _state())
    newer = _state(at=NOW + timedelta(seconds=5), battery_pct=79)

    assert await _apply(db, telematics_id, newer) is SnapshotOutcome.APPLIED

    car = await service.get_car(db, await _car_id(db, telematics_id))
    assert car.state == newer


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(seconds=-5)])
async def test_repeated_or_late_snapshot_is_ignored(db: AsyncSession, delay: timedelta) -> None:
    telematics_id = await _add_car(db)
    await _apply(db, telematics_id, _state())
    late = _state(at=NOW + delay, battery_pct=10, point=GeoPoint(lat=53.85, lon=27.45))

    assert await _apply(db, telematics_id, late) is SnapshotOutcome.STALE

    car = await service.get_car(db, await _car_id(db, telematics_id))
    assert car.state == _state()


async def test_snapshot_of_an_unknown_car(db: AsyncSession) -> None:
    assert await _apply(db, uuid.uuid4(), _state()) is SnapshotOutcome.UNKNOWN_CAR


async def test_snapshot_from_the_future_is_refused(db: AsyncSession) -> None:
    telematics_id = await _add_car(db)
    ahead = _state(at=NOW + MAX_CLOCK_SKEW + timedelta(seconds=1))

    with pytest.raises(SnapshotFromFutureError):
        await _apply(db, telematics_id, ahead)


async def test_snapshot_needs_the_callers_transaction(db: AsyncSession) -> None:
    with pytest.raises(RuntimeError, match="transaction"):
        await service.apply_state_snapshot(db, uuid.uuid4(), _state(), NOW)


async def test_change_status_follows_the_status_machine(db: AsyncSession) -> None:
    car_id = await _car_id(db, await _add_car(db))

    async with db.begin():
        status = await service.change_status(db, car_id, CarTransition.RESERVE, NOW)
    assert status is CarStatus.RESERVED
    assert (await service.get_car(db, car_id)).status is CarStatus.RESERVED

    with pytest.raises(CarTransitionNotAllowedError):
        async with db.begin():
            await service.change_status(db, car_id, CarTransition.SEND_TO_SERVICE, NOW)
    assert (await service.get_car(db, car_id)).status is CarStatus.RESERVED


async def test_change_status_of_an_unknown_car(db: AsyncSession) -> None:
    with pytest.raises(CarNotFoundError):
        async with db.begin():
            await service.change_status(db, uuid.uuid4(), CarTransition.RESERVE, NOW)


async def test_get_unknown_car(db: AsyncSession) -> None:
    with pytest.raises(CarNotFoundError):
        await service.get_car(db, uuid.uuid4())


async def test_rentable_follows_the_settings(
    db: AsyncSession, integration_settings: Settings
) -> None:
    telematics_id = await _add_car(db)
    await _apply(db, telematics_id, _state(battery_pct=integration_settings.fleet_min_battery_pct))
    car = await service.get_car(db, await _car_id(db, telematics_id))
    rules = service.availability_rules(integration_settings)

    assert service.is_rentable(car, rules, NOW)
    assert not service.is_rentable(car, rules, NOW + rules.max_state_age + timedelta(seconds=1))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE cars SET status = 'parked' WHERE telematics_id = :id",
        "UPDATE cars SET battery_pct = 101, state_at = now(),"
        " location = 'SRID=4326;POINT(27.5 53.9)', doors_locked = true, ignition_on = false"
        " WHERE telematics_id = :id",
        # Half a snapshot: the coordinates without the rest.
        "UPDATE cars SET location = 'SRID=4326;POINT(27.5 53.9)' WHERE telematics_id = :id",
    ],
)
async def test_database_refuses_invalid_rows(db: AsyncSession, statement: str) -> None:
    telematics_id = await _add_car(db)

    with pytest.raises(IntegrityError):
        async with db.begin():
            await db.execute(text(statement), {"id": telematics_id})
