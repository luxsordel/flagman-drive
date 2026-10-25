import uuid
from datetime import UTC, datetime

import pytest

from flagman.core.geo import GeoPoint
from flagman.geo.domain import (
    MAX_ZONE_VERTICES,
    Zone,
    ZoneKind,
    ZonePolygon,
    ZonePolygonHolesError,
    ZonePolygonNotClosedError,
    ZonePolygonOutsideAreaError,
    ZonePolygonSelfIntersectingError,
    ZonePolygonTooFewVerticesError,
    ZonePolygonTooManyVerticesError,
    can_end_rental_at,
    parse_polygon,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

# [lon, lat] as in GeoJSON: a square of about 1.3 x 2.2 km in the center of Minsk.
SQUARE = [(27.55, 53.89), (27.57, 53.89), (27.57, 53.91), (27.55, 53.91), (27.55, 53.89)]
# A smaller square inside SQUARE, around its center.
INNER = [(27.555, 53.895), (27.565, 53.895), (27.565, 53.905), (27.555, 53.905), (27.555, 53.895)]


def _zone(ring: list[tuple[float, float]], kind: ZoneKind, *, active: bool = True) -> Zone:
    return Zone(
        id=uuid.uuid4(),
        name="zone",
        kind=kind,
        polygon=parse_polygon([ring]),
        created_at=NOW,
        updated_at=NOW,
        disabled_at=None if active else NOW,
    )


def test_parse_polygon_keeps_the_closed_ring() -> None:
    polygon = parse_polygon([SQUARE])

    assert polygon == ZonePolygon(ring=tuple(GeoPoint(lat=lat, lon=lon) for lon, lat in SQUARE))
    assert polygon.vertex_count == 4


def test_parse_polygon_accepts_either_orientation() -> None:
    assert parse_polygon([SQUARE[::-1]]).vertex_count == 4


def test_parse_polygon_refuses_an_open_ring() -> None:
    with pytest.raises(ZonePolygonNotClosedError):
        parse_polygon([[*SQUARE[:-1], (27.551, 53.89)]])


@pytest.mark.parametrize(
    "ring",
    [
        pytest.param(SQUARE[:2] + SQUARE[:1], id="three-points"),
        pytest.param([(27.55, 53.89), (27.56, 53.90), (27.57, 53.91), (27.55, 53.89)], id="line"),
        pytest.param([(27.55, 53.89)] * 4, id="one-point"),
        pytest.param(
            [(27.55, 53.89), (27.57, 53.89), (27.57, 53.89), (27.55, 53.89)], id="repeated"
        ),
    ],
)
def test_parse_polygon_refuses_a_polygon_without_area(ring: list[tuple[float, float]]) -> None:
    with pytest.raises(ZonePolygonTooFewVerticesError):
        parse_polygon([ring])


def test_parse_polygon_takes_vertices_up_to_the_limit() -> None:
    assert parse_polygon([_circle(MAX_ZONE_VERTICES)]).vertex_count == MAX_ZONE_VERTICES

    with pytest.raises(ZonePolygonTooManyVerticesError):
        parse_polygon([_circle(MAX_ZONE_VERTICES + 1)])


def test_parse_polygon_refuses_holes() -> None:
    with pytest.raises(ZonePolygonHolesError):
        parse_polygon([SQUARE, INNER])


def test_parse_polygon_refuses_no_rings() -> None:
    with pytest.raises(ZonePolygonHolesError):
        parse_polygon([])


def test_parse_polygon_refuses_crossing_edges() -> None:
    bow_tie = [(27.55, 53.89), (27.57, 53.91), (27.57, 53.89), (27.55, 53.91), (27.55, 53.89)]

    with pytest.raises(ZonePolygonSelfIntersectingError):
        parse_polygon([bow_tie])


@pytest.mark.parametrize(
    "vertex",
    [
        pytest.param((53.90, 27.56), id="swapped-lat-lon"),
        pytest.param((2.756, 53.90), id="lost-digit"),
        pytest.param((27.56, 54.5), id="north-of-minsk"),
    ],
)
def test_parse_polygon_refuses_vertices_outside_the_pilot_area(
    vertex: tuple[float, float],
) -> None:
    ring = [SQUARE[0], SQUARE[1], vertex, SQUARE[3], SQUARE[0]]

    with pytest.raises(ZonePolygonOutsideAreaError):
        parse_polygon([ring])


def test_rental_ends_inside_an_allowed_zone_only() -> None:
    zones = [_zone(SQUARE, ZoneKind.ALLOWED)]

    assert can_end_rental_at(GeoPoint(lat=53.90, lon=27.56), zones)
    assert not can_end_rental_at(GeoPoint(lat=53.92, lon=27.56), zones)
    assert not can_end_rental_at(GeoPoint(lat=53.90, lon=27.56), [])


def test_border_of_an_allowed_zone_allows() -> None:
    zones = [_zone(SQUARE, ZoneKind.ALLOWED)]

    assert can_end_rental_at(GeoPoint(lat=53.89, lon=27.56), zones)
    assert can_end_rental_at(GeoPoint(lat=53.89, lon=27.55), zones)


def test_forbidden_zone_cuts_a_piece_out_of_the_allowed_one() -> None:
    zones = [_zone(SQUARE, ZoneKind.ALLOWED), _zone(INNER, ZoneKind.FORBIDDEN)]

    assert not can_end_rental_at(GeoPoint(lat=53.90, lon=27.56), zones)
    assert can_end_rental_at(GeoPoint(lat=53.892, lon=27.56), zones)


def test_border_of_a_forbidden_zone_forbids() -> None:
    zones = [_zone(SQUARE, ZoneKind.ALLOWED), _zone(INNER, ZoneKind.FORBIDDEN)]

    assert not can_end_rental_at(GeoPoint(lat=53.895, lon=27.56), zones)


def test_forbidden_zone_alone_allows_nothing() -> None:
    assert not can_end_rental_at(GeoPoint(lat=53.92, lon=27.56), [_zone(INNER, ZoneKind.FORBIDDEN)])


def test_disabled_zones_do_not_count() -> None:
    allowed_off = [_zone(SQUARE, ZoneKind.ALLOWED, active=False)]
    forbidden_off = [
        _zone(SQUARE, ZoneKind.ALLOWED),
        _zone(INNER, ZoneKind.FORBIDDEN, active=False),
    ]

    assert not can_end_rental_at(GeoPoint(lat=53.90, lon=27.56), allowed_off)
    assert can_end_rental_at(GeoPoint(lat=53.90, lon=27.56), forbidden_off)


def _circle(vertices: int) -> list[tuple[float, float]]:
    """A regular polygon around the center of Minsk, closed."""
    import math  # noqa: PLC0415 - only this helper needs it

    ring = [
        (
            27.56 + 0.02 * math.cos(2 * math.pi * step / vertices),
            53.90 + 0.01 * math.sin(2 * math.pi * step / vertices),
        )
        for step in range(vertices)
    ]
    return [*ring, ring[0]]
