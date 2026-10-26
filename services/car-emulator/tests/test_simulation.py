import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from car_emulator.domain import (
    MINSK_LAT_RANGE,
    MINSK_LON_RANGE,
    Car,
    CarMode,
    Destination,
    SimulationParams,
    distance_km,
    drive_toward,
    seed_fleet,
    start_trip,
    step_car,
    step_fleet,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
TICK = timedelta(seconds=5)

PARAMS = SimulationParams(
    trip_share=0.2,
    speed_kmh=30.0,
    idle_drain_pct_per_h=1.0,
    drive_drain_pct_per_km=0.2,
    charge_below_pct=25.0,
    charge_until_pct=90.0,
    charge_rate_pct_per_h=40.0,
)
# Opposite corners of the city: about 25 km, far more than one step drives.
SOUTH_WEST = Destination(lat=MINSK_LAT_RANGE[0], lon=MINSK_LON_RANGE[0])
NORTH_EAST = Destination(lat=MINSK_LAT_RANGE[1], lon=MINSK_LON_RANGE[1])


@pytest.fixture
def car() -> Car:
    return replace(
        seed_fleet(1, seed=1, now=NOW)[0],
        lat=SOUTH_WEST.lat,
        lon=SOUTH_WEST.lon,
        charge_pct=60.5,
    )


def _rng(seed: int | str) -> random.Random:
    return random.Random(seed)  # noqa: S311 - not used for security


def _mode(car: Car) -> CarMode:
    # Through a call, so the type checker does not keep the mode narrowed between steps.
    return car.mode


def _driving(car: Car, destination: Destination = NORTH_EAST) -> Car:
    start_trip(car, destination, NOW)
    return car


def _run(cars: list[Car], steps: int, rng: random.Random, tick: timedelta = TICK) -> datetime:
    now = NOW
    for _ in range(steps):
        now += tick
        step_fleet(cars, tick, PARAMS, rng, now)
    return now


def test_parked_car_drains_slowly(car: Car) -> None:
    step_car(car, timedelta(hours=2), PARAMS, NOW + timedelta(hours=2))

    assert car.charge_pct == pytest.approx(58.5)
    assert car.battery_pct == 58
    assert car.mode is CarMode.PARKED
    assert (car.lat, car.lon) == (SOUTH_WEST.lat, SOUTH_WEST.lon)


def test_drain_adds_up_over_short_steps(car: Car) -> None:
    # One percent an hour is a thousandth of a percent per tick: it must not round away.
    now = NOW
    for _ in range(720):
        now += TICK
        step_car(car, TICK, PARAMS, now)

    assert car.charge_pct == pytest.approx(59.5)


def test_updated_at_moves_only_with_a_visible_change(car: Car) -> None:
    step_car(car, TICK, PARAMS, NOW + TICK)
    assert car.updated_at == NOW

    later = NOW + timedelta(hours=1)
    step_car(car, timedelta(hours=1), PARAMS, later)
    assert car.battery_pct == 59
    assert car.updated_at == later


def test_charge_never_goes_below_zero(car: Car) -> None:
    params = replace(PARAMS, charge_below_pct=0.0)
    car.charge_pct = 0.5

    step_car(car, timedelta(hours=10), params, NOW)

    assert car.charge_pct == 0.0
    assert car.battery_pct == 0


def test_low_car_charges_in_place_up_to_the_threshold(car: Car) -> None:
    car.charge_pct = 26.0
    step_car(car, timedelta(hours=2), PARAMS, NOW)
    assert car.mode is CarMode.CHARGING

    step_car(car, timedelta(hours=1), PARAMS, NOW)
    assert _mode(car) is CarMode.CHARGING
    assert car.charge_pct == pytest.approx(64.0)

    step_car(car, timedelta(hours=5), PARAMS, NOW)
    assert _mode(car) is CarMode.PARKED
    assert car.charge_pct == 90.0
    assert (car.lat, car.lon) == (SOUTH_WEST.lat, SOUTH_WEST.lon)
    assert not car.ignition_on


def test_charge_never_goes_above_100(car: Car) -> None:
    params = replace(PARAMS, charge_until_pct=100.0)
    car.mode = CarMode.CHARGING
    car.charge_pct = 99.0

    step_car(car, timedelta(hours=10), params, NOW)

    assert car.charge_pct == 100.0
    assert car.battery_pct == 100


def test_driving_car_moves_toward_its_destination(car: Car) -> None:
    _driving(car)
    before = distance_km(car.lat, car.lon, NORTH_EAST.lat, NORTH_EAST.lon)

    step_car(car, timedelta(minutes=6), PARAMS, NOW + timedelta(minutes=6))

    after = distance_km(car.lat, car.lon, NORTH_EAST.lat, NORTH_EAST.lon)
    assert before - after == pytest.approx(3.0, abs=0.01)
    assert car.mode is CarMode.DRIVING
    assert car.ignition_on
    assert car.charge_pct == pytest.approx(60.5 - 3.0 * 0.2)


def test_driving_drains_faster_than_parking(car: Car) -> None:
    parked = replace(car)
    _driving(car)

    step_car(car, timedelta(minutes=30), PARAMS, NOW)
    step_car(parked, timedelta(minutes=30), PARAMS, NOW)

    assert car.charge_pct < parked.charge_pct


def test_trip_reaches_the_destination_and_stops(car: Car) -> None:
    _driving(car)
    car.doors_locked = False

    step_car(car, timedelta(hours=2), PARAMS, NOW)

    assert (car.lat, car.lon) == (NORTH_EAST.lat, NORTH_EAST.lon)
    assert car.mode is CarMode.PARKED
    assert car.destination is None
    assert not car.ignition_on
    assert car.doors_locked
    # Charged only for the way, not for the rest of the step spent parked.
    way_km = distance_km(SOUTH_WEST.lat, SOUTH_WEST.lon, NORTH_EAST.lat, NORTH_EAST.lon)
    assert car.charge_pct == pytest.approx(60.5 - way_km * 0.2)


def test_empty_car_stops_where_it_is(car: Car) -> None:
    car.charge_pct = 1.0
    _driving(car)

    step_car(car, timedelta(hours=2), PARAMS, NOW)

    assert car.charge_pct == 0.0
    assert distance_km(SOUTH_WEST.lat, SOUTH_WEST.lon, car.lat, car.lon) == pytest.approx(
        5.0, abs=0.01
    )
    assert not car.ignition_on
    assert car.mode is CarMode.CHARGING


def test_drive_toward_reports_arrival(car: Car) -> None:
    near = Destination(lat=round(car.lat + 0.001, 6), lon=car.lon)

    assert not drive_toward(car, NORTH_EAST, 1.0, 0.2)
    assert drive_toward(car, near, 50.0, 0.2)
    assert (car.lat, car.lon) == (near.lat, near.lon)


@pytest.mark.parametrize(
    "change",
    [
        {"engine_locked": True},
        {"doors_locked": False},
        {"ignition_on": True},
        {"mode": CarMode.CHARGING},
        {"charge_pct": 24.0},
    ],
)
def test_some_cars_never_leave(change: dict[str, object]) -> None:
    cars = [replace(car, **change) for car in seed_fleet(10, seed=2, now=NOW)]  # type: ignore[arg-type]

    _run(cars, 20, _rng(1))

    assert all(car.mode is not CarMode.DRIVING for car in cars)


def test_only_the_trip_share_of_the_fleet_drives() -> None:
    cars = seed_fleet(30, seed=3, now=NOW)
    for car in cars:
        car.charge_pct = 80.0

    rng = _rng(1)
    for _ in range(200):
        _run(cars, 1, rng, tick=timedelta(minutes=1))
        assert sum(car.mode is CarMode.DRIVING for car in cars) <= 6

    assert sum(car.mode is CarMode.DRIVING for car in cars) == 6


def test_trips_start_with_ignition_on() -> None:
    cars = seed_fleet(10, seed=4, now=NOW)
    for car in cars:
        car.charge_pct = 80.0

    now = _run(cars, 1, _rng(1))

    [first, second] = [car for car in cars if car.mode is CarMode.DRIVING]
    for car in (first, second):
        assert car.ignition_on
        assert car.destination is not None
        assert car.updated_at == now


def test_cars_stay_in_minsk_for_a_long_demo() -> None:
    cars = seed_fleet(30, seed=5, now=NOW)

    _run(cars, 2000, _rng(1), tick=timedelta(minutes=1))

    for car in cars:
        assert MINSK_LAT_RANGE[0] <= car.lat <= MINSK_LAT_RANGE[1]
        assert MINSK_LON_RANGE[0] <= car.lon <= MINSK_LON_RANGE[1]
        assert 0 <= car.charge_pct <= 100
        # Charging keeps the fleet alive: nobody runs flat over 33 hours.
        assert car.charge_pct > 0


def test_cars_get_somewhere_over_time() -> None:
    cars = seed_fleet(30, seed=6, now=NOW)
    start = {car.id: (car.lat, car.lon) for car in cars}

    _run(cars, 600, _rng(1), tick=timedelta(minutes=1))

    assert sum((car.lat, car.lon) != start[car.id] for car in cars) >= 6


def test_same_seed_and_times_give_the_same_fleet() -> None:
    def simulate() -> list[Car]:
        cars = seed_fleet(30, seed=7, now=NOW)
        _run(cars, 500, _rng("7:simulation"), tick=timedelta(seconds=30))
        return cars

    assert simulate() == simulate()


def test_no_time_no_change() -> None:
    cars = seed_fleet(10, seed=8, now=NOW)
    before = [replace(car) for car in cars]

    step_fleet(cars, timedelta(0), PARAMS, _rng(1), NOW)
    step_fleet(cars, timedelta(seconds=-5), PARAMS, _rng(1), NOW)

    assert cars == before
