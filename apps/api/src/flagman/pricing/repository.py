from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.pricing.domain import TariffPrices
from flagman.pricing.domain import TariffVersion as Version
from flagman.pricing.models import Tariff, TariffVersion


def _select_versions() -> Select[TariffVersion, UUID]:
    return select(TariffVersion, Tariff.model_id).join(Tariff, TariffVersion.tariff_id == Tariff.id)


def _to_version(row: TariffVersion, model_id: UUID) -> Version:
    return Version(
        id=row.id,
        tariff_id=row.tariff_id,
        model_id=model_id,
        number=row.number,
        prices=TariffPrices(
            driving_per_minute=row.driving_per_minute,
            parked_per_minute=row.parked_per_minute,
            waiting_per_minute=row.waiting_per_minute,
            free_waiting_minutes=row.free_waiting_minutes,
            hold_amount=row.hold_amount,
        ),
        valid_from=row.valid_from,
        created_by=row.created_by,
    )


def _latest(query: Select[TariffVersion, UUID]) -> Select[TariffVersion, UUID]:
    # Versions of a tariff are numbered in the order they were saved, under the tariff row lock.
    return query.order_by(TariffVersion.tariff_id, TariffVersion.number.desc()).ext(
        distinct_on(TariffVersion.tariff_id)
    )


async def current_versions(
    db: AsyncSession, model_ids: frozenset[UUID] | None = None
) -> dict[UUID, Version]:
    """The latest version of each tariff, of the given models or of all, by car model id."""
    query = _select_versions()
    if model_ids is not None:
        query = query.where(Tariff.model_id.in_(model_ids))
    rows = await db.execute(_latest(query))
    return {model_id: _to_version(row, model_id) for row, model_id in rows}


async def current_version_of_model(db: AsyncSession, model_id: UUID) -> Version | None:
    row = (
        await db.execute(_latest(_select_versions().where(Tariff.model_id == model_id)))
    ).one_or_none()
    return _to_version(*row) if row else None


async def current_version_of_tariff(
    db: AsyncSession, tariff_id: UUID, *, for_update: bool = False
) -> Version | None:
    """With `for_update` the tariff row stays locked until commit, so versions get their numbers
    one at a time."""
    if for_update:
        locked = await db.scalar(select(Tariff.id).where(Tariff.id == tariff_id).with_for_update())
        if locked is None:
            return None
    row = (
        await db.execute(_latest(_select_versions().where(Tariff.id == tariff_id)))
    ).one_or_none()
    return _to_version(*row) if row else None


async def get_version(db: AsyncSession, version_id: UUID) -> Version | None:
    row = (await db.execute(_select_versions().where(TariffVersion.id == version_id))).one_or_none()
    return _to_version(*row) if row else None


async def list_versions(db: AsyncSession, tariff_id: UUID) -> list[Version]:
    rows = await db.execute(
        _select_versions()
        .where(TariffVersion.tariff_id == tariff_id)
        .order_by(TariffVersion.number.desc())
    )
    return [_to_version(row, model_id) for row, model_id in rows]


async def insert_tariff(db: AsyncSession, model_id: UUID, now: datetime) -> UUID | None:
    """Returns the new tariff's id, or None if the model has a tariff already."""
    created: UUID | None = await db.scalar(
        insert(Tariff)
        .values(model_id=model_id, created_at=now)
        .on_conflict_do_nothing(index_elements=[Tariff.model_id])
        .returning(Tariff.id)
    )
    return created


@dataclass(frozen=True)
class NewVersion:
    tariff_id: UUID
    number: int
    prices: TariffPrices
    valid_from: datetime
    created_by: UUID | None


async def insert_version(db: AsyncSession, version: NewVersion) -> UUID:
    prices = version.prices
    version_id: UUID | None = await db.scalar(
        insert(TariffVersion)
        .values(
            tariff_id=version.tariff_id,
            number=version.number,
            driving_per_minute=prices.driving_per_minute,
            parked_per_minute=prices.parked_per_minute,
            waiting_per_minute=prices.waiting_per_minute,
            free_waiting_minutes=prices.free_waiting_minutes,
            hold_amount=prices.hold_amount,
            valid_from=version.valid_from,
            created_by=version.created_by,
        )
        .returning(TariffVersion.id)
    )
    if version_id is None:
        raise RuntimeError("INSERT ... RETURNING gave no id")
    return version_id
