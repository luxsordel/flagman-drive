from datetime import UTC, datetime

import pytest

from car_emulator.domain import (
    MINSK_LAT_RANGE,
    MINSK_LON_RANGE,
    Car,
    CommandRejectedError,
    CommandType,
    apply_command,
    seed_fleet,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 22, 12, 5, tzinfo=UTC)


@pytest.fixture
def car() -> Car:
    return seed_fleet(1, seed=1, now=NOW)[0]


def test_seed_is_deterministic() -> None:
    assert seed_fleet(10, seed=3, now=NOW) == seed_fleet(10, seed=3, now=NOW)
    assert seed_fleet(10, seed=3, now=NOW) != seed_fleet(10, seed=4, now=NOW)


def test_seeded_cars_are_parked_in_minsk() -> None:
    cars = seed_fleet(200, seed=1, now=NOW)
    assert len({car.id for car in cars}) == 200
    for car in cars:
        assert MINSK_LAT_RANGE[0] <= car.lat <= MINSK_LAT_RANGE[1]
        assert MINSK_LON_RANGE[0] <= car.lon <= MINSK_LON_RANGE[1]
        assert 0 <= car.battery_pct <= 100
        assert car.doors_locked
        assert not car.ignition_on
        assert not car.engine_locked


def test_open_unlocks_doors(car: Car) -> None:
    apply_command(car, CommandType.OPEN, LATER)
    assert not car.doors_locked
    assert car.updated_at == LATER


def test_close_locks_doors(car: Car) -> None:
    car.doors_locked = False
    apply_command(car, CommandType.CLOSE, LATER)
    assert car.doors_locked


def test_close_is_rejected_while_ignition_is_on(car: Car) -> None:
    car.doors_locked = False
    car.ignition_on = True
    with pytest.raises(CommandRejectedError) as exc_info:
        apply_command(car, CommandType.CLOSE, LATER)
    assert exc_info.value.reason == "ignition_on"
    assert not car.doors_locked
    assert car.updated_at == NOW


def test_lock_engine_does_not_stop_a_running_car(car: Car) -> None:
    car.ignition_on = True
    apply_command(car, CommandType.LOCK_ENGINE, LATER)
    assert car.engine_locked
    assert car.ignition_on
