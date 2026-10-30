import json
import random
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from car_emulator.config import Settings
from car_emulator.domain import (
    MIN_CLIENT_TRIP_KM,
    Car,
    CarMode,
    CommandRejectedError,
    CommandType,
    Gear,
    SimulationParams,
    apply_command,
    distance_km,
    seed_fleet,
    step_fleet,
)
from car_emulator.fleet import FleetService
from car_emulator.main import create_app
from tests.conftest import BASE_URL, COMMAND_SECRET, TELEMETRY_SECRET, FakeClock, signed

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
TICK = timedelta(seconds=5)
REST = timedelta(minutes=2)
PARAMS = SimulationParams(
    # No background trips: only the rented car moves.
    trip_share=0.0,
    speed_kmh=30.0,
    idle_drain_pct_per_h=1.0,
    drive_drain_pct_per_km=0.2,
    charge_below_pct=25.0,
    charge_until_pct=90.0,
    charge_rate_pct_per_h=40.0,
    rental_trip_km=1.5,
    rental_rest=REST,
)


def _opened(charge_pct: float = 70.0) -> Car:
    car = replace(
        seed_fleet(1, seed=3, now=NOW)[0], lat=53.9023, lon=27.5615, charge_pct=charge_pct
    )
    apply_command(car, CommandType.OPEN, NOW)
    return car


def _live(car: Car, until: datetime, start: datetime = NOW) -> datetime:
    rng = random.Random(1)  # noqa: S311 - not used for security
    now = start
    while now < until:
        now += TICK
        step_fleet([car], TICK, PARAMS, rng, now)
    return now


def _mode(car: Car) -> CarMode:
    return car.mode


def test_an_opened_car_rests_then_drives_a_short_hop() -> None:
    car = _opened()
    start = (car.lat, car.lon)

    _live(car, NOW + REST - TICK)
    assert (car.mode, car.ignition_on) == (CarMode.PARKED, False)

    _live(car, NOW + REST + 2 * TICK)
    assert (_mode(car), car.ignition_on, car.driven_by_client) == (CarMode.DRIVING, True, True)
    assert (car.headlights_on, car.gear) == (True, Gear.DRIVE)
    destination = car.destination
    assert destination is not None
    hop = distance_km(*start, destination.lat, destination.lon)
    assert MIN_CLIENT_TRIP_KM <= hop <= PARAMS.rental_trip_km + 0.01


def test_a_clients_hop_ends_in_park_with_the_lights_off_and_the_doors_open() -> None:
    car = _opened()
    charge = car.charge_pct
    now = _live(car, NOW + REST + 2 * TICK)
    assert _mode(car) is CarMode.DRIVING

    rng = random.Random(1)  # noqa: S311 - not used for security
    # 1.5 km at 30 km/h takes at most three minutes.
    for _ in range(40):
        now += TICK
        step_fleet([car], TICK, PARAMS, rng, now)
        if _mode(car) is not CarMode.DRIVING:
            break

    assert (_mode(car), car.ignition_on, car.doors_locked) == (CarMode.PARKED, False, False)
    assert (car.headlights_on, car.gear) == (False, Gear.PARK)
    assert car.rest_until == now + REST
    assert car.charge_pct < charge


def test_a_driving_car_cannot_be_locked() -> None:
    car = _opened()
    _live(car, NOW + REST + 2 * TICK)

    with pytest.raises(CommandRejectedError, match="ignition_on"):
        apply_command(car, CommandType.CLOSE, NOW)


def test_a_locked_or_immobilized_car_does_not_drive() -> None:
    locked = _opened()
    apply_command(locked, CommandType.CLOSE, NOW)
    immobilized = _opened()
    apply_command(immobilized, CommandType.LOCK_ENGINE, NOW)

    for car in (locked, immobilized):
        _live(car, NOW + 3 * REST)
        assert car.mode is CarMode.PARKED


def test_a_rented_car_with_little_charge_is_not_taken_to_a_charger() -> None:
    car = _opened(charge_pct=10.0)

    _live(car, NOW + TICK)

    assert car.mode is not CarMode.CHARGING


def test_the_demo_stops_a_rented_car_on_the_spot() -> None:
    car = _opened()
    _live(car, NOW + REST + 2 * TICK)
    fleet = FleetService([car], timedelta(minutes=10))
    at = NOW + REST + 3 * TICK

    stopped = fleet.park_for_demo(car.id, timedelta(minutes=10), at)

    assert (stopped.mode, stopped.ignition_on, stopped.doors_locked) == (
        CarMode.PARKED,
        False,
        False,
    )
    assert (stopped.headlights_on, stopped.gear) == (False, Gear.PARK)
    assert stopped.rest_until == at + timedelta(minutes=10)


def test_the_demo_does_not_stop_a_car_nobody_rents() -> None:
    car = seed_fleet(1, seed=3, now=NOW)[0]
    fleet = FleetService([car], timedelta(minutes=10))

    with pytest.raises(CommandRejectedError, match="not_rented"):
        fleet.park_for_demo(car.id, timedelta(minutes=10), NOW)


def _demo_settings(*, enabled: bool) -> Settings:
    return Settings(
        command_secret=SecretStr(COMMAND_SECRET),
        telemetry_secret=SecretStr(TELEMETRY_SECRET),
        car_count=2,
        seed=7,
        simulation_enabled=False,
        demo_endpoints=enabled,
    )


async def _park(app: FastAPI, clock: FakeClock, car_id: uuid.UUID) -> httpx.Response:
    path = f"/demo/cars/{car_id}/park"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        return await c.post(path, content=b"", headers=signed("POST", path, b"", clock()))


async def test_the_demo_endpoint_is_off_by_default(clock: FakeClock) -> None:
    app = create_app(_demo_settings(enabled=False), clock)
    fleet: FleetService = app.state.fleet

    response = await _park(app, clock, fleet.list_cars()[0].id)

    assert response.status_code == 404


async def test_the_demo_endpoint_parks_an_opened_car(clock: FakeClock) -> None:
    app = create_app(_demo_settings(enabled=True), clock)
    fleet: FleetService = app.state.fleet
    [rented, idle] = [car.id for car in fleet.list_cars()]
    fleet.execute(rented, uuid.uuid4(), CommandType.OPEN, clock())

    parked = await _park(app, clock, rented)
    refused = await _park(app, clock, idle)

    assert parked.status_code == 200
    body = json.loads(parked.content)
    assert (body["ignition_on"], body["headlights_on"], body["gear"]) == (False, False, "P")
    assert (refused.status_code, refused.json()["detail"]) == (409, "command_rejected:not_rented")
