import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flagman.core.config import Settings
from flagman.identity import repository as identity_repository
from flagman.identity.domain import Language
from flagman.notifications import repository, service
from flagman.notifications.domain import (
    MAX_ATTEMPTS,
    Notification,
    NotificationCategory,
    NotificationEvent,
    OutboxStatus,
    PushPlatform,
    token_digest,
)
from flagman.notifications.fake_provider import NOT_FAKE_TICKET, FakePushProvider
from flagman.notifications.models import OutboxNotification, PushDevice
from flagman.notifications.models import PushTicket as TicketRow
from flagman.notifications.ports import (
    PushMessage,
    PushReceipt,
    PushReceiptStatus,
    PushTicket,
    PushTicketStatus,
)
from flagman.notifications.tests.conftest import Caller, push_token, sign_in

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _only_this_tests_notifications(sessions: async_sessionmaker[AsyncSession]) -> None:
    """The database is shared by the whole run: rows other tests left pending would be sent
    together with this test's, use up FakePushProvider.failures_left and push its row into a
    later batch. Tickets left behind would be asked for together with this test's too."""
    async with sessions() as db, db.begin():
        await db.execute(
            update(OutboxNotification)
            .where(OutboxNotification.status == OutboxStatus.PENDING.value)
            .values(status=OutboxStatus.UNDELIVERED.value, finished_at=datetime.now(UTC))
        )
        await db.execute(delete(TicketRow))


def _notification(
    event: NotificationEvent = NotificationEvent.TRIP_FINISHED, **params: int
) -> Notification:
    defaults = {
        NotificationEvent.RESERVATION_EXPIRING: {"minutes_left": 5},
        NotificationEvent.RESERVATION_EXPIRED: {},
        NotificationEvent.TRIP_LOW_BATTERY: {"battery_pct": 15},
        NotificationEvent.TRIP_FINISHED: {"total": 1250},
        NotificationEvent.PAYMENT_FAILED: {"amount": 990},
    }
    return Notification(event, uuid.uuid4(), params or defaults[event])


async def _notify(
    sessions: async_sessionmaker[AsyncSession], caller: Caller, notification: Notification
) -> None:
    async with sessions() as db, db.begin():
        await service.notify_client(db, caller.id, notification, datetime.now(UTC))


async def _register(
    sessions: async_sessionmaker[AsyncSession], caller: Caller, token: str | None = None
) -> str:
    token = token or push_token()
    async with sessions() as db:
        await service.register_device(
            db,
            user_id=caller.id,
            session_id=caller.session_id,
            token=token,
            platform=PushPlatform.IOS,
            now=datetime.now(UTC),
        )
    return token


async def _outbox(
    sessions: async_sessionmaker[AsyncSession], notification: Notification
) -> list[OutboxNotification]:
    async with sessions() as db:
        rows = await db.scalars(
            select(OutboxNotification).where(OutboxNotification.dedup_key == notification.dedup_key)
        )
        return list(rows)


def _sent_to(provider: FakePushProvider, token: str) -> list[tuple[str, str]]:
    return [(m.content.title, m.content.body) for m in provider.sent if m.token == token]


async def _dispatch(
    sessions: async_sessionmaker[AsyncSession],
    provider: FakePushProvider,
    now: datetime | None = None,
) -> service.DispatchResult:
    return await service.dispatch_due(sessions, provider, now or datetime.now(UTC))


@pytest.mark.parametrize("event", list(NotificationEvent))
async def test_every_event_reaches_every_device_of_the_client(
    sessions: async_sessionmaker[AsyncSession],
    notifications_settings: Settings,
    event: NotificationEvent,
) -> None:
    phone = await sign_in(sessions, notifications_settings, Language.EN)
    tablet = await sign_in(sessions, notifications_settings, user_id=phone.id)
    tokens = [await _register(sessions, phone), await _register(sessions, tablet)]
    notification = _notification(event)
    provider = FakePushProvider()

    await _notify(sessions, phone, notification)
    await _dispatch(sessions, provider)

    for token in tokens:
        assert len(_sent_to(provider, token)) == 1
    [row] = await _outbox(sessions, notification)
    assert row.status == OutboxStatus.SENT
    assert row.attempts == 1


async def test_text_is_in_the_clients_language(
    sessions: async_sessionmaker[AsyncSession], notifications_settings: Settings
) -> None:
    caller = await sign_in(sessions, notifications_settings, Language.EN)
    token = await _register(sessions, caller)
    provider = FakePushProvider()

    await _notify(sessions, caller, _notification(NotificationEvent.PAYMENT_FAILED, amount=990))
    await _dispatch(sessions, provider)

    assert _sent_to(provider, token) == [
        ("Payment failed", "We could not charge 9.90 BYN. Please check your card.")
    ]


