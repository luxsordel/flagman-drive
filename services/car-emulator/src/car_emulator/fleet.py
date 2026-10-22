"""In-memory fleet: the emulator's only state, lost on restart by design."""

import uuid
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta

from car_emulator.domain import Car, CommandType, apply_command


class CarNotFoundError(Exception):
    pass


class DuplicateCommandError(Exception):
    pass


class FleetService:
    def __init__(self, cars: Iterable[Car], replay_window: timedelta) -> None:
        self._cars = {car.id: car for car in cars}
        self._replay_window = replay_window
        self._seen_commands: dict[uuid.UUID, datetime] = {}

    def list_cars(self) -> list[Car]:
        # Copies, so callers cannot change fleet state behind the service's back.
        return [replace(car) for car in self._cars.values()]

    def get_car(self, car_id: uuid.UUID) -> Car:
        return replace(self._get(car_id))

    def execute(
        self, car_id: uuid.UUID, command_id: uuid.UUID, command: CommandType, now: datetime
    ) -> Car:
        self._forget_expired_commands(now)
        if command_id in self._seen_commands:
            raise DuplicateCommandError
        car = self._get(car_id)
        # Remembered even if the command is rejected: a retry needs a new id, as with real APIs.
        self._seen_commands[command_id] = now + self._replay_window
        apply_command(car, command, now)
        return replace(car)

    def _get(self, car_id: uuid.UUID) -> Car:
        try:
            return self._cars[car_id]
        except KeyError:
            raise CarNotFoundError from None

    def _forget_expired_commands(self, now: datetime) -> None:
        expired = [cid for cid, expires_at in self._seen_commands.items() if expires_at <= now]
        for cid in expired:
            del self._seen_commands[cid]
