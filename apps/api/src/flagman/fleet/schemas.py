from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from flagman.audit.domain import validate_details
from flagman.core.geo import GeoPoint
from flagman.fleet.domain import (
    COMMENT_MAX_LENGTH,
    COMMENT_MIN_LENGTH,
    AvailabilityRules,
    Car,
    CarCommand,
    CarOnMap,
    CarStatus,
    CommandOutcome,
    Gear,
    MapArea,
    MapQuery,
    StaffCarFilter,
    range_left_km,
)

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
    engine_locked: bool
    # Optional: an emulator older than #144 does not send them, and its cars stay "unknown".
    headlights_on: bool | None = None
    gear: Gear | None = None
    updated_at: AwareDatetime


class EmulatorCommandResult(BaseModel):
    """car_emulator.schemas.CommandResult."""

    model_config = ConfigDict(extra="forbid")

    command_id: UUID
    car: EmulatorCarState


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


MAP_CARS_DEFAULT = 50
MAP_CARS_MAX = 200

Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]


class ClientPoint(BaseModel):
    """Where the client is, for the distance to cars; never logged or stored."""

    model_config = ConfigDict(extra="forbid")

    lat: Latitude | None = Field(default=None, description="With `lon`: the client's latitude.")
    lon: Longitude | None = Field(default=None, description="With `lat`: the client's longitude.")

    @model_validator(mode="after")
    def _check_pair(self) -> Self:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("give both lat and lon or neither")
        return self

    def point(self) -> GeoPoint | None:
        if self.lat is None or self.lon is None:
            return None
        return GeoPoint(lat=self.lat, lon=self.lon)


class MapCarsQuery(ClientPoint):
    min_lat: Latitude | None = Field(default=None, description="South edge of the visible map.")
    min_lon: Longitude | None = Field(default=None, description="West edge of the visible map.")
    max_lat: Latitude | None = Field(default=None, description="North edge of the visible map.")
    max_lon: Longitude | None = Field(default=None, description="East edge of the visible map.")
    limit: int = Field(default=MAP_CARS_DEFAULT, ge=1, le=MAP_CARS_MAX)

    @model_validator(mode="after")
    def _check_area(self) -> Self:
        edges = (self.min_lat, self.min_lon, self.max_lat, self.max_lon)
        if any(edge is None for edge in edges) and any(edge is not None for edge in edges):
            raise ValueError("give all four edges of the area or none")
        if self.lat is None and self.min_lat is None:
            raise ValueError("give the area, the client's point or both")
        self.query()  # a reversed area fails here, with the other input errors
        return self

    def area(self) -> MapArea | None:
        min_lat, min_lon, max_lat, max_lon = self.min_lat, self.min_lon, self.max_lat, self.max_lon
        if min_lat is None or min_lon is None or max_lat is None or max_lon is None:
            return None
        return MapArea(min_lat, min_lon, max_lat, max_lon)

    def query(self) -> MapQuery:
        return MapQuery(area=self.area(), near=self.point(), limit=self.limit)


class MapTariffOut(BaseModel):
    """Kopecks of BYN; the server counts the price of a rental, the app only shows these."""

    driving_per_minute: int
    parked_per_minute: int


class MapCarOut(BaseModel):
    id: UUID
    brand: str
    model: str
    photo_url: str | None
    lat: float
    lon: float
    battery_pct: int
    range_km: int = Field(description="Estimated from the charge, rounded down.")
    distance_m: int | None = Field(description="From the client's point, if one was given.")
    tariff: MapTariffOut = Field(
        description="The current tariff of the car's model; cars of a model without one are hidden."
    )

    @classmethod
    def of(cls, car: CarOnMap, tariff: MapTariffOut) -> "MapCarOut":
        return cls(
            id=car.id,
            brand=car.brand,
            model=car.model,
            photo_url=car.photo_url,
            lat=car.location.lat,
            lon=car.location.lon,
            battery_pct=car.battery_pct,
            range_km=car.range_km,
            distance_m=car.distance_m,
            tariff=tariff,
        )


class MapCarList(BaseModel):
    items: list[MapCarOut]


class StaffCarsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: list[CarStatus] = Field(
        default_factory=list, description="Only cars in these statuses; repeat for several."
    )
    low_battery: bool = Field(
        default=False, description="Only cars charged below `rules.low_battery_pct`."
    )

    def filter(self) -> StaffCarFilter:
        return StaffCarFilter(statuses=frozenset(self.status), low_battery=self.low_battery)


