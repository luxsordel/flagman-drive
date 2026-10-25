"""Coordinates shared by the modules that store or compare places: fleet and geo."""

from dataclasses import dataclass

# Coordinates as GPS gives them; geography measures distances in meters on the sphere.
SRID_WGS84 = 4326


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not (-90 <= self.lat <= 90 and -180 <= self.lon <= 180):  # noqa: PLR2004
            raise ValueError("coordinates out of range")
