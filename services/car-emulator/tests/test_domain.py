import uuid
from datetime import UTC, datetime

import pytest

from car_emulator.domain import (
    MINSK_LAT_RANGE,
    MINSK_LON_RANGE,
    Car,
    CommandRejectedError,
    CommandType,
    apply_command,
    car_id,
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


def test_car_ids_match_the_api_seed() -> None:
    # Reference vector shared with apps/api/src/flagman/fleet/tests/test_domain.py
    # (test_emulator_car_ids_match_the_emulator): if this changes, telemetry no longer lands on the
    # cars the API seeded.
    cars = seed_fleet(30, seed=42, now=NOW)

    assert cars[0].id == uuid.UUID("a0b2fa60-ac1d-5ee4-b7a5-2b037e18bd35")
    assert cars[29].id == uuid.UUID("c6069517-01a5-59b6-a91c-291f0738f6d3")
    assert [car.id for car in cars] == [car_id(42, index) for index in range(30)]


def test_car_ids_do_not_depend_on_the_count() -> None:
    assert seed_fleet(5, seed=42, now=NOW) == seed_fleet(30, seed=42, now=NOW)[:5]


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


def test_unlock_engine_lets_the_car_start_again(car: Car) -> None:
    apply_command(car, CommandType.LOCK_ENGINE, NOW)
    apply_command(car, CommandType.UNLOCK_ENGINE, LATER)
    assert not car.engine_locked
    assert car.updated_at == LATER