class StaffCarStateOut(BaseModel):
    lat: float
    lon: float
    battery_pct: int
    range_km: int = Field(description="Estimated from the charge, rounded down.")
    doors_locked: bool
    ignition_on: bool
    engine_locked: bool
    recorded_at: AwareDatetime = Field(description="The car's time of the last telemetry.")


class StaffCarOut(BaseModel):
    id: UUID
    plate: str
    vin: str
    brand: str
    model: str
    status: CarStatus
    state: StaffCarStateOut | None = Field(description="None until the first telemetry arrives.")

    @classmethod
    def of(cls, car: Car) -> "StaffCarOut":
        state = car.state
        return cls(
            id=car.id,
            plate=car.plate,
            vin=car.vin,
            brand=car.model.brand,
            model=car.model.name,
            status=car.status,
            state=StaffCarStateOut(
                lat=state.location.lat,
                lon=state.location.lon,
                battery_pct=state.battery_pct,
                range_km=range_left_km(car.model.range_km, state.battery_pct),
                doors_locked=state.doors_locked,
                ignition_on=state.ignition_on,
                engine_locked=state.engine_locked,
                recorded_at=state.recorded_at,
            )
            if state is not None
            else None,
        )


class FleetRulesOut(BaseModel):
    """The thresholds the panel marks cars by, so it can mark them as time passes too."""

    telemetry_max_age_seconds: int = Field(
        description="Older telemetry is not fresh: the car may be elsewhere by now."
    )
    low_battery_pct: int = Field(description="A lower charge is low: the car is not rented out.")

    @classmethod
    def of(cls, rules: AvailabilityRules) -> "FleetRulesOut":
        return cls(
            telemetry_max_age_seconds=int(rules.max_state_age.total_seconds()),
            low_battery_pct=rules.min_battery_pct,
        )


class StaffCarList(BaseModel):
    items: list[StaffCarOut] = Field(description="By plate.")
    rules: FleetRulesOut
    as_of: AwareDatetime = Field(
        description="The server's time: telemetry age is counted from it, not the browser's clock."
    )

    @classmethod
    def of(cls, cars: list[Car], rules: AvailabilityRules, now: datetime) -> Self:
        return cls(
            items=[StaffCarOut.of(car) for car in cars], rules=FleetRulesOut.of(rules), as_of=now
        )


class CarCommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: CarCommand
    comment: str = Field(
        min_length=COMMENT_MIN_LENGTH,
        max_length=COMMENT_MAX_LENGTH,
        description=(
            "Why the command is sent; goes to the audit log. No phone numbers or emails: "
            "name the rental or the ticket instead."
        ),
    )
    confirm_active_rental: bool = Field(
        default=False,
        description=(
            "Needed for a reserved or rented car: a client holds it, and the command may lock "
            "them out or in. Without it such a command answers `car-in-active-rental`."
        ),
    )

    @field_validator("comment")
    @classmethod
    def _check_comment(cls, comment: str) -> str:
        comment = comment.strip()
        if len(comment) < COMMENT_MIN_LENGTH:
            raise ValueError(f"the comment needs at least {COMMENT_MIN_LENGTH} characters")
        # The audit log takes no personal data; better to say so now than fail after the car moved.
        validate_details({"comment": comment})
        return comment


class CarCommandOut(BaseModel):
    command_id: UUID
    car: StaffCarOut = Field(description="The car after the command.")


# The staff stream (docs/adr/0017). Not in OpenAPI: the admin panel keeps its own copy of these.

STREAM_MESSAGE_MAX_CHARS = 4096


class StreamAuth(BaseModel):
    """The first message of a staff member, and again whenever their access token is renewed."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["auth"]
    token: str = Field(min_length=1)


class StreamSnapshot(StaffCarList):
    type: Literal["snapshot"] = "snapshot"


class StreamCars(BaseModel):
    type: Literal["cars"] = "cars"
    items: list[StaffCarOut]
    as_of: AwareDatetime


class StreamCommandOut(BaseModel):
    command_id: UUID
    car_id: UUID
    command: CarCommand
    outcome: CommandOutcome
    reason: str | None = Field(description="The car's reason code of a refusal.")
    staff_id: UUID
    at: AwareDatetime


class StreamCommand(BaseModel):
    type: Literal["command"] = "command"
    command: StreamCommandOut
