from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from flagman.geo.domain import (
    MAX_ZONE_VERTICES,
    ZONE_NAME_MAX_LENGTH,
    ZoneKind,
    ZonePolygon,
    normalize_zone_name,
)

# The real limits are checked by geo.domain.parse_polygon, which names the broken rule; these
# only stop a huge body before it is parsed.
_RING_INPUT_MAX_POINTS = 2 * MAX_ZONE_VERTICES
_RINGS_INPUT_MAX = 8
_NAME_INPUT_MAX_LENGTH = 1000

Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]


class PolygonGeometry(BaseModel):
    """A GeoJSON `Polygon` (RFC 7946): `[lon, lat]` pairs, the first point repeated at the end."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"]
    coordinates: list[
        Annotated[list[tuple[Longitude, Latitude]], Field(max_length=_RING_INPUT_MAX_POINTS)]
    ] = Field(
        max_length=_RINGS_INPUT_MAX,
        description=(
            f"One outer ring of at most {MAX_ZONE_VERTICES} vertices, without holes: "
            "forbidden zones cut pieces out instead."
        ),
        examples=[
            [[[27.55, 53.89], [27.57, 53.89], [27.57, 53.91], [27.55, 53.91], [27.55, 53.89]]]
        ],
    )

    @classmethod
    def of(cls, polygon: ZonePolygon) -> "PolygonGeometry":
        return cls(type="Polygon", coordinates=[[(point.lon, point.lat) for point in polygon.ring]])


class ZoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str,
        Field(
            max_length=_NAME_INPUT_MAX_LENGTH, description=f"1-{ZONE_NAME_MAX_LENGTH} characters."
        ),
        AfterValidator(normalize_zone_name),
    ]
    kind: ZoneKind
    polygon: PolygonGeometry


class ZoneOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    kind: ZoneKind
    polygon: PolygonGeometry
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None = Field(
        description="Set once the zone is disabled: it no longer counts anywhere."
    )


class ZoneList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ZoneOut]
