"""Public interface of the notifications for other modules, see docs/adr/0020.

Other modules call `notify_client` inside their own transaction: the notification is stored in
the outbox together with the change it tells of, or not at all. `flagman-worker` sends it later,
so a provider that is down never fails a rental or a payment.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flagman.identity import service as identity
from flagman.identity.domain import Language
from flagman.notifications import repository
from flagman.notifications.domain import (
    Notification,
    NotificationCategory,
    NotificationPreferences,
    OutboxItem,
    OutboxStatus,
    PushDevice,
    PushPlatform,
    render,
    retry_delay,
)
from flagman.notifications.ports import (
    PushMessage,
    PushProvider,
    PushProviderError,
    PushTicketStatus,
)

logger = structlog.get_logger(__name__)

DISPATCH_BATCH = 100
# Longer than a send of a whole batch may take, with Expo's timeout and a retry inside it.
DISPATCH_LEASE = timedelta(minutes=2)


async def notify_client(
    db: AsyncSession, user_id: UUID, notification: Notification, now: datetime
) -> None:
    """Queues a push for the client; runs in the caller's transaction.

    Does nothing when the client switched the category off or the same event for the same
    rental is already queued, so callers may report an event as often as they see it.
    """
    preferences = await repository.get_preferences(db, user_id)
    if not preferences.is_enabled(notification.category):
        return
    added = await repository.add_to_outbox(db, user_id, notification, now)
    if added:
        logger.info("notification_queued", push_event=notification.event.value)


async def register_device(  # noqa: PLR0913 - one device's fields
    db: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    token: str,
    platform: PushPlatform,
    now: datetime,
) -> None:
    """Pushes for the client go to this token while this session lasts."""
    async with db.begin():
        await repository.save_device(
            db, user_id=user_id, session_id=session_id, token=token, platform=platform, now=now
        )


async def forget_device(db: AsyncSession, user_id: UUID, session_id: UUID) -> None:
    """Idempotent: a session without a device is fine."""
    async with db.begin():
        await repository.delete_session_device(db, user_id, session_id)


async def get_preferences(db: AsyncSession, user_id: UUID) -> NotificationPreferences:
    async with db.begin():
        return await repository.get_preferences(db, user_id)


async def update_preferences(
    db: AsyncSession,
    user_id: UUID,
    changes: Mapping[NotificationCategory, bool],
    now: datetime,
) -> NotificationPreferences:
    """Raises NotificationCategoryMandatoryError for an attempt to switch off trip or payment
    notifications. Categories left out of `changes` stay as they were."""
    async with db.begin():
        current = await repository.get_preferences(db, user_id)
        updated = current.with_changes(changes)
        await repository.save_preferences(db, user_id, updated, now)
    return updated


@dataclass(frozen=True)
class DispatchResult:
    sent: int
    retried: int
    undelivered: int


@dataclass(frozen=True)
class _Claimed:
    items: list[OutboxItem]
    devices: dict[UUID, list[PushDevice]]
    languages: dict[UUID, Language]


async def dispatch_due(
    session_factory: async_sessionmaker[AsyncSession],
    provider: PushProvider,
    now: datetime,
) -> DispatchResult:
    """Sends everything that is due, batch by batch, until nothing is left."""
    sent = retried = undelivered = 0
    while True:
        result, claimed = await _dispatch_batch(session_factory, provider, now)
        sent += result.sent
        retried += result.retried
        undelivered += result.undelivered
        if claimed < DISPATCH_BATCH:
            return DispatchResult(sent=sent, retried=retried, undelivered=undelivered)


async def _dispatch_batch(
    session_factory: async_sessionmaker[AsyncSession],
    provider: PushProvider,
    now: datetime,
) -> tuple[DispatchResult, int]:
    # Claimed in one transaction and settled in another: the provider is called in between,
    # and a transaction must not stay open over a network call.
    async with session_factory() as db, db.begin():
        claimed = await _claim(db, now)
    if not claimed.items:
        return DispatchResult(0, 0, 0), 0

    addressed, no_device = _address(claimed)
    try:
        statuses = await provider.send([a.message for a in addressed]) if addressed else []
    except PushProviderError:
        statuses = None
    outcome = _outcome(addressed, no_device, statuses, now)

    async with session_factory() as db, db.begin():
        await repository.delete_devices(db, outcome.unregistered)
        await repository.finish(db, outcome.delivered, OutboxStatus.SENT, now)
        await repository.finish(db, outcome.gave_up, OutboxStatus.UNDELIVERED, now)
        for outbox_id, at in outcome.retry.items():
            await repository.retry_at(db, outbox_id, at)

    result = DispatchResult(
        sent=len(outcome.delivered), retried=len(outcome.retry), undelivered=len(outcome.gave_up)
    )
    # Counts only: who got what is nobody's business in the logs.
    logger.info(
        "notifications_dispatched",
        sent=result.sent,
        retried=result.retried,
        undelivered=result.undelivered,
        unregistered_devices=len(outcome.unregistered),
        provider_failed=statuses is None,
    )
    return result, len(claimed.items)


@dataclass(frozen=True)
class _Addressed:
    item: OutboxItem
    device: PushDevice
    message: PushMessage


def _address(claimed: _Claimed) -> tuple[list[_Addressed], list[UUID]]:
    """One message per device of each client; also the notifications nobody can receive."""
    addressed: list[_Addressed] = []
    no_device: list[UUID] = []
    for item in claimed.items:
        devices = claimed.devices.get(item.user_id, [])
        if not devices:
            no_device.append(item.id)
            continue
        content = render(item.notification, claimed.languages.get(item.user_id, Language.RU))
        addressed += [
            _Addressed(item, device, PushMessage(token=device.token, content=content))
            for device in devices
        ]
    return addressed, no_device


@dataclass(frozen=True)
class _Outcome:
    delivered: set[UUID]
    gave_up: list[UUID]
    retry: dict[UUID, datetime]
    unregistered: list[UUID]


def _outcome(
    addressed: list[_Addressed],
    no_device: list[UUID],
    statuses: list[PushTicketStatus] | None,
    now: datetime,
) -> _Outcome:
    """`statuses` is None when the provider took nothing: then the whole batch is retried."""
    tried = {a.item.id: a.item for a in addressed}
    if statuses is None:
        retry: dict[UUID, datetime] = {}
        gave_up = list(no_device)
        for item in tried.values():
            delay = retry_delay(item.attempts)
            if delay is None:
                gave_up.append(item.id)
            else:
                retry[item.id] = now + delay
        return _Outcome(delivered=set(), gave_up=gave_up, retry=retry, unregistered=[])
    delivered = {
        a.item.id
        for a, status in zip(addressed, statuses, strict=True)
        if status is PushTicketStatus.OK
    }
    unregistered = [
        a.device.id
        for a, status in zip(addressed, statuses, strict=True)
        if status is PushTicketStatus.DEVICE_NOT_REGISTERED
    ]
    # Delivered to at least one phone is enough; a message the provider refused will be refused
    # again, so there is no retry.
    gave_up = no_device + [item_id for item_id in tried if item_id not in delivered]
    return _Outcome(delivered=delivered, gave_up=gave_up, retry={}, unregistered=unregistered)


async def _claim(db: AsyncSession, now: datetime) -> _Claimed:
    items = await repository.claim_due(db, now, now + DISPATCH_LEASE, DISPATCH_BATCH)
    if not items:
        return _Claimed([], {}, {})
    user_ids = {item.user_id for item in items}
    all_devices = await repository.get_devices(db, user_ids)
    active = await identity.get_active_client_sessions(
        db, {device.session_id for device in all_devices}, now
    )
    # A signed-out phone must not show what happens on the account any more.
    await repository.delete_devices(
        db, [device.id for device in all_devices if device.session_id not in active]
    )
    devices: dict[UUID, list[PushDevice]] = defaultdict(list)
    for device in all_devices:
        if device.session_id in active:
            devices[device.user_id].append(device)
    languages = await identity.get_client_languages(db, user_ids)
    return _Claimed(items=items, devices=dict(devices), languages=languages)


async def clean_up_outbox(db: AsyncSession, now: datetime, retention: timedelta) -> int:
    """Deletes sent and undelivered notifications older than `retention`; returns how many."""
    async with db.begin():
        return await repository.delete_finished_before(db, now - retention)
