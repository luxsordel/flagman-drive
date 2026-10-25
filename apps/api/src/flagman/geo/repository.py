import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import ColumnElement, Select, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from flagman.core.geo import SRID_WGS84, GeoPoint
from flagman.geo.domain import Zone, ZoneKind, ZonePolygon
from flagman.geo.models import Zone as ZoneRow

# Nine decimals of a degree are under a millimeter: the ring comes back as it was drawn.
_GEOJSON_DECIMALS = 9
_AREA_GEOJSON = func.ST_AsGeoJSON(ZoneRow.area, _GEOJSON_DECIMALS).label("area_geojson")


@dataclass(frozen=True)
class ZoneDraft:
    name: str
    kind: ZoneKind
    polygon: ZonePolygon


def _geography(polygon: ZonePolygon) -> ColumnElement[Any]:
    # GeoJSON as a bound parameter, not WKT pasted into SQL: coordinates never become SQL text.
    geojson = json.dumps(
        {"type": "Polygon", "coordinates": [[[point.lon, point.lat] for point in polygon.ring]]}
    )
    return cast(func.ST_SetSRID(func.ST_GeomFromGeoJSON(geojson), SRID_WGS84), Geography)


def _point(point: GeoPoint) -> ColumnElement[Any]:
    return cast(func.ST_SetSRID(func.ST_MakePoint(point.lon, point.lat), SRID_WGS84), Geography)


def _select_zones() -> Select[Any]:
    # The raw geography value is not loaded: the ring comes as GeoJSON text instead.
    return select(ZoneRow, _AREA_GEOJSON).options(defer(ZoneRow.area))


def _to_zone(row: ZoneRow, area_geojson: str) -> Zone:
    ring = json.loads(area_geojson)["coordinates"][0]
    return Zone(
        id=row.id,
        name=row.name,
        kind=ZoneKind(row.kind),
        polygon=ZonePolygon(ring=tuple(GeoPoint(lat=lat, lon=lon) for lon, lat in ring)),
        created_at=row.created_at,
        updated_at=row.updated_at,
        disabled_at=row.disabled_at,
    )


async def list_zones(db: AsyncSession, *, include_disabled: bool) -> list[Zone]:
    query = _select_zones().order_by(ZoneRow.created_at, ZoneRow.id)
    if not include_disabled:
        query = query.where(ZoneRow.disabled_at.is_(None))
    return [_to_zone(*row) for row in await db.execute(query)]


async def get_zone(db: AsyncSession, zone_id: UUID, *, for_update: bool = False) -> Zone | None:
    query = _select_zones().where(ZoneRow.id == zone_id)
    if for_update:
        query = query.with_for_update(of=ZoneRow)
    row = (await db.execute(query)).one_or_none()
    return _to_zone(*row) if row else None


async def insert_zone(
    db: AsyncSession, draft: ZoneDraft, now: datetime, zone_id: UUID | None = None
) -> UUID | None:
    """Returns the new zone's id, or None if a zone with `zone_id` is there already."""
    values: dict[str, Any] = {
        "name": draft.name,
        "kind": draft.kind,
        "area": _geography(draft.polygon),
        "created_at": now,
        "updated_at": now,
    }
    if zone_id is not None:
        values["id"] = zone_id
    created: UUID | None = await db.scalar(
        insert(ZoneRow)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[ZoneRow.id])
        .returning(ZoneRow.id)
    )
    return created


async def update_zone(db: AsyncSession, zone_id: UUID, draft: ZoneDraft, now: datetime) -> None:
    await db.execute(
        update(ZoneRow)
        .where(ZoneRow.id == zone_id)
        .values(name=draft.name, kind=draft.kind, area=_geography(draft.polygon), updated_at=now)
    )


async def disable_zone(db: AsyncSession, zone_id: UUID, now: datetime) -> None:
    await db.execute(
        update(ZoneRow).where(ZoneRow.id == zone_id).values(disabled_at=now, updated_at=now)
    )


async def active_zones_at(db: AsyncSession, point: GeoPoint) -> list[Zone]:
    """Active zones touching the point, border included; the GIST index finds them."""
    query = _select_zones().where(
        ZoneRow.disabled_at.is_(None), func.ST_Intersects(ZoneRow.area, _point(point))
    )
    return [_to_zone(*row) for row in await db.execute(query)]


async def distance_m(db: AsyncSession, a: GeoPoint, b: GeoPoint) -> float:
    # On the spheroid, as every distance the API reports; a cast to geometry would give degrees.
    distance = await db.scalar(select(func.ST_Distance(_point(a), _point(b))))
    if distance is None:
        raise RuntimeError("ST_Distance returned no value")
    return float(distance)
