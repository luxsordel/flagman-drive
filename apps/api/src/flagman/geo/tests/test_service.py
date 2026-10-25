from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.geo import repository, service
from flagman.geo.domain import ZoneKind, parse_polygon
from flagman.geo.repository import ZoneDraft
from flagman.geo.seed import DEMO_ZONE_ID

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
# North of the demo zone, so a seeded database does not change the answers.
SQUARE = [(27.80, 54.05), (27.82, 54.05), (27.82, 54.07), (27.80, 54.07), (27.80, 54.05)]
INNER = [(27.805, 54.055), (27.815, 54.055), (27.815, 54.065), (27.805, 54.065), (27.805, 54.055)]
CENTER = GeoPoint(lat=54.06, lon=27.81)


@pytest.fixture
async def db(integration_settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_engine(integration_settings)
    try:
        async with create_session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


async def _add(db: AsyncSession, ring: list[tuple[float, float]], kind: ZoneKind) -> None:
    draft = ZoneDraft(name="test", kind=kind, polygon=parse_polygon([ring]))
    assert await repository.insert_zone(db, draft, NOW) is not None


async def test_rental_ends_in_an_allowed_zone_outside_forbidden_ones(db: AsyncSession) -> None:
    # Rolled back at the end: the zones of this test never reach the others.
    async with db.begin():
        await _add(db, SQUARE, ZoneKind.ALLOWED)
        await _add(db, INNER, ZoneKind.FORBIDDEN)

        center = await service.can_end_rental_at(db, CENTER)
        between = await service.can_end_rental_at(db, GeoPoint(lat=54.052, lon=27.81))
        # On the west edges: they run along meridians, which are the same line on the sphere
        # and on the plane, so the point lies exactly on the border for both.
        allowed_edge = await service.can_end_rental_at(db, GeoPoint(lat=54.06, lon=27.80))
        forbidden_edge = await service.can_end_rental_at(db, GeoPoint(lat=54.06, lon=27.805))
        outside = await service.can_end_rental_at(db, GeoPoint(lat=54.10, lon=27.81))
        await db.rollback()

    assert (center, between, allowed_edge, forbidden_edge, outside) == (
        False,
        True,
        True,
        False,
        False,
    )


async def test_disabled_zone_no_longer_allows(db: AsyncSession) -> None:
    async with db.begin():
        draft = ZoneDraft(name="test", kind=ZoneKind.ALLOWED, polygon=parse_polygon([SQUARE]))
        zone_id = await repository.insert_zone(db, draft, NOW)
        assert zone_id is not None
        before = await service.can_end_rental_at(db, CENTER)
        await repository.disable_zone(db, zone_id, NOW)
        after = await service.can_end_rental_at(db, CENTER)
        await db.rollback()

    assert (before, after) == (True, False)


async def test_distance_is_in_meters(db: AsyncSession) -> None:
    # 0.01 degree of latitude is about 1113 m anywhere.
    distance = await service.distance_m(db, CENTER, GeoPoint(lat=54.07, lon=27.81))

    assert 1100 < distance < 1120
    assert await service.distance_m(db, CENTER, CENTER) == 0


async def test_seed_adds_the_demo_zone_once(db: AsyncSession) -> None:
    await service.seed_demo_zone(db, NOW)

    assert not await service.seed_demo_zone(db, NOW)
    zone = await service.get_zone(db, DEMO_ZONE_ID)
    assert zone.kind is ZoneKind.ALLOWED
    # The emulator parks its cars around the center of Minsk.
    assert await service.can_end_rental_at(db, GeoPoint(lat=53.9023, lon=27.5619))
