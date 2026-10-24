from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# The emulator runs at most 1000 cars (EMULATOR_CAR_COUNT); a snapshot is about 200 bytes of JSON,
# so the body limit leaves room for a full batch and stops anything far larger before parsing.
MAX_TELEMETRY_CARS = 1000
MAX_TELEMETRY_BODY_BYTES = 512 * 1024


class EmulatorCarState(BaseModel):
    """car_emulator.schemas.CarState."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    battery_pct: int = Field(ge=0, le=100)
    doors_locked: bool
    ignition_on: bool
    # Accepted but not stored yet: it matters once the API sends commands to cars.
    engine_locked: bool
    updated_at: AwareDatetime


class EmulatorTelemetryBatch(BaseModel):
    """car_emulator.schemas.TelemetryBatch."""

    model_config = ConfigDict(extra="forbid")

    sent_at: AwareDatetime
    cars: list[EmulatorCarState] = Field(max_length=MAX_TELEMETRY_CARS)


class TelemetryReceived(BaseModel):
    applied: int
    stale: int
    unknown_car: int
    from_future: int
