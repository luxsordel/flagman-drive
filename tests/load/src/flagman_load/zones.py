"""Which cars can end a rental where they stand: the API's rule, applied to /map/zones."""

from collections.abc import Sequence
from dataclasses import dataclass

Ring = Sequence[Sequence[float]]


@dataclass(frozen=True)
class Zone:
    kind: str
    # GeoJSON: the outer ring, then the holes, each as [lon, lat] points.
    rings: Sequence[Ring]


def _in_ring(ring: Ring, lat: float, lon: float) -> bool:
    inside = False
    previous = ring[-1]
    for point in ring:
        if (point[1] > lat) != (previous[1] > lat):
            at_lat = (previous[0] - point[0]) * (lat - point[1]) / (previous[1] - point[1])
            if lon < at_lat + point[0]:
                inside = not inside
        previous = point
    return inside


def _in_zone(zone: Zone, lat: float, lon: float) -> bool:
    outer, *holes = zone.rings
    return _in_ring(outer, lat, lon) and not any(_in_ring(hole, lat, lon) for hole in holes)


def can_finish_at(zones: Sequence[Zone], lat: float, lon: float) -> bool:
    allowed = any(z.kind == "allowed" and _in_zone(z, lat, lon) for z in zones)
    forbidden = any(z.kind == "forbidden" and _in_zone(z, lat, lon) for z in zones)
    return allowed and not forbidden