async def test_the_same_event_is_sent_once(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    first = _notification(NotificationEvent.TRIP_LOW_BATTERY, battery_pct=19)
    again = Notification(first.event, first.rental_id, {"battery_pct": 12})
    provider = FakePushProvider()

    await _notify(sessions, caller, first)
    await _notify(sessions, caller, again)
    await _dispatch(sessions, provider)
    await _notify(sessions, caller, again)
    await _dispatch(sessions, provider)

    assert len(_sent_to(provider, token)) == 1
    assert len(await _outbox(sessions, first)) == 1


async def test_a_rolled_back_event_sends_nothing(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    notification = _notification()

    class BusinessFailedError(Exception):
        pass

    async def finish_rental() -> None:
        # The rental or the payment the notification tells of fails after it was queued.
        async with sessions() as db, db.begin():
            await service.notify_client(db, caller.id, notification, datetime.now(UTC))
            raise BusinessFailedError

    with pytest.raises(BusinessFailedError):
        await finish_rental()

    assert await _outbox(sessions, notification) == []


async def test_a_switched_off_category_is_not_queued(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    async with sessions() as db:
        await service.update_preferences(
            db, caller.id, {NotificationCategory.RESERVATION: False}, datetime.now(UTC)
        )
    expiring = _notification(NotificationEvent.RESERVATION_EXPIRING)
    finished = _notification(NotificationEvent.TRIP_FINISHED)

    await _notify(sessions, caller, expiring)
    await _notify(sessions, caller, finished)

    assert await _outbox(sessions, expiring) == []
    assert len(await _outbox(sessions, finished)) == 1


async def test_a_provider_outage_is_retried_and_then_given_up(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    notification = _notification()
    provider = FakePushProvider(failures_left=MAX_ATTEMPTS)
    await _notify(sessions, caller, notification)
    now = datetime.now(UTC)

    await _dispatch(sessions, provider, now)
    [row] = await _outbox(sessions, notification)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.next_attempt_at == now + timedelta(seconds=30)

    # Not due yet: nothing is tried before the pause is over.
    await _dispatch(sessions, provider, now + timedelta(seconds=29))
    [row] = await _outbox(sessions, notification)
    assert row.attempts == 1

    for _ in range(MAX_ATTEMPTS - 1):
        now += timedelta(hours=1)
        await _dispatch(sessions, provider, now)

    [row] = await _outbox(sessions, notification)
    assert (row.status, row.attempts) == (OutboxStatus.UNDELIVERED, MAX_ATTEMPTS)
    assert _sent_to(provider, token) == []


async def test_a_retry_after_an_outage_delivers(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    notification = _notification()
    provider = FakePushProvider(failures_left=1)
    await _notify(sessions, caller, notification)
    now = datetime.now(UTC)

    await _dispatch(sessions, provider, now)
    await _dispatch(sessions, provider, now + timedelta(seconds=30))

    assert len(_sent_to(provider, token)) == 1
    [row] = await _outbox(sessions, notification)
    assert (row.status, row.attempts) == (OutboxStatus.SENT, 2)


class _OnePhoneDown(FakePushProvider):
    """Delivers to every token but one, as if that phone's request failed."""

    def __init__(self, down: str) -> None:
        super().__init__()
        self.down = down

    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicket]:
        up = [m for m in messages if m.token != self.down]
        delivered = iter(await super().send(up))
        return [
            PushTicket(PushTicketStatus.NOT_SENT) if m.token == self.down else next(delivered)
            for m in messages
        ]


async def test_a_phone_the_provider_missed_does_not_resend_to_the_others(
    sessions: async_sessionmaker[AsyncSession], notifications_settings: Settings
) -> None:
    phone = await sign_in(sessions, notifications_settings)
    tablet = await sign_in(sessions, notifications_settings, user_id=phone.id)
    reached = await _register(sessions, phone)
    missed = await _register(sessions, tablet)
    notification = _notification()
    provider = _OnePhoneDown(missed)
    await _notify(sessions, phone, notification)
    now = datetime.now(UTC)

    await _dispatch(sessions, provider, now)
    await _dispatch(sessions, provider, now + timedelta(hours=1))

    assert len(_sent_to(provider, reached)) == 1
    [row] = await _outbox(sessions, notification)
    assert (row.status, row.attempts) == (OutboxStatus.SENT, 1)


async def test_a_notification_too_late_to_help_is_not_sent(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    notification = _notification(NotificationEvent.RESERVATION_EXPIRING, minutes_left=5)
    await _notify(sessions, caller, notification)
    provider = FakePushProvider()

    # The worker was down until the reservation was over.
    await _dispatch(sessions, provider, datetime.now(UTC) + timedelta(minutes=6))

    assert _sent_to(provider, token) == []
    [row] = await _outbox(sessions, notification)
    assert (row.status, row.attempts) == (OutboxStatus.UNDELIVERED, 0)


async def test_the_provider_drops_a_message_it_cannot_deliver_in_time(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    await _notify(
        sessions, caller, _notification(NotificationEvent.RESERVATION_EXPIRING, minutes_left=5)
    )
    provider = FakePushProvider()
    before = datetime.now(UTC)

    await _dispatch(sessions, provider)

    [message] = [m for m in provider.sent if m.token == token]
    assert (
        before + timedelta(minutes=4)
        < message.expires_at
        <= datetime.now(UTC) + timedelta(minutes=5)
    )


async def test_a_refused_message_is_not_retried(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    notification = _notification()
    await _notify(sessions, caller, notification)

    await _dispatch(sessions, FakePushProvider(rejected_tokens={token}))

    [row] = await _outbox(sessions, notification)
    assert (row.status, row.attempts) == (OutboxStatus.UNDELIVERED, 1)


async def test_a_notification_a_dead_worker_took_is_sent_after_the_lease(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    notification = _notification()
    await _notify(sessions, caller, notification)
    now = datetime.now(UTC)
    # Taken for sending, and the worker dies before it settles anything.
    async with sessions() as db, db.begin():
        await repository.claim_due(db, now, now + service.DISPATCH_LEASE, service.DISPATCH_BATCH)
    provider = FakePushProvider()

    await _dispatch(sessions, provider, now + timedelta(minutes=1))
    assert _sent_to(provider, token) == []

    await _dispatch(sessions, provider, now + service.DISPATCH_LEASE)
    assert len(_sent_to(provider, token)) == 1
    [row] = await _outbox(sessions, notification)
    assert (row.status, row.attempts) == (OutboxStatus.SENT, 2)


async def test_more_than_a_batch_goes_out_in_one_run(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    count = service.DISPATCH_BATCH + 1
    async with sessions() as db, db.begin():
        for _ in range(count):
            await service.notify_client(db, caller.id, _notification(), datetime.now(UTC))
    provider = FakePushProvider()

    result = await _dispatch(sessions, provider)

    assert result.sent == count
    assert len(_sent_to(provider, token)) == count


async def test_an_unregistered_device_is_deleted(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    notification = _notification()
    provider = FakePushProvider(unregistered_tokens={token})
    await _notify(sessions, caller, notification)

    await _dispatch(sessions, provider)

    [row] = await _outbox(sessions, notification)
    assert row.status == OutboxStatus.UNDELIVERED
    async with sessions() as db:
        assert await db.scalar(select(PushDevice).where(PushDevice.token == token)) is None


async def test_a_signed_out_phone_gets_nothing(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    async with sessions() as db, db.begin():
        await identity_repository.revoke_session(db, caller.session_id, datetime.now(UTC))
    notification = _notification()
    provider = FakePushProvider()
    await _notify(sessions, caller, notification)

    await _dispatch(sessions, provider)

    assert _sent_to(provider, token) == []
    [row] = await _outbox(sessions, notification)
    assert row.status == OutboxStatus.UNDELIVERED
    async with sessions() as db:
        assert await db.scalar(select(PushDevice).where(PushDevice.token == token)) is None


async def test_logs_hold_neither_tokens_nor_texts(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    await _notify(sessions, caller, _notification(NotificationEvent.PAYMENT_FAILED, amount=990))

    with structlog.testing.capture_logs() as logs:
        await _dispatch(sessions, FakePushProvider())

    logged = repr(logs)
    assert token not in logged
    assert "9,90" not in logged
    assert str(caller.id) not in logged


async def test_old_finished_notifications_are_cleaned_up(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    await _register(sessions, caller)
    notification = _notification()
    await _notify(sessions, caller, notification)
    await _dispatch(sessions, FakePushProvider())

    async with sessions() as db:
        await service.clean_up_outbox(db, datetime.now(UTC), timedelta(days=30))
    assert len(await _outbox(sessions, notification)) == 1

    async with sessions() as db:
        await service.clean_up_outbox(
            db, datetime.now(UTC) + timedelta(days=31), timedelta(days=30)
        )
    assert await _outbox(sessions, notification) == []


async def test_an_unreadable_row_is_given_up_without_blocking_the_rest(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    broken = _notification()
    await _notify(sessions, caller, broken)
    # As if queued before the template took other params.
    async with sessions() as db, db.begin():
        await db.execute(
            update(OutboxNotification)
            .where(OutboxNotification.dedup_key == broken.dedup_key)
            .values(params={"old_total": 1}, next_attempt_at=datetime.now(UTC) - timedelta(hours=1))
        )
    fine = _notification()
    await _notify(sessions, caller, fine)
    provider = FakePushProvider()

    await _dispatch(sessions, provider)

    [broken_row] = await _outbox(sessions, broken)
    [fine_row] = await _outbox(sessions, fine)
    assert broken_row.status == OutboxStatus.UNDELIVERED
    assert fine_row.status == OutboxStatus.SENT
    assert len(_sent_to(provider, token)) == 1


async def test_without_a_provider_overdue_notifications_expire_and_are_cleaned_up(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    notification = _notification(NotificationEvent.RESERVATION_EXPIRING, minutes_left=5)
    await _notify(sessions, caller, notification)
    later = datetime.now(UTC) + timedelta(minutes=6)

    async with sessions() as db:
        await service.clean_up_outbox(db, later, timedelta(days=30))
    [row] = await _outbox(sessions, notification)
    assert row.status == OutboxStatus.UNDELIVERED

    async with sessions() as db:
        await service.clean_up_outbox(db, later + timedelta(days=31), timedelta(days=30))
    assert await _outbox(sessions, notification) == []


async def test_a_withdrawn_notification_is_not_sent(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    reminder = _notification(NotificationEvent.RESERVATION_EXPIRING, minutes_left=5)
    other = Notification(NotificationEvent.TRIP_FINISHED, reminder.rental_id, {"total": 100})
    await _notify(sessions, caller, reminder)
    await _notify(sessions, caller, other)

    async with sessions() as db, db.begin():
        await service.withdraw(
            db, reminder.rental_id, [NotificationEvent.RESERVATION_EXPIRING], datetime.now(UTC)
        )
    provider = FakePushProvider()
    await _dispatch(sessions, provider)

    [row] = await _outbox(sessions, reminder)
    assert row.status == OutboxStatus.UNDELIVERED
    assert [m.content.data["event"] for m in provider.sent if m.token == token] == ["trip_finished"]


async def test_until_shortens_the_lifetime(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    notification = _notification(NotificationEvent.TRIP_FINISHED)
    now = datetime.now(UTC)
    until = now + timedelta(minutes=2)

    async with sessions() as db, db.begin():
        await service.notify_client(db, caller.id, notification, now, until=until)

    [row] = await _outbox(sessions, notification)
    assert row.expires_at == until


async def _tickets(sessions: async_sessionmaker[AsyncSession], token: str) -> list[TicketRow]:
    async with sessions() as db:
        rows = await db.scalars(
            select(TicketRow)
            .join(PushDevice, PushDevice.id == TicketRow.device_id)
            .where(PushDevice.token == token)
        )
        return list(rows)


async def _device(sessions: async_sessionmaker[AsyncSession], token: str) -> PushDevice | None:
    async with sessions() as db:
        return await db.scalar(select(PushDevice).where(PushDevice.token == token))


async def _send(
    sessions: async_sessionmaker[AsyncSession], caller: Caller, provider: FakePushProvider
) -> datetime:
    """Sends the caller a notification; returns when."""
    await _notify(sessions, caller, _notification())
    now = datetime.now(UTC)
    await _dispatch(sessions, provider, now)
    return now


async def _check(
    sessions: async_sessionmaker[AsyncSession], provider: FakePushProvider, now: datetime
) -> service.ReceiptsResult:
    return await service.check_receipts(sessions, provider, now)


RECEIPT_READY = timedelta(minutes=16)


async def test_a_taken_message_leaves_a_ticket_for_its_device(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)

    sent_at = await _send(sessions, caller, FakePushProvider())

    [ticket] = await _tickets(sessions, token)
    assert ticket.sent_at == sent_at


async def test_a_refused_message_leaves_no_ticket(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)

    await _send(sessions, caller, FakePushProvider(rejected_tokens={token}))

    assert await _tickets(sessions, token) == []


async def test_a_device_its_receipt_calls_unregistered_is_deleted(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    provider = FakePushProvider(unregistered_on_receipt={token})
    sent_at = await _send(sessions, caller, provider)

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert result.unregistered_devices == 1
    assert await _device(sessions, token) is None


async def test_receipts_are_not_asked_for_too_soon(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    provider = FakePushProvider(unregistered_on_receipt={token})
    sent_at = await _send(sessions, caller, provider)

    await _check(sessions, provider, sent_at + timedelta(minutes=10))

    assert await _device(sessions, token) is not None
    assert len(await _tickets(sessions, token)) == 1


async def test_a_delivered_receipt_keeps_the_device_and_drops_the_ticket(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    provider = FakePushProvider()
    sent_at = await _send(sessions, caller, provider)

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert result.accepted == 1
    assert await _device(sessions, token) is not None
    assert await _tickets(sessions, token) == []


async def test_a_receipt_not_ready_yet_is_asked_for_again(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    provider = FakePushProvider(unregistered_on_receipt={token}, receipts_ready=False)
    sent_at = await _send(sessions, caller, provider)

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)
    assert result.not_ready == 1
    assert len(await _tickets(sessions, token)) == 1

    provider.receipts_ready = True
    await _check(sessions, provider, sent_at + 2 * RECEIPT_READY)
    assert await _device(sessions, token) is None


async def test_a_ticket_past_a_day_is_dropped_without_asking(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    provider = FakePushProvider(unregistered_on_receipt={token})
    sent_at = await _send(sessions, caller, provider)

    result = await _check(sessions, provider, sent_at + timedelta(hours=25))

    assert result.expired == 1
    assert await _tickets(sessions, token) == []
    # Expo keeps no receipt that long: nothing tells the token is dead.
    assert await _device(sessions, token) is not None


class _RateLimited(FakePushProvider):
    async def get_receipts(self, ticket_ids: Sequence[str]) -> dict[str, PushReceipt]:
        return {
            ticket_id: PushReceipt(PushReceiptStatus.FAILED, "MessageRateExceeded")
            for ticket_id in ticket_ids
        }


async def test_other_receipt_errors_are_only_counted(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    provider = _RateLimited()
    sent_at = await _send(sessions, caller, provider)

    with structlog.testing.capture_logs() as logs:
        result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert result.failed == {"MessageRateExceeded": 1}
    assert await _device(sessions, token) is not None
    assert await _tickets(sessions, token) == []
    [checked] = [log for log in logs if log["event"] == "push_receipts_checked"]
    assert checked["log_level"] == "warning"
    assert checked["failed"] == {"MessageRateExceeded": 1}
    assert token not in repr(logs)


async def test_the_fake_fails_the_tickets_it_did_not_issue() -> None:
    expo_ticket = str(uuid.uuid4())

    receipts = await FakePushProvider().get_receipts([expo_ticket, "fake-unknown"])

    assert receipts == {
        expo_ticket: PushReceipt(PushReceiptStatus.FAILED, NOT_FAKE_TICKET),
        "fake-unknown": PushReceipt(PushReceiptStatus.OK),
    }


async def test_a_device_registered_again_after_the_message_is_kept(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    old_token = await _register(sessions, caller)
    provider = FakePushProvider(unregistered_on_receipt={old_token})
    sent_at = await _send(sessions, caller, provider)
    new_token = push_token()
    async with sessions() as db:
        # The same session after reinstalling the app: the old ticket is about a dead token.
        await service.register_device(
            db,
            user_id=caller.id,
            session_id=caller.session_id,
            token=new_token,
            platform=PushPlatform.IOS,
            now=sent_at + timedelta(minutes=1),
        )

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert result.unregistered_devices == 0
    assert await _device(sessions, new_token) is not None


class _Asked(FakePushProvider):
    def __init__(self, *, receipts_ready: bool = True, down: bool = False) -> None:
        super().__init__(receipts_ready=receipts_ready)
        self.down = down
        self.asked: list[list[str]] = []

    async def get_receipts(self, ticket_ids: Sequence[str]) -> dict[str, PushReceipt] | None:
        self.asked.append(list(ticket_ids))
        if self.down:
            return None
        return await super().get_receipts(ticket_ids)


async def test_receipts_are_asked_for_page_by_page(
    sessions: async_sessionmaker[AsyncSession],
    notifications_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "RECEIPTS_BATCH", 2)
    provider = _Asked(receipts_ready=True)
    sent_at = await _send_to_three(sessions, notifications_settings, provider)

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert [len(batch) for batch in provider.asked] == [2, 1]
    assert len({ticket for batch in provider.asked for ticket in batch}) == 3
    assert result.accepted == 3


async def test_a_page_with_no_receipt_ready_does_not_hold_back_the_next(
    sessions: async_sessionmaker[AsyncSession],
    notifications_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "RECEIPTS_BATCH", 2)
    provider = _Asked(receipts_ready=False)
    sent_at = await _send_to_three(sessions, notifications_settings, provider)

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    # Each is asked for once per run, not again on the next page.
    assert [len(batch) for batch in provider.asked] == [2, 1]
    assert len({ticket for batch in provider.asked for ticket in batch}) == 3
    assert result.not_ready == 3


async def test_a_provider_with_no_answer_ends_the_run(
    sessions: async_sessionmaker[AsyncSession],
    notifications_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "RECEIPTS_BATCH", 2)
    provider = _Asked(down=True)
    sent_at = await _send_to_three(sessions, notifications_settings, provider)

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert [len(batch) for batch in provider.asked] == [2]
    assert result.not_ready == 2
    assert len(await _all_tickets(sessions)) == 3


async def _send_to_three(
    sessions: async_sessionmaker[AsyncSession],
    notifications_settings: Settings,
    provider: FakePushProvider,
) -> datetime:
    for _ in range(3):
        caller = await sign_in(sessions, notifications_settings)
        await _register(sessions, caller)
        await _notify(sessions, caller, _notification())
    sent_at = datetime.now(UTC)
    await _dispatch(sessions, provider, sent_at)
    return sent_at


async def _all_tickets(sessions: async_sessionmaker[AsyncSession]) -> list[TicketRow]:
    async with sessions() as db:
        return list(await db.scalars(select(TicketRow)))


async def test_a_device_registered_again_with_the_same_dead_token_is_deleted(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    provider = FakePushProvider(unregistered_on_receipt={token})
    sent_at = await _send(sessions, caller, provider)
    async with sessions() as db:
        await service.register_device(
            db,
            user_id=caller.id,
            session_id=caller.session_id,
            token=token,
            platform=PushPlatform.IOS,
            now=sent_at + timedelta(minutes=1),
        )

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert result.unregistered_devices == 1
    assert await _device(sessions, token) is None


async def test_a_new_token_saved_by_a_request_older_than_the_message_is_kept(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    old_token = await _register(sessions, caller)
    provider = FakePushProvider(unregistered_on_receipt={old_token})
    sent_at = await _send(sessions, caller, provider)
    new_token = push_token()
    # The request started before the message went out, but committed after the worker had read
    # the old token: its time says nothing about which token the message went to.
    async with sessions() as db:
        await service.register_device(
            db,
            user_id=caller.id,
            session_id=caller.session_id,
            token=new_token,
            platform=PushPlatform.IOS,
            now=sent_at - timedelta(seconds=1),
        )

    result = await _check(sessions, provider, sent_at + RECEIPT_READY)

    assert result.unregistered_devices == 0
    assert await _device(sessions, new_token) is not None


async def test_tickets_of_a_device_deleted_meanwhile_are_left_out(
    sessions: async_sessionmaker[AsyncSession], caller: Caller
) -> None:
    token = await _register(sessions, caller)
    device = await _device(sessions, token)
    assert device is not None

    async with sessions() as db, db.begin():
        await repository.save_tickets(
            db,
            {"kept": (device.id, token_digest(token)), "left-out": (uuid.uuid4(), "digest")},
            datetime.now(UTC),
        )

    assert [ticket.id for ticket in await _all_tickets(sessions)] == ["kept"]


async def test_tickets_not_saved_hold_back_no_batch(
    sessions: async_sessionmaker[AsyncSession],
    caller: Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*args: object) -> None:
        raise SQLAlchemyError("connection lost")

    monkeypatch.setattr(service, "DISPATCH_BATCH", 1)
    monkeypatch.setattr(repository, "save_tickets", broken)
    token = await _register(sessions, caller)
    for _ in range(2):
        await _notify(sessions, caller, _notification())
    provider = FakePushProvider()

    with structlog.testing.capture_logs() as logs:
        result = await _dispatch(sessions, provider)

    assert result.sent == 2
    assert len(_sent_to(provider, token)) == 2
    assert [log["tickets"] for log in logs if log["event"] == "push_tickets_not_saved"] == [1, 1]
