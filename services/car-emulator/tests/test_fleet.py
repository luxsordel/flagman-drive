import uuid
from datetime import UTC, datetime, timedelta

import pytest

from car_emulator.domain import CommandRejectedError, CommandType, seed_fleet
from car_emulator.fleet import CarNotFoundError, DuplicateCommandError, FleetService

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)


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
