"""Public interface of the tariffs for other modules.

Reads join the caller's transaction if one is open, so a rental (stage 3) fixes the tariff
version in the same transaction as its booking. Tariff changes are staff commands: each runs in a
transaction of its own, together with its audit event.
"""

from collections.abc import Awaitable, Callable, Iterable
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
from flagman.fleet import service as fleet
from flagman.fleet.domain import StoredCarModel
from flagman.pricing import repository
from flagman.pricing.domain import (
    CarModelNotFoundError,
    CarModelRef,
    Interval,
    ModelTariff,
    Quote,
    TariffChangedError,
    TariffExistsError,
    TariffNotFoundError,
    TariffPrices,
    TariffVersion,
    TariffVersionNotFoundError,
    calculate,
)
from flagman.pricing.seed import DEMO_PRICES

logger = structlog.get_logger(__name__)


async def _read[T](db: AsyncSession, read: Callable[[], Awaitable[T]]) -> T:
    if db.in_transaction():
        return await read()
    async with db.begin():
        return await read()


def _ref(model: StoredCarModel) -> CarModelRef:
    return CarModelRef(id=model.id, brand=model.spec.brand, name=model.spec.name)


async def list_tariffs(db: AsyncSession) -> list[ModelTariff]:
    """Every car model with its current tariff version, or without one, by brand and name."""

    async def read() -> list[ModelTariff]:
        models = await fleet.list_models(db)
        versions = await repository.current_versions(db)
        return [ModelTariff(_ref(model), versions.get(model.id)) for model in models]

    return await _read(db, read)


async def get_tariff(db: AsyncSession, tariff_id: UUID) -> ModelTariff:
    async def read() -> ModelTariff:
        version = await repository.current_version_of_tariff(db, tariff_id)
        if version is None:
            raise TariffNotFoundError
        model = await fleet.get_model(db, version.model_id)
        if model is None:
            raise RuntimeError("a tariff points at a missing car model")
        return ModelTariff(_ref(model), version)

    return await _read(db, read)


async def list_versions(db: AsyncSession, tariff_id: UUID) -> list[TariffVersion]:
    """Newest first; raises TariffNotFoundError for an unknown tariff."""
    versions = await _read(db, lambda: repository.list_versions(db, tariff_id))
    if not versions:
        raise TariffNotFoundError
    return versions


async def current_version(db: AsyncSession, model_id: UUID) -> TariffVersion:
    """The version a rental of a car of this model is booked with now.

    Raises TariffNotFoundError when the model has no tariff: such a car is not rented out.
    """
    version = await _read(db, lambda: repository.current_version_of_model(db, model_id))
    if version is None:
        raise TariffNotFoundError
    return version


async def current_versions(
    db: AsyncSession, model_ids: Iterable[UUID]
) -> dict[UUID, TariffVersion]:
    """Current versions of the given models, by model id; models without a tariff are left out."""
    wanted = set(model_ids)
    versions = await _read(db, lambda: repository.current_versions(db))
    return {model_id: version for model_id, version in versions.items() if model_id in wanted}


async def get_version(db: AsyncSession, version_id: UUID) -> TariffVersion:
    version = await _read(db, lambda: repository.get_version(db, version_id))
    if version is None:
        raise TariffVersionNotFoundError
    return version


async def quote(db: AsyncSession, version_id: UUID, intervals: Iterable[Interval]) -> Quote:
    """The price of a rental's intervals by the version it was booked with (pricing.domain)."""
    version = await get_version(db, version_id)
    return calculate(intervals, version.prices)


@dataclass(frozen=True)
class TariffEditor:
    """Tariff changes of one staff member; every change goes to the audit log with it."""

    db: AsyncSession
    staff_id: UUID
    origin: RequestOrigin

    async def create(self, model_id: UUID, prices: TariffPrices, now: datetime) -> ModelTariff:
        """Raises CarModelNotFoundError or TariffExistsError."""
        async with self.db.begin():
            model = await fleet.get_model(self.db, model_id)
            if model is None:
                raise CarModelNotFoundError
            tariff_id = await repository.insert_tariff(self.db, model_id, now)
            if tariff_id is None:
                raise TariffExistsError
            await repository.insert_version(
                self.db,
                repository.NewVersion(tariff_id, 1, prices, now, self.staff_id),
            )
            await self._record(AuditAction.TARIFF_CREATED, tariff_id, 1, prices)
            version = await repository.current_version_of_tariff(self.db, tariff_id)
        if version is None:
            raise RuntimeError("the new tariff vanished")
        logger.info("tariff_created", tariff_id=str(tariff_id), model_id=str(model_id))
        return ModelTariff(_ref(model), version)

    async def update(
        self, tariff_id: UUID, based_on: int, prices: TariffPrices, now: datetime
    ) -> ModelTariff:
        """Saves a new version that applies from now on; rentals booked before keep theirs.

        `based_on` is the version number the admin saw: if someone saved a newer one meanwhile,
        TariffChangedError is raised instead of silently overwriting their prices.
        """
        async with self.db.begin():
            current = await repository.current_version_of_tariff(
                self.db, tariff_id, for_update=True
            )
            if current is None:
                raise TariffNotFoundError
            if current.number != based_on:
                raise TariffChangedError
            number = current.number + 1
            await repository.insert_version(
                self.db,
                repository.NewVersion(tariff_id, number, prices, now, self.staff_id),
            )
            await self._record(AuditAction.TARIFF_UPDATED, tariff_id, number, prices)
            version = await repository.current_version_of_tariff(self.db, tariff_id)
            model = await fleet.get_model(self.db, current.model_id)
        if version is None or model is None:
            raise RuntimeError("the changed tariff vanished")
        logger.info("tariff_updated", tariff_id=str(tariff_id), version=number)
        return ModelTariff(_ref(model), version)

    async def _record(
        self, action: AuditAction, tariff_id: UUID, number: int, prices: TariffPrices
    ) -> None:
        # The prices are in the event: the audit log answers "who set this price" on its own.
        await audit.record(
            self.db,
            AuditEntry(
                Actor(ActorType.STAFF, self.staff_id),
                action,
                self.origin,
                AuditTarget(AuditTargetType.TARIFF, tariff_id),
                details={
                    "version": number,
                    "driving_per_minute": prices.driving_per_minute,
                    "parked_per_minute": prices.parked_per_minute,
                    "waiting_per_minute": prices.waiting_per_minute,
                    "free_waiting_minutes": prices.free_waiting_minutes,
                    "hold_amount": prices.hold_amount,
                },
            ),
        )


async def seed_demo_tariffs(db: AsyncSession, now: datetime) -> int:
    """Gives every car model without a tariff the demo one; returns how many were added.

    Models with a tariff keep it, also after it was changed in the admin panel.
    """
    created = 0
    async with db.begin():
        for model in await fleet.list_models(db):
            tariff_id = await repository.insert_tariff(db, model.id, now)
            if tariff_id is None:
                continue
            await repository.insert_version(
                db, repository.NewVersion(tariff_id, 1, DEMO_PRICES, now, None)
            )
            created += 1
    return created
