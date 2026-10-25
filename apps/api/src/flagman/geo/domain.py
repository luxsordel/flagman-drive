"""Zones where a rental may end, and the checks of their polygons. No I/O here."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from http import HTTPStatus
from uuid import UUID

from shapely import Point, Polygon, make_valid

from flagman.core.errors import AppError
from flagman.core.geo import GeoPoint

ZONE_NAME_MAX_LENGTH = 100
# A city district drawn by hand needs tens of vertices; the cap keeps every point-in-zone check
# and every map response small.
MAX_ZONE_VERTICES = 500
# A ring is closed: the first point is repeated at the end, as GeoJSON requires.
MIN_RING_POINTS = 4
# About one square meter in Minsk: anything smaller is rounding noise around a line.
_MIN_AREA_DEG2 = 1e-10


class ZoneKind(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    ALLOWED = "allowed"
    # Cuts a piece out of the allowed zones it lies in: a courtyard, a park, a closed area.
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class Bounds:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def contains(self, point: GeoPoint) -> bool:
        return (
            self.min_lat <= point.lat <= self.max_lat and self.min_lon <= point.lon <= self.max_lon
        )


# The pilot runs in Minsk: the ring road plus about 25 km around it. A vertex outside is a typo
# in the coordinates (swapped latitude and longitude, a lost digit), not a real zone.
PILOT_AREA = Bounds(min_lat=53.65, min_lon=27.15, max_lat=54.15, max_lon=27.95)


class ZoneNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "zone-not-found"
    title = "Zone not found"


class ZoneDisabledError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "zone-disabled"
    title = "The zone is disabled and cannot be changed"


class ZonePolygonError(AppError):
    """Each reason has its own type, so the admin panel shows a text for it."""

    status = HTTPStatus.UNPROCESSABLE_ENTITY


class ZonePolygonNotClosedError(ZonePolygonError):
    slug = "zone-polygon-not-closed"
    title = "The first and the last point of the polygon must be the same"


class ZonePolygonTooFewVerticesError(ZonePolygonError):
    slug = "zone-polygon-too-few-vertices"
    title = "A polygon needs at least three distinct vertices"


class ZonePolygonTooManyVerticesError(ZonePolygonError):
    slug = "zone-polygon-too-many-vertices"
    title = f"A polygon may have at most {MAX_ZONE_VERTICES} vertices"


class ZonePolygonHolesError(ZonePolygonError):
    slug = "zone-polygon-holes"
    title = "A zone has one outer ring; cut pieces out with forbidden zones instead"


class ZonePolygonSelfIntersectingError(ZonePolygonError):
    slug = "zone-polygon-self-intersecting"
    title = "The polygon edges cross each other"


class ZonePolygonOutsideAreaError(ZonePolygonError):
    slug = "zone-polygon-outside-area"
    title = "The polygon goes outside the pilot area around Minsk"


@dataclass(frozen=True)
class ZonePolygon:
    """A checked outer ring, closed; build it with `parse_polygon`."""

    ring: tuple[GeoPoint, ...]

    @property
    def vertex_count(self) -> int:
        return len(self.ring) - 1


@dataclass(frozen=True)
class Zone:
    id: UUID
    name: str
    kind: ZoneKind
    polygon: ZonePolygon
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None


def normalize_zone_name(name: str) -> str:
    """Operators tell zones apart by name, so it cannot be empty.

    Python's `strip` also drops control characters that pydantic's own trimming keeps.
    """
    name = name.strip()
    if not name or len(name) > ZONE_NAME_MAX_LENGTH:
        raise ValueError(f"zone name must be 1-{ZONE_NAME_MAX_LENGTH} characters")
    return name


def parse_polygon(
    rings: Sequence[Sequence[tuple[float, float]]], area: Bounds = PILOT_AREA
) -> ZonePolygon:
    """Checks GeoJSON `Polygon` coordinates: rings of `[lon, lat]` pairs."""
    if len(rings) != 1:
        raise ZonePolygonHolesError
    ring = [GeoPoint(lat=lat, lon=lon) for lon, lat in rings[0]]
    if len(ring) < MIN_RING_POINTS:
        raise ZonePolygonTooFewVerticesError
    if ring[0] != ring[-1]:
        raise ZonePolygonNotClosedError
    if len(ring) - 1 > MAX_ZONE_VERTICES:
        raise ZonePolygonTooManyVerticesError
    if not all(area.contains(point) for point in ring):
        raise ZonePolygonOutsideAreaError
    shape = _shape(ring)
    # Repeated or collinear points pass the length check above but leave a line or a point.
    # The repaired shape is measured: a bow tie's two halves cancel out in the raw signed area.
    if len(set(ring)) < MIN_RING_POINTS - 1 or make_valid(shape).area < _MIN_AREA_DEG2:
        raise ZonePolygonTooFewVerticesError
    if not shape.is_valid:
        raise ZonePolygonSelfIntersectingError
    return ZonePolygon(ring=tuple(ring))


def can_end_rental_at(point: GeoPoint, zones: Sequence[Zone]) -> bool:
    """Inside an active allowed zone and outside every active forbidden one.

    A point on an edge counts as inside both kinds: an allowed zone's border still allows,
    a forbidden zone's border already forbids, so a doubtful spot is always refused.
    Planar geometry over degrees is accurate enough at city scale: the edges differ from the
    great-circle ones by centimeters.
    """
    spot = Point(point.lon, point.lat)
    inside = [zone for zone in zones if zone.is_active and _shape(zone.polygon.ring).covers(spot)]
    return any(zone.kind is ZoneKind.ALLOWED for zone in inside) and not any(
        zone.kind is ZoneKind.FORBIDDEN for zone in inside
    )


def _shape(ring: Sequence[GeoPoint]) -> Polygon:
    return Polygon([(point.lon, point.lat) for point in ring])
