from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import CarOnMap, MapArea, MapQuery

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

    @classmethod
    def of(cls, car: CarOnMap) -> "MapCarOut":
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
        )


class MapCarList(BaseModel):
    items: list[MapCarOut]
