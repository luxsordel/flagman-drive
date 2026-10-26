import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flagman.audit.models import AuditEvent
from flagman.core.config import Settings
from flagman.payments import service
from flagman.payments.domain import (
    CardBrand,
    CardDetails,
    DeclineReason,
    IdempotencyKeyReusedError,
    NoUsableCardError,
    OperationKind,
    OperationStatus,
    PaymentCardExpiredError,
    PaymentOperation,
    PaymentOperationInvalidError,
    PaymentOperationNotFoundError,
)
from flagman.payments.fake_provider import FakeProvider
from flagman.payments.models import PaymentAttempt
from flagman.payments.models import PaymentOperation as PaymentOperationRow
from flagman.payments.ports import (
    OperationRequest,
    OperationResult,
    PaymentProviderError,
    WebhookEvent,
    WebhookEventType,
)
from flagman.payments.service import Gateway, Payment
from flagman.payments.tests.conftest import RETURN_URL, add_user

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
# The second hold of LateFailureProvider: the slow retry.
SLOW_RETRY = 2


async def _bind(  # noqa: PLR0913 - the card traits are optional keywords
    db: AsyncSession,
    gw: Gateway,
    provider: FakeProvider,
    user_id: uuid.UUID,
    *,
    behavior: str = "ok",
    details: CardDetails | None = None,
) -> None:
    binding, _ = await service.start_binding(
        db,
        gw,
        service.BindingRequest(
            user_id,
            RETURN_URL,
            service.ReturnUrlRules(frozenset(), allow_expo_go=False),
            "https://t/",
        ),
        NOW,
    )
    event = WebhookEvent(
        uuid.uuid4().hex,
        WebhookEventType.CARD_BOUND,
        binding.id,
        card=details or CardDetails(CardBrand.VISA, "4242", 12, 2030),
        token=f"fake_{behavior}_{uuid.uuid4().hex}",
    )
    assert await service.receive_webhook(db, gw, provider.signed_webhook(event, NOW), NOW)


@pytest.fixture
def provider(payments_settings: Settings) -> FakeProvider:
    return service.fake_provider(payments_settings)


@pytest.fixture
def gw(payments_settings: Settings) -> Gateway:
    return service.gateway(payments_settings)


@pytest.fixture
async def user_id(payments_settings: Settings) -> uuid.UUID:
    return await add_user(payments_settings)


def _key() -> str:
    return f"test-{uuid.uuid4()}"


async def test_hold_capture_and_refund(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)

    held = await service.hold(db, gw, user_id, Payment(3000, _key()), NOW)
    captured = await service.capture(db, gw, held.id, Payment(2150, _key()), NOW)
    refunded = await service.refund(db, gw, captured.id, Payment(150, _key()), NOW)

    assert (held.kind, held.status, held.amount) == (
        OperationKind.HOLD,
        OperationStatus.SUCCEEDED,
        3000,
    )
    assert held.provider_ref is not None
    assert (captured.status, captured.parent_id) == (OperationStatus.SUCCEEDED, held.id)
    assert refunded.status is OperationStatus.SUCCEEDED
    assert await service.get_operation(db, refunded.id) == refunded


