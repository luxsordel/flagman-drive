"""Virtual car state and command rules. Pure Python: no HTTP, no I/O."""

import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# Rough bounding box of Minsk inside the ring road.
MINSK_LAT_RANGE = (53.84, 53.97)
MINSK_LON_RANGE = (27.40, 27.70)

# Car ids are a contract with the API seed (apps/api, flagman.fleet): it derives the same ids from
# EMULATOR_SEED and EMULATOR_CAR_COUNT, so telemetry lands on the cars the API knows. Changing the
# namespace or the name format changes every id on both sides.
CAR_ID_NAMESPACE = uuid.UUID("b3dbc708-f7ca-4013-88f6-d3564a1f0b2c")


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


def car_id(seed: int, index: int) -> uuid.UUID:
    return uuid.uuid5(CAR_ID_NAMESPACE, f"{seed}:{index}")


def seed_fleet(count: int, seed: int, now: datetime) -> list[Car]:
    # A fixed seed gives every developer the same cars. Ids do not depend on the random state,
    # so the API can derive them without repeating how positions and charge are drawn.
    rng = random.Random(seed)  # noqa: S311 - not used for security
    return [
        Car(
            id=car_id(seed, index),
            lat=round(rng.uniform(*MINSK_LAT_RANGE), 6),
            lon=round(rng.uniform(*MINSK_LON_RANGE), 6),
            battery_pct=rng.randint(20, 100),
            doors_locked=True,
            ignition_on=False,
            engine_locked=False,
            updated_at=now,
        )
        for index in range(count)
    ]
