"""Virtual car state and command rules. Pure Python: no HTTP, no I/O."""

import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# Rough bounding box of Minsk inside the ring road.
MINSK_LAT_RANGE = (53.84, 53.97)
MINSK_LON_RANGE = (27.40, 27.70)


class CommandType(StrEnum):
    OPEN = "open"
    CLOSE = "close"
    LOCK_ENGINE = "lock_engine"


class CommandRejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class Car:
    id: uuid.UUID
    lat: float
    lon: float
    battery_pct: int
    doors_locked: bool
    ignition_on: bool
    engine_locked: bool
    updated_at: datetime


def apply_command(car: Car, command: CommandType, now: datetime) -> None:
    match command:
        case CommandType.OPEN:
            car.doors_locked = False
        case CommandType.CLOSE:
            # Locking a running car would strand the driver outside it.
            if car.ignition_on:
                raise CommandRejectedError("ignition_on")
            car.doors_locked = True
        case CommandType.LOCK_ENGINE:
            # Like real immobilizers, this blocks the next start instead of stopping a moving car.
            car.engine_locked = True
    car.updated_at = now


def seed_fleet(count: int, seed: int, now: datetime) -> list[Car]:
    # A fixed seed gives every developer the same cars, so ids can be shared in docs and seed data.
    rng = random.Random(seed)  # noqa: S311 - not used for security
    return [
        Car(
            id=uuid.UUID(int=rng.getrandbits(128), version=4),
            lat=round(rng.uniform(*MINSK_LAT_RANGE), 6),
            lon=round(rng.uniform(*MINSK_LON_RANGE), 6),
            battery_pct=rng.randint(20, 100),
            doors_locked=True,
            ignition_on=False,
            engine_locked=False,
            updated_at=now,
        )
        for _ in range(count)
    ]