async def test_a_repeated_call_returns_the_same_operation(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    payment = Payment(3000, _key())

    first = await service.hold(db, gw, user_id, payment, NOW)
    again = await service.hold(db, gw, user_id, payment, NOW)

    assert again == first
    async with db.begin():
        attempts = (
            await db.scalars(select(PaymentAttempt).where(PaymentAttempt.operation_id == first.id))
        ).all()
    assert len(attempts) == 1


async def test_parallel_calls_with_one_key_make_one_operation(
    sessions: async_sessionmaker[AsyncSession],
    gw: Gateway,
    provider: FakeProvider,
    user_id: uuid.UUID,
) -> None:
    async with sessions() as db:
        await _bind(db, gw, provider, user_id)
    payment = Payment(3000, _key())

    async def hold() -> uuid.UUID:
        async with sessions() as db:
            return (await service.hold(db, gw, user_id, payment, NOW)).id

    results = await asyncio.gather(*(hold() for _ in range(5)))

    assert len(set(results)) == 1
    async with sessions() as db, db.begin():
        rows = (
            await db.scalars(
                select(PaymentOperationRow.id).where(
                    PaymentOperationRow.idempotency_key == payment.idempotency_key
                )
            )
        ).all()
    assert rows == [results[0]]


async def test_a_key_is_one_operation(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    key = _key()
    held = await service.hold(db, gw, user_id, Payment(3000, key), NOW)

    with pytest.raises(IdempotencyKeyReusedError):
        await service.hold(db, gw, user_id, Payment(3100, key), NOW)
    with pytest.raises(IdempotencyKeyReusedError):
        await service.charge(db, gw, user_id, Payment(3000, key), NOW)
    with pytest.raises(IdempotencyKeyReusedError):
        await service.capture(db, gw, held.id, Payment(3000, key), NOW)


async def test_not_enough_money(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id, behavior="nofunds")

    held = await service.hold(db, gw, user_id, Payment(3000, _key()), NOW)
    charged = await service.charge(db, gw, user_id, Payment(500, _key()), NOW)

    for operation in (held, charged):
        assert operation.status is OperationStatus.DECLINED
        assert operation.decline_reason is DeclineReason.INSUFFICIENT_FUNDS
    # A declined hold has nothing to capture.
    with pytest.raises(PaymentOperationInvalidError):
        await service.capture(db, gw, held.id, Payment(100, _key()), NOW)


async def test_no_card_or_an_expired_one(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    assert not await service.has_usable_card(db, user_id, NOW)
    with pytest.raises(NoUsableCardError):
        await service.hold(db, gw, user_id, Payment(3000, _key()), NOW)

    await _bind(db, gw, provider, user_id, details=CardDetails(CardBrand.VISA, "4242", 1, 2020))

    assert not await service.has_usable_card(db, user_id, NOW)
    with pytest.raises(NoUsableCardError):
        await service.charge(db, gw, user_id, Payment(3000, _key()), NOW)


async def test_usable_card(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)

    assert await service.has_usable_card(db, user_id, NOW)


async def test_a_hold_is_captured_or_voided_once(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    held = await service.hold(db, gw, user_id, Payment(3000, _key()), NOW)

    voided = await service.void(db, gw, held.id, _key(), NOW)

    assert (voided.status, voided.amount) == (OperationStatus.SUCCEEDED, 3000)
    with pytest.raises(PaymentOperationInvalidError):
        await service.capture(db, gw, held.id, Payment(3000, _key()), NOW)
    with pytest.raises(PaymentOperationInvalidError):
        await service.void(db, gw, held.id, _key(), NOW)


async def test_amounts_stay_within_the_parent(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    held = await service.hold(db, gw, user_id, Payment(3000, _key()), NOW)

    with pytest.raises(PaymentOperationInvalidError):
        await service.capture(db, gw, held.id, Payment(3001, _key()), NOW)
    captured = await service.capture(db, gw, held.id, Payment(1000, _key()), NOW)
    await service.refund(db, gw, captured.id, Payment(600, _key()), NOW)
    with pytest.raises(PaymentOperationInvalidError):
        await service.refund(db, gw, captured.id, Payment(401, _key()), NOW)
    with pytest.raises(PaymentOperationInvalidError):
        await service.refund(db, gw, held.id, Payment(100, _key()), NOW)


async def test_unknown_parent(db: AsyncSession, gw: Gateway) -> None:
    with pytest.raises(PaymentOperationNotFoundError):
        await service.capture(db, gw, uuid.uuid4(), Payment(100, _key()), NOW)


async def test_charge_goes_to_the_default_card(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id, behavior="nofunds")
    await _bind(
        db, gw, provider, user_id, details=CardDetails(CardBrand.MASTERCARD, "4444", 1, 2031)
    )
    working = next(
        card for card in await service.list_cards(db, user_id) if card.details.last4 == "4444"
    )
    await service.set_default_card(db, user_id, working.id, NOW)

    charged = await service.charge(db, gw, user_id, Payment(700, _key()), NOW)

    assert (charged.card_id, charged.status) == (working.id, OperationStatus.SUCCEEDED)


class FlakyProvider(FakeProvider):
    """Fails the first `failures` holds, then holds like the fake provider."""

    def __init__(self, inner: FakeProvider, failures: int) -> None:
        super().__init__(inner.secret, inner.tolerance_s)
        self.failures = failures
        self.calls: list[OperationRequest] = []

    async def hold(self, request: OperationRequest) -> OperationResult:
        self.calls.append(request)
        if len(self.calls) <= self.failures:
            raise PaymentProviderError
        return await super().hold(request)


class LateFailureProvider(FakeProvider):
    """The first hold fails; of two retries at once, the slow one fails after the fast succeeded."""

    def __init__(self, inner: FakeProvider) -> None:
        super().__init__(inner.secret, inner.tolerance_s)
        self.calls: list[OperationRequest] = []
        self.slow_waiting = asyncio.Event()
        self.fast_done = asyncio.Event()

    async def hold(self, request: OperationRequest) -> OperationResult:
        self.calls.append(request)
        if len(self.calls) == 1:
            raise PaymentProviderError
        if len(self.calls) == SLOW_RETRY:
            self.slow_waiting.set()
            await self.fast_done.wait()
            raise PaymentProviderError
        return await super().hold(request)


async def test_an_unknown_outcome_is_retried_with_the_same_key(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    flaky = FlakyProvider(provider, failures=1)
    flaky_gw = Gateway(provider=flaky, token_key=gw.token_key)
    payment = Payment(3000, _key())

    failed = await service.hold(db, flaky_gw, user_id, payment, NOW)
    retried = await service.hold(db, flaky_gw, user_id, payment, NOW)

    assert failed.status is OperationStatus.FAILED
    assert (retried.id, retried.status) == (failed.id, OperationStatus.SUCCEEDED)
    assert [call.idempotency_key for call in flaky.calls] == [payment.idempotency_key] * 2
    async with db.begin():
        attempts = (
            await db.scalars(
                select(PaymentAttempt.status, PaymentAttempt.error)
                .where(PaymentAttempt.operation_id == failed.id)
                .order_by(PaymentAttempt.attempted_at, PaymentAttempt.id)
            )
        ).all()
    assert len(attempts) == 2


async def test_a_removed_card_is_not_charged(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    (card,) = await service.list_cards(db, user_id)

    await service.remove_card(db, user_id, card.id, NOW)

    with pytest.raises(NoUsableCardError):
        await service.hold(db, gw, user_id, Payment(3000, _key()), NOW)


async def test_a_late_failure_does_not_undo_a_known_outcome(
    sessions: async_sessionmaker[AsyncSession],
    gw: Gateway,
    provider: FakeProvider,
    user_id: uuid.UUID,
) -> None:
    async with sessions() as db:
        await _bind(db, gw, provider, user_id)
    late = LateFailureProvider(provider)
    late_gw = Gateway(provider=late, token_key=gw.token_key)
    payment = Payment(3000, _key())
    async with sessions() as db:
        failed = await service.hold(db, late_gw, user_id, payment, NOW)

    async def slow() -> PaymentOperation:
        async with sessions() as db:
            return await service.hold(db, late_gw, user_id, payment, NOW)

    async def fast() -> PaymentOperation:
        # Starts once the slow retry is waiting on the provider.
        await late.slow_waiting.wait()
        async with sessions() as db:
            succeeded = await service.hold(db, late_gw, user_id, payment, NOW)
        late.fast_done.set()
        return succeeded

    slow_result, fast_result = await asyncio.gather(slow(), fast())

    assert failed.status is OperationStatus.FAILED
    assert fast_result.status is OperationStatus.SUCCEEDED
    assert (slow_result.status, slow_result.provider_ref) == (
        OperationStatus.SUCCEEDED,
        fast_result.provider_ref,
    )
    async with sessions() as db:
        assert await service.get_operation(db, failed.id) == fast_result


async def test_every_known_outcome_goes_to_the_audit_log(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    flaky = FlakyProvider(provider, failures=1)
    flaky_gw = Gateway(provider=flaky, token_key=gw.token_key)
    payment = Payment(3000, _key())

    failed = await service.hold(db, flaky_gw, user_id, payment, NOW)
    await service.hold(db, flaky_gw, user_id, payment, NOW)
    await service.hold(db, flaky_gw, user_id, payment, NOW)

    async with db.begin():
        rows = await db.execute(
            select(AuditEvent.action, AuditEvent.actor_type, AuditEvent.details)
            .where(AuditEvent.target_id == failed.id)
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
        events = [(action, actor, details) for action, actor, details in rows]
    details = {"kind": "hold", "amount": 3000, "user_id": str(user_id)}
    # The third call finds the outcome stored and asks nobody: no event for it.
    assert events == [
        ("payment.failed", "system", details),
        ("payment.succeeded", "system", details),
    ]


async def test_an_expired_card_cannot_be_the_default(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id)
    await _bind(db, gw, provider, user_id, details=CardDetails(CardBrand.VISA, "1111", 1, 2020))
    expired = next(
        card for card in await service.list_cards(db, user_id) if card.details.last4 == "1111"
    )

    with pytest.raises(PaymentCardExpiredError):
        await service.set_default_card(db, user_id, expired.id, NOW)


async def test_a_card_bound_over_an_expired_default_takes_over(
    db: AsyncSession, gw: Gateway, provider: FakeProvider, user_id: uuid.UUID
) -> None:
    await _bind(db, gw, provider, user_id, details=CardDetails(CardBrand.VISA, "1111", 1, 2020))
    await _bind(db, gw, provider, user_id)

    cards = {card.details.last4: card.is_default for card in await service.list_cards(db, user_id)}

    assert cards == {"1111": False, "4242": True}
    assert await service.has_usable_card(db, user_id, NOW)


async def test_two_first_cards_bound_at_once_make_one_default(
    sessions: async_sessionmaker[AsyncSession],
    gw: Gateway,
    provider: FakeProvider,
    user_id: uuid.UUID,
) -> None:
    rules = service.ReturnUrlRules(frozenset(), allow_expo_go=False)
    request = service.BindingRequest(user_id, RETURN_URL, rules, "https://t/")
    async with sessions() as db:
        first, _ = await service.start_binding(db, gw, request, NOW)
        second, _ = await service.start_binding(db, gw, request, NOW)

    async def bound(binding_id: uuid.UUID, last4: str) -> bool:
        event = WebhookEvent(
            uuid.uuid4().hex,
            WebhookEventType.CARD_BOUND,
            binding_id,
            card=CardDetails(CardBrand.VISA, last4, 12, 2030),
            token=f"fake_ok_{uuid.uuid4().hex}",
        )
        async with sessions() as db:
            return await service.receive_webhook(db, gw, provider.signed_webhook(event, NOW), NOW)

    applied = await asyncio.gather(bound(first.id, "1111"), bound(second.id, "2222"))

    assert list(applied) == [True, True]
    async with sessions() as db:
        cards = await service.list_cards(db, user_id)
    assert sorted(card.is_default for card in cards) == [False, True]
