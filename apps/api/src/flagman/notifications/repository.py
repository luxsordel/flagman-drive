from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CTE,
    DateTime,
    String,
    Uuid,
    column,
    delete,
    func,
    literal,
    select,
    tuple_,
    update,
    values,
)
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
    SentTicket,
)
from flagman.notifications.models import NotificationOptOut, OutboxNotification, PushTicket
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


async def save_tickets(db: AsyncSession, tickets: Mapping[str, UUID], sent_at: datetime) -> None:
    """Ticket id -> the device the message went to; a device deleted meanwhile is left out."""
    if not tickets:
        return
    sent = values(column("id", String), column("device_id", Uuid), name="sent").data(
        list(tickets.items())
    )
    rows = select(sent.c.id, sent.c.device_id, literal(sent_at)).join(
        DeviceRow, DeviceRow.id == sent.c.device_id
    )
    await db.execute(
        insert(PushTicket)
        .from_select(["id", "device_id", "sent_at"], rows)
        .on_conflict_do_nothing(index_elements=[PushTicket.id])
    )


async def get_tickets_sent_between(
    db: AsyncSession,
    since: datetime,
    until: datetime,
    after: SentTicket | None,
    limit: int,
) -> list[SentTicket]:
    """Oldest first, page by page: the ones after `after`, so a ticket whose receipt is not
    ready yet is not met again on the next page."""
    statement = select(PushTicket).where(PushTicket.sent_at > since, PushTicket.sent_at <= until)
    if after is not None:
        statement = statement.where(
            tuple_(PushTicket.sent_at, PushTicket.id)
            > tuple_(literal(after.sent_at), literal(after.id))
        )
    rows = await db.scalars(statement.order_by(PushTicket.sent_at, PushTicket.id).limit(limit))
    return [SentTicket(id=row.id, device_id=row.device_id, sent_at=row.sent_at) for row in rows]


async def delete_tickets(db: AsyncSession, ticket_ids: Collection[str]) -> None:
    if ticket_ids:
        await db.execute(delete(PushTicket).where(PushTicket.id.in_(ticket_ids)))


async def delete_tickets_sent_before(db: AsyncSession, before: datetime) -> int:
    statement = delete(PushTicket).where(PushTicket.sent_at <= before).returning(PushTicket.id)
    return await _count(db, statement.cte("deleted"))


async def delete_devices_unchanged_since(db: AsyncSession, sent_at: Mapping[UUID, datetime]) -> int:
    """Device id -> when a message the device never got went out. A device registered again
    since then, maybe with a new token, is kept."""
    if not sent_at:
        return 0
    sent = values(
        column("device_id", Uuid), column("sent_at", DateTime(timezone=True)), name="sent"
    ).data(list(sent_at.items()))
    statement = (
        delete(DeviceRow)
        .where(DeviceRow.id == sent.c.device_id, DeviceRow.updated_at <= sent.c.sent_at)
        .returning(DeviceRow.id)
    )
    return await _count(db, statement.cte("deleted"))


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


async def _count(db: AsyncSession, changed: CTE) -> int:
    # Counted by Postgres: the changed rows themselves never travel to the worker.
    return await db.scalar(select(func.count()).select_from(changed)) or 0


async def expire_overdue(db: AsyncSession, now: datetime) -> int:
    """Gives up on pending notifications that are too late to be of use; returns how many."""
    statement = (
        update(OutboxNotification)
        .where(
            OutboxNotification.status == OutboxStatus.PENDING.value,
            OutboxNotification.expires_at <= now,
        )
        .values(status=OutboxStatus.UNDELIVERED.value, finished_at=now)
        .returning(OutboxNotification.id)
    )
    return await _count(db, statement.cte("expired"))


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
    statement = (
        delete(OutboxNotification)
        .where(
            OutboxNotification.status != OutboxStatus.PENDING.value,
            OutboxNotification.finished_at < before,
        )
        .returning(OutboxNotification.id)
    )
    return await _count(db, statement.cte("deleted"))
