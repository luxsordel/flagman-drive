import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict

from car_emulator.domain import Car, CommandType


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: uuid.UUID
    type: CommandType


class CarState(BaseModel):
    id: uuid.UUID
    lat: float
    lon: float
    battery_pct: int
    doors_locked: bool
    ignition_on: bool
    engine_locked: bool
    updated_at: AwareDatetime

    @classmethod
    def from_domain(cls, car: Car, as_of: datetime | None = None) -> "CarState":
        """`updated_at` is when the car last changed, or `as_of` for a snapshot taken then."""
        return cls(
            id=car.id,
            lat=car.lat,
            lon=car.lon,
            battery_pct=car.battery_pct,
            doors_locked=car.doors_locked,
            ignition_on=car.ignition_on,
            engine_locked=car.engine_locked,
            updated_at=as_of or car.updated_at,
        )


class CommandResult(BaseModel):
    command_id: uuid.UUID
    car: CarState


class TelemetryBatch(BaseModel):
    sent_at: AwareDatetime
    cars: list[CarState]


class Health(BaseModel):
    status: str
