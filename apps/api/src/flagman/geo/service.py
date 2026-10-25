"""Public interface of the zones for other modules.

`can_end_rental_at` and `distance_m` join the caller's transaction if one is open, so a rental
(stage 3) checks the spot and ends in one transaction. Zone changes are staff commands: each runs
in a transaction of its own, together with its audit event.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditTarget,
    AuditTargetType,
    RequestOrigin,
)
from flagman.core.geo import GeoPoint
from flagman.geo import domain, repository
from flagman.geo.domain import (
    Zone,
    ZoneDisabledError,
    ZoneKind,
    ZoneNotFoundError,
    ZonePolygon,
)
from flagman.geo.repository import ZoneDraft
from flagman.geo.seed import DEMO_ZONE_ID, DEMO_ZONE_KIND, DEMO_ZONE_NAME, DEMO_ZONE_RING

logger = structlog.get_logger(__name__)

Rings = Sequence[Sequence[tuple[float, float]]]


async def _read[T](db: AsyncSession, read: Callable[[], Awaitable[T]]) -> T:
    if db.in_transaction():
        return await read()
    async with db.begin():
        return await read()


async def list_zones(db: AsyncSession, *, include_disabled: bool = False) -> list[Zone]:
    return await _read(db, lambda: repository.list_zones(db, include_disabled=include_disabled))


async def get_zone(db: AsyncSession, zone_id: UUID) -> Zone:
    zone = await _read(db, lambda: repository.get_zone(db, zone_id))
    if zone is None:
        raise ZoneNotFoundError
    return zone


async def can_end_rental_at(db: AsyncSession, point: GeoPoint) -> bool:
    """Whether a rental may end at the point; the rule is in geo.domain.can_end_rental_at."""
    zones = await _read(db, lambda: repository.active_zones_at(db, point))
    return domain.can_end_rental_at(point, zones)


async def distance_m(db: AsyncSession, a: GeoPoint, b: GeoPoint) -> float:
    """Meters between two points on the Earth's spheroid."""
    return await _read(db, lambda: repository.distance_m(db, a, b))


@dataclass(frozen=True)
class ZoneChange:
    """What an admin sends; checked by `ZoneEditor` before anything is stored."""

    name: str
    kind: ZoneKind
    rings: Rings


@dataclass(frozen=True)
class ZoneEditor:
    """Zone changes of one staff member; every change goes to the audit log with it."""

    db: AsyncSession
    staff_id: UUID
    origin: RequestOrigin

    async def create(self, change: ZoneChange, now: datetime) -> Zone:
        """Raises a ZonePolygonError subclass naming the rule the polygon breaks."""
        draft = _draft(change.name, change.kind, change.rings)
        async with self.db.begin():
            zone_id = await repository.insert_zone(self.db, draft, now)
            if zone_id is None:
                raise RuntimeError("a new zone id clashed with an existing one")
            await self._record(AuditAction.ZONE_CREATED, zone_id, draft.kind, draft.polygon)
            zone = await _get(self.db, zone_id)
        logger.info("zone_created", zone_id=str(zone_id), kind=draft.kind)
        return zone

    async def update(self, zone_id: UUID, change: ZoneChange, now: datetime) -> Zone:
        """Replaces the name, the kind and the outline at once.

        Raises ZoneDisabledError for a disabled zone: it is history, a new zone is drawn instead.
        """
        draft = _draft(change.name, change.kind, change.rings)
        async with self.db.begin():
            current = await _get(self.db, zone_id, for_update=True)
            if not current.is_active:
                raise ZoneDisabledError
            await repository.update_zone(self.db, zone_id, draft, now)
            await self._record(AuditAction.ZONE_UPDATED, zone_id, draft.kind, draft.polygon)
            zone = await _get(self.db, zone_id)
        logger.info("zone_updated", zone_id=str(zone_id), kind=draft.kind)
        return zone

    async def disable(self, zone_id: UUID, now: datetime) -> Zone:
        """Takes the zone out of every check; disabling a disabled zone changes nothing."""
        async with self.db.begin():
            current = await _get(self.db, zone_id, for_update=True)
            if not current.is_active:
                return current
            await repository.disable_zone(self.db, zone_id, now)
            await self._record(AuditAction.ZONE_DISABLED, zone_id, current.kind, current.polygon)
            zone = await _get(self.db, zone_id)
        logger.info("zone_disabled", zone_id=str(zone_id))
        return zone

    async def _record(
        self, action: AuditAction, zone_id: UUID, kind: ZoneKind, polygon: ZonePolygon
    ) -> None:
        # Not the name or the outline: the zone itself shows them, the event says who and when.
        await audit.record(
            self.db,
            AuditEntry(
                Actor(ActorType.STAFF, self.staff_id),
                action,
                self.origin,
                AuditTarget(AuditTargetType.ZONE, zone_id),
                details={"kind": kind.value, "vertex_count": polygon.vertex_count},
            ),
        )


async def seed_demo_zone(db: AsyncSession, now: datetime) -> bool:
    """Adds the demo zone unless it is there already; returns whether it was added now."""
    draft = _draft(DEMO_ZONE_NAME, DEMO_ZONE_KIND, [DEMO_ZONE_RING])
    async with db.begin():
        created = await repository.insert_zone(db, draft, now, zone_id=DEMO_ZONE_ID)
    return created is not None


def _draft(name: str, kind: ZoneKind, rings: Rings) -> ZoneDraft:
    return ZoneDraft(
        name=domain.normalize_zone_name(name), kind=kind, polygon=domain.parse_polygon(rings)
    )


async def _get(db: AsyncSession, zone_id: UUID, *, for_update: bool = False) -> Zone:
    zone = await repository.get_zone(db, zone_id, for_update=for_update)
    if zone is None:
        raise ZoneNotFoundError
    return zone
