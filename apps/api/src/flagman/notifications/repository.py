from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.notifications.domain import (
    Notification,
    NotificationCategory,
    NotificationEvent,
    NotificationPreferences,
    OutboxItem,
    OutboxStatus,
    PushDevice,
    PushPlatform,
)
from flagman.notifications.models import NotificationOptOut, OutboxNotification
from flagman.notifications.models import PushDevice as DeviceRow


async def save_device(  # noqa: PLR0913 - one row's columns
    db: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    token: str,
    platform: PushPlatform,
    now: datetime,
) -> None:
    # A token belongs to the phone, not the account: when someone else signs in on it, their
    # session takes it over and the previous one stops getting anything.
    await db.execute(
        delete(DeviceRow).where(DeviceRow.token == token, DeviceRow.session_id != session_id)
    )
    statement = insert(DeviceRow).values(
        user_id=user_id,
        session_id=session_id,
        token=token,
        platform=platform.value,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[DeviceRow.session_id],
            set_={"token": token, "platform": platform.value, "updated_at": now},
        )
    )


async def delete_session_device(db: AsyncSession, user_id: UUID, session_id: UUID) -> None:
    await db.execute(
        delete(DeviceRow).where(DeviceRow.user_id == user_id, DeviceRow.session_id == session_id)
    )


async def delete_devices(db: AsyncSession, device_ids: Collection[UUID]) -> None:
    if device_ids:
        await db.execute(delete(DeviceRow).where(DeviceRow.id.in_(device_ids)))


async def get_devices(db: AsyncSession, user_ids: Collection[UUID]) -> list[PushDevice]:
    rows = await db.scalars(select(DeviceRow).where(DeviceRow.user_id.in_(user_ids)))
    return [
        PushDevice(id=row.id, user_id=row.user_id, session_id=row.session_id, token=row.token)
        for row in rows
    ]


async def get_preferences(db: AsyncSession, user_id: UUID) -> NotificationPreferences:
    categories = await db.scalars(
        select(NotificationOptOut.category).where(NotificationOptOut.user_id == user_id)
    )
    return NotificationPreferences(frozenset(NotificationCategory(c) for c in categories))


async def save_preferences(
    db: AsyncSession, user_id: UUID, preferences: NotificationPreferences, now: datetime
) -> None:
    await db.execute(delete(NotificationOptOut).where(NotificationOptOut.user_id == user_id))
    if preferences.disabled:
        await db.execute(
            insert(NotificationOptOut).values(
                [
                    {"user_id": user_id, "category": category.value, "created_at": now}
                    for category in sorted(preferences.disabled)
                ]
            )
        )


async def add_to_outbox(
    db: AsyncSession, user_id: UUID, notification: Notification, expires_at: datetime, now: datetime
) -> bool:
    """False when the same event was already stored."""
    statement = (
        insert(OutboxNotification)
        .values(
            user_id=user_id,
            event=notification.event.value,
            rental_id=notification.rental_id,
            params=dict(notification.params),
            dedup_key=notification.dedup_key,
            status=OutboxStatus.PENDING.value,
            attempts=0,
            next_attempt_at=now,
            expires_at=expires_at,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[OutboxNotification.dedup_key])
        .returning(OutboxNotification.id)
    )
    return (await db.scalar(statement)) is not None


@dataclass(frozen=True)
class Claimed:
    items: list[OutboxItem]
    # Rows whose event or params no template takes any more, e.g. queued before a change of the
    # templates: given up at once, so they do not block the queue.
    unreadable: list[str]


async def claim_due(db: AsyncSession, now: datetime, lease_until: datetime, limit: int) -> Claimed:
    """Takes the due rows for one attempt. Until `lease_until` no one else takes them, so a
    sender that died midway leaves them to be tried again, not lost."""
    rows = (
        await db.scalars(
            select(OutboxNotification)
            .where(
                OutboxNotification.status == OutboxStatus.PENDING.value,
                OutboxNotification.next_attempt_at <= now,
            )
            .order_by(OutboxNotification.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    items: list[OutboxItem] = []
    unreadable: list[str] = []
    for row in rows:
        row.attempts += 1
        try:
            notification = Notification(
                event=NotificationEvent(row.event), rental_id=row.rental_id, params=row.params
            )
        except ValueError:
            row.status = OutboxStatus.UNDELIVERED.value
            row.finished_at = now
            unreadable.append(row.event)
            continue
        row.next_attempt_at = lease_until
        items.append(
            OutboxItem(
                id=row.id,
                user_id=row.user_id,
                notification=notification,
                attempts=row.attempts,
                expires_at=row.expires_at,
            )
        )
    return Claimed(items=items, unreadable=unreadable)


async def withdraw(
    db: AsyncSession, rental_id: UUID, events: Collection[NotificationEvent], now: datetime
) -> None:
    await db.execute(
        update(OutboxNotification)
        .where(
            OutboxNotification.rental_id == rental_id,
            OutboxNotification.event.in_([event.value for event in events]),
            OutboxNotification.status == OutboxStatus.PENDING.value,
        )
        .values(status=OutboxStatus.UNDELIVERED.value, finished_at=now)
    )


async def expire_overdue(db: AsyncSession, now: datetime) -> int:
    """Gives up on pending notifications that are too late to be of use; returns how many."""
    expired = await db.scalars(
        update(OutboxNotification)
        .where(
            OutboxNotification.status == OutboxStatus.PENDING.value,
            OutboxNotification.expires_at <= now,
        )
        .values(status=OutboxStatus.UNDELIVERED.value, finished_at=now)
        .returning(OutboxNotification.id)
    )
    return len(expired.all())


async def finish(
    db: AsyncSession, outbox_ids: Collection[UUID], status: OutboxStatus, now: datetime
) -> None:
    if outbox_ids:
        await db.execute(
            update(OutboxNotification)
            .where(OutboxNotification.id.in_(outbox_ids))
            .values(status=status.value, finished_at=now)
        )


async def retry_at(db: AsyncSession, outbox_id: UUID, at: datetime) -> None:
    await db.execute(
        update(OutboxNotification)
        .where(OutboxNotification.id == outbox_id)
        .values(next_attempt_at=at)
    )


async def delete_finished_before(db: AsyncSession, before: datetime) -> int:
    deleted = await db.scalars(
        delete(OutboxNotification)
        .where(
            OutboxNotification.status != OutboxStatus.PENDING.value,
            OutboxNotification.finished_at < before,
        )
        .returning(OutboxNotification.id)
    )
    return len(deleted.all())
