from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography, Geometry
from sqlalchemy import ColumnElement, Select, cast, exists, func, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from flagman.core.geo import SRID_WGS84, GeoPoint
from flagman.fleet.changes import CHANNEL
from flagman.fleet.domain import (
    AvailabilityRules,
    Car,
    CarIdentityTakenError,
    CarModelSpec,
    CarState,
    CarStatus,
    MapQuery,
    SnapshotOutcome,
)
from flagman.fleet.models import Car as CarRow
from flagman.fleet.models import CarModel

_point = cast(CarRow.location, Geometry)
_LAT = func.ST_Y(_point).label("lat")
_LON = func.ST_X(_point).label("lon")


def _geography(point: GeoPoint) -> ColumnElement[Any]:
    # Bound parameters, not WKT text: coordinates never become part of the SQL string.
    return cast(func.ST_SetSRID(func.ST_MakePoint(point.lon, point.lat), SRID_WGS84), Geography)


def _select_cars() -> Select[Any]:
    # The raw geography value is not loaded: the coordinates come as plain numbers instead.
    return (
        select(CarRow, CarModel, _LAT, _LON)
        .join(CarModel, CarRow.model_id == CarModel.id)
        .options(defer(CarRow.location))
    )


def _to_car(row: CarRow, model: CarModel, lat: float | None, lon: float | None) -> Car:
    state = None
    # The state_complete constraint sets these together; the checks only narrow the types.
    if (
        row.state_at is not None
        and lat is not None
        and lon is not None
        and row.battery_pct is not None
        and row.doors_locked is not None
        and row.ignition_on is not None
        and row.engine_locked is not None
    ):
        state = CarState(
            location=GeoPoint(lat=lat, lon=lon),
            battery_pct=row.battery_pct,
            doors_locked=row.doors_locked,
            ignition_on=row.ignition_on,
            engine_locked=row.engine_locked,
            recorded_at=row.state_at,
        )
    return Car(
        id=row.id,
        model=CarModelSpec(
            brand=model.brand,
            name=model.name,
            seats=model.seats,
            battery_wh=model.battery_wh,
            range_km=model.range_km,
            photo_url=model.photo_url,
        ),
        plate=row.plate,
        vin=row.vin,
        telematics_id=row.telematics_id,
        status=CarStatus(row.status),
        state=state,
    )


async def get_car(db: AsyncSession, car_id: UUID) -> Car | None:
    query = _select_cars().where(CarRow.id == car_id)
    row = (await db.execute(query)).one_or_none()
    return _to_car(*row) if row else None


async def list_cars(db: AsyncSession) -> list[Car]:
    rows = await db.execute(_select_cars().order_by(CarRow.plate))
    return [_to_car(*row) for row in rows]


async def find_rentable_cars(
    db: AsyncSession,
    rules: AvailabilityRules,
    now: datetime,
    query: MapQuery,
    car_id: UUID | None = None,
) -> list[tuple[Car, float | None]]:
    """Cars that pass the availability rule, nearest first when `query.near` is given.

    The rule is repeated in SQL so that the limit counts only cars a client may see; the service
    still checks each one with fleet.domain.is_rentable.
    """
    near, area = query.near, query.area
    distance = func.ST_Distance(CarRow.location, _geography(near)) if near else null()
    select_cars = (
        _select_cars()
        .add_columns(distance.label("distance_m"))
        .where(
            CarRow.status == CarStatus.AVAILABLE,
            CarRow.state_at >= now - rules.max_state_age,
            CarRow.battery_pct >= rules.min_battery_pct,
        )
        .order_by(distance if near else CarRow.id)
        .limit(query.limit)
    )
    if area is not None:
        envelope = func.ST_MakeEnvelope(
            area.min_lon, area.min_lat, area.max_lon, area.max_lat, SRID_WGS84
        )
        select_cars = select_cars.where(
            func.ST_Intersects(CarRow.location, cast(envelope, Geography))
        )
    if car_id is not None:
        select_cars = select_cars.where(CarRow.id == car_id)
    rows = await db.execute(select_cars)
    return [(_to_car(row, model, lat, lon), meters) for row, model, lat, lon, meters in rows]


