import random
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from car_emulator.domain import (
    CarMode,
    CommandRejectedError,
    CommandType,
    SimulationParams,
    seed_fleet,
    step_fleet,
)
from car_emulator.fleet import CarNotFoundError, DuplicateCommandError, FleetService, Simulation

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)
PARAMS = SimulationParams(
    trip_share=0.5,
    speed_kmh=30.0,
    idle_drain_pct_per_h=1.0,
    drive_drain_pct_per_km=0.2,
    charge_below_pct=25.0,
    charge_until_pct=90.0,
    charge_rate_pct_per_h=40.0,
)


def _rng() -> random.Random:
    return random.Random(1)  # noqa: S311 - not used for security


def _simulated(time_scale: float = 1.0) -> FleetService:
    simulation = Simulation(params=PARAMS, rng=_rng(), time_scale=time_scale)
    return FleetService(seed_fleet(6, seed=1, now=NOW), WINDOW, simulation)


@pytest.fixture
def fleet() -> FleetService:
    return FleetService(seed_fleet(3, seed=1, now=NOW), WINDOW)


def _car_id(fleet: FleetService) -> uuid.UUID:
    return fleet.list_cars()[0].id


def test_execute_changes_state(fleet: FleetService) -> None:
    car_id = _car_id(fleet)
    result = fleet.execute(car_id, uuid.uuid4(), CommandType.OPEN, NOW)
    assert not result.doors_locked
    assert not fleet.get_car(car_id).doors_locked


def test_returned_cars_are_copies(fleet: FleetService) -> None:
    car_id = _car_id(fleet)
    fleet.get_car(car_id).doors_locked = False
    fleet.list_cars()[0].doors_locked = False
    assert fleet.get_car(car_id).doors_locked


def test_unknown_car(fleet: FleetService) -> None:
    with pytest.raises(CarNotFoundError):
        fleet.get_car(uuid.uuid4())
    with pytest.raises(CarNotFoundError):
        fleet.execute(uuid.uuid4(), uuid.uuid4(), CommandType.OPEN, NOW)


def test_duplicate_command_id_is_rejected_within_window(fleet: FleetService) -> None:
    car_id = _car_id(fleet)
    command_id = uuid.uuid4()
    fleet.execute(car_id, command_id, CommandType.OPEN, NOW)
    fleet.execute(car_id, uuid.uuid4(), CommandType.CLOSE, NOW)
    with pytest.raises(DuplicateCommandError):
        fleet.execute(car_id, command_id, CommandType.OPEN, NOW + WINDOW - timedelta(seconds=1))
    assert fleet.get_car(car_id).doors_locked


def test_command_id_is_forgotten_after_window(fleet: FleetService) -> None:
    car_id = _car_id(fleet)
    command_id = uuid.uuid4()
    fleet.execute(car_id, command_id, CommandType.OPEN, NOW)
    fleet.execute(car_id, command_id, CommandType.OPEN, NOW + WINDOW)


def test_rejected_command_id_cannot_be_reused(fleet: FleetService) -> None:
    car_id = _car_id(fleet)
    command_id = uuid.uuid4()
    fleet._cars[car_id].ignition_on = True
    with pytest.raises(CommandRejectedError):
        fleet.execute(car_id, command_id, CommandType.CLOSE, NOW)
    with pytest.raises(DuplicateCommandError):
        fleet.execute(car_id, command_id, CommandType.CLOSE, NOW)


def test_without_simulation_nothing_changes(fleet: FleetService) -> None:
    before = fleet.list_cars()

    fleet.simulate(NOW)
    fleet.simulate(NOW + timedelta(hours=5))

    assert fleet.list_cars() == before


def test_first_simulate_only_starts_the_count() -> None:
    fleet = _simulated()
    before = fleet.list_cars()

    fleet.simulate(NOW + timedelta(hours=5))

    assert fleet.list_cars() == before


def test_simulate_lives_the_time_since_the_previous_call() -> None:
    fleet = _simulated()
    expected = seed_fleet(6, seed=1, now=NOW)
    step_fleet(expected, timedelta(minutes=10), PARAMS, _rng(), NOW + timedelta(minutes=10))

    fleet.simulate(NOW)
    fleet.simulate(NOW + timedelta(minutes=10))

    assert fleet.list_cars() == expected
    assert sum(car.mode is CarMode.DRIVING for car in fleet.list_cars()) == 3


def test_time_scale_speeds_the_fleet_up() -> None:
    fast = _simulated(time_scale=60)
    expected = seed_fleet(6, seed=1, now=NOW)
    later = NOW + timedelta(minutes=1)
    step_fleet(expected, timedelta(hours=1), PARAMS, _rng(), later)

    fast.simulate(NOW)
    fast.simulate(later)

    assert fast.list_cars() == expected


def test_commands_between_steps_do_not_change_the_draws() -> None:
    quiet, busy = _simulated(), _simulated()
    car_id = quiet.list_cars()[0].id
    for fleet in (quiet, busy):
        fleet.simulate(NOW)
    busy.execute(car_id, uuid.uuid4(), CommandType.LOCK_ENGINE, NOW)
    busy.execute(car_id, uuid.uuid4(), CommandType.UNLOCK_ENGINE, NOW)

    for fleet in (quiet, busy):
        fleet.simulate(NOW + timedelta(minutes=10))

    assert [replace(car, updated_at=NOW) for car in busy.list_cars()] == [
        replace(car, updated_at=NOW) for car in quiet.list_cars()
    ]


def test_close_is_rejected_while_a_car_drives() -> None:
    fleet = _simulated()
    fleet.simulate(NOW)
    fleet.simulate(NOW + timedelta(minutes=1))
    driving = next(car for car in fleet.list_cars() if car.mode is CarMode.DRIVING)

    fleet.execute(driving.id, uuid.uuid4(), CommandType.OPEN, NOW)
    with pytest.raises(CommandRejectedError):
        fleet.execute(driving.id, uuid.uuid4(), CommandType.CLOSE, NOW)
