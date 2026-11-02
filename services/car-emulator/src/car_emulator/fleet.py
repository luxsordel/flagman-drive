"""In-memory fleet: the emulator's only state, lost on restart by design."""

import random
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from car_emulator.domain import (
    Car,
    CommandRejectedError,
    CommandType,
    SimulationParams,
    apply_command,
    is_rented,
    step_fleet,
    stop_for_client,
)


class CarNotFoundError(Exception):
    pass


class DuplicateCommandError(Exception):
    pass


@dataclass(frozen=True)
class Simulation:
    params: SimulationParams
    # Its own generator: commands and API calls between steps must not change what happens next.
    rng: random.Random
    time_scale: float


class FleetService:
    def __init__(
        self,
        cars: Iterable[Car],
        replay_window: timedelta,
        simulation: Simulation | None = None,
    ) -> None:
        self._cars = {car.id: car for car in cars}
        self._replay_window = replay_window
        self._seen_commands: dict[uuid.UUID, datetime] = {}
        self._simulation = simulation
        self._simulated_at: datetime | None = None

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

    def park_for_demo(self, car_id: uuid.UUID, rest: timedelta, now: datetime) -> Car:
        """Stops a rented car where it is and keeps it there for `rest`, so a demo can finish
        the rental without waiting for the drive to end. Raises CommandRejectedError
        `not_rented` for a car nobody has opened."""
        car = self._get(car_id)
        if not is_rented(car):
            raise CommandRejectedError("not_rented")
        stop_for_client(car, rest, now)
        return replace(car)

    def simulate(self, now: datetime) -> None:
        """Lives the time since the previous call; the first call only starts the count."""
        if self._simulation is None:
            return
        if self._simulated_at is not None:
            elapsed = (now - self._simulated_at) * self._simulation.time_scale
            sim = self._simulation
            step_fleet(list(self._cars.values()), elapsed, sim.params, sim.rng, now)
        self._simulated_at = now

    def _get(self, car_id: uuid.UUID) -> Car:
        try:
            return self._cars[car_id]
        except KeyError:
            raise CarNotFoundError from None

    def _forget_expired_commands(self, now: datetime) -> None:
        expired = [cid for cid, expires_at in self._seen_commands.items() if expires_at <= now]
        for cid in expired:
            del self._seen_commands[cid]