async def get_cars(db: AsyncSession, car_ids: Collection[UUID]) -> list[Car]:
    rows = await db.execute(_select_cars().where(CarRow.id.in_(car_ids)).order_by(CarRow.plate))
    return [_to_car(*row) for row in rows]


async def notify(db: AsyncSession, payloads: Sequence[str]) -> None:
    """Queues notifications on fleet.changes.CHANNEL; Postgres sends them on commit only."""
    for payload in payloads:
        await db.execute(select(func.pg_notify(CHANNEL, payload)))


async def get_status_for_update(db: AsyncSession, car_id: UUID) -> CarStatus | None:
    status = await db.scalar(select(CarRow.status).where(CarRow.id == car_id).with_for_update())
    return CarStatus(status) if status is not None else None


async def set_status(db: AsyncSession, car_id: UUID, status: CarStatus, now: datetime) -> None:
    await db.execute(
        update(CarRow).where(CarRow.id == car_id).values(status=status, updated_at=now)
    )


async def apply_state(
    db: AsyncSession, telematics_id: UUID, state: CarState, now: datetime
) -> tuple[SnapshotOutcome, UUID | None]:
    """The outcome and, for an applied snapshot, the id of the car it changed."""
    # One conditional update instead of read-then-write: two webhooks for the same car cannot
    # both pass the freshness check and leave the older snapshot stored.
    applied = await db.scalar(
        update(CarRow)
        .where(
            CarRow.telematics_id == telematics_id,
            or_(CarRow.state_at.is_(None), CarRow.state_at < state.recorded_at),
        )
        .values(
            location=_geography(state.location),
            battery_pct=state.battery_pct,
            doors_locked=state.doors_locked,
            ignition_on=state.ignition_on,
            engine_locked=state.engine_locked,
            state_at=state.recorded_at,
            updated_at=now,
        )
        .returning(CarRow.id)
    )
    if applied is not None:
        return SnapshotOutcome.APPLIED, applied
    known = await db.scalar(select(exists().where(CarRow.telematics_id == telematics_id)))
    return (SnapshotOutcome.STALE if known else SnapshotOutcome.UNKNOWN_CAR), None


async def ensure_model(db: AsyncSession, spec: CarModelSpec, now: datetime) -> tuple[UUID, bool]:
    """Returns the model id and whether it was created now; an existing model is left as is."""
    created = await db.scalar(
        insert(CarModel)
        .values(
            brand=spec.brand,
            name=spec.name,
            seats=spec.seats,
            battery_wh=spec.battery_wh,
            range_km=spec.range_km,
            photo_url=spec.photo_url,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[CarModel.brand, CarModel.name])
        .returning(CarModel.id)
    )
    if created is not None:
        return created, True
    existing = await db.scalar(
        select(CarModel.id).where(CarModel.brand == spec.brand, CarModel.name == spec.name)
    )
    if existing is None:
        raise RuntimeError("car model vanished between insert and select")
    return existing, False


@dataclass(frozen=True)
class NewCar:
    model_id: UUID
    plate: str
    vin: str
    telematics_id: UUID
    status: CarStatus


async def add_car_if_absent(db: AsyncSession, car: NewCar, now: datetime) -> bool:
    """Inserts the car unless one with the same telematics id exists.

    Raises CarIdentityTakenError if the plate or the VIN belongs to a car with another
    telematics id.
    """
    try:
        created = await db.scalar(
            insert(CarRow)
            .values(
                model_id=car.model_id,
                plate=car.plate,
                vin=car.vin,
                telematics_id=car.telematics_id,
                status=car.status,
                created_at=now,
                updated_at=now,
            )
            # Only the telematics id counts as "already there": skipping a plate or VIN clash too
            # would keep a car that telemetry never reaches and still report success.
            .on_conflict_do_nothing(index_elements=[CarRow.telematics_id])
            .returning(CarRow.id)
        )
    except IntegrityError as error:
        raise CarIdentityTakenError from error
    return created is not None
