"""Public interface of the payments for other modules, see docs/adr/0019.

Every function here is a command of its own and opens its own transactions: a payment operation
calls the provider between two of them, and a transaction must not stay open over a network call.
A rental (stage 3) therefore records its own state after the payment returns, using the
operation's idempotency key to make a retry safe.
"""

import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    SYSTEM,
    AuditAction,
    AuditEntry,
    AuditTarget,
    AuditTargetType,
    RequestOrigin,
)
from flagman.core.config import Settings
from flagman.payments import repository
from flagman.payments.domain import (
    BINDING_TTL,
    CardBinding,
    CardBindingNotFoundError,
    CardBindingStatus,
    DeclineReason,
    IdempotencyKeyReusedError,
    NoUsableCardError,
    OperationKind,
    OperationStatus,
    PaymentCard,
    PaymentCardExpiredError,
    PaymentCardNotFoundError,
    PaymentOperation,
    PaymentOperationNotFoundError,
    PaymentsUnavailableError,
    check_amount,
    check_child,
    check_idempotency_key,
    check_return_url,
    decrypt_card_token,
    default_after_removal,
    encrypt_card_token,
)
from flagman.payments.fake_provider import FakeProvider
from flagman.payments.ports import (
    CardBindingStart,
    OperationRequest,
    OperationResult,
    PaymentProvider,
    PaymentProviderError,
    PaymentWebhook,
    WebhookEventType,
)

logger = structlog.get_logger(__name__)

# Every outcome learned from the provider is a money event of its own (docs/03-security.md).
_AUDIT_ACTIONS = {
    OperationStatus.SUCCEEDED: AuditAction.PAYMENT_SUCCEEDED,
    OperationStatus.DECLINED: AuditAction.PAYMENT_DECLINED,
    OperationStatus.FAILED: AuditAction.PAYMENT_FAILED,
}


@dataclass(frozen=True)
class Gateway:
    """The configured provider and the key its card tokens are stored under."""

    provider: PaymentProvider
    token_key: bytes


def gateway(settings: Settings) -> Gateway:
    """Raises PaymentsUnavailableError when PAYMENTS_PROVIDER is not set."""
    provider = fake_provider(settings)
    key = settings.payments_token_key
    if key is None:
        raise PaymentsUnavailableError
    return Gateway(provider=provider, token_key=key)


def fake_provider(settings: Settings) -> FakeProvider:
    # The settings check that the secret and the key come with the provider.
    secret = settings.payments_webhook_secret
    if settings.payments_provider != "fake" or secret is None:
        raise PaymentsUnavailableError
    return FakeProvider(
        secret=secret.get_secret_value().encode(),
        tolerance_s=settings.payments_webhook_tolerance_seconds,
    )


# Cards of a client


async def list_cards(db: AsyncSession, user_id: UUID) -> list[PaymentCard]:
    async with db.begin():
        return await repository.list_cards(db, user_id)


async def has_usable_card(db: AsyncSession, user_id: UUID, now: datetime) -> bool:
    """Whether a hold or a charge has a card to go to: a default card that has not expired."""
    if db.in_transaction():
        default = await repository.default_card(db, user_id)
    else:
        async with db.begin():
            default = await repository.default_card(db, user_id)
    return default is not None and not default[0].details.is_expired(now)


@dataclass(frozen=True)
class ReturnUrlRules:
    # The web client's origins: the same as API_CORS_ORIGINS.
    web_origins: Collection[str]
    # Expo Go's exp:// links, for development only.
    allow_expo_go: bool


def return_url_rules(settings: Settings) -> ReturnUrlRules:
    return ReturnUrlRules(
        web_origins=frozenset(settings.api_cors_origins), allow_expo_go=not settings.is_prod
    )


@dataclass(frozen=True)
class BindingRequest:
    user_id: UUID
    return_url: str
    rules: ReturnUrlRules
    # The API's address as the client reached it: the fake provider's page is served there.
    api_base_url: str


async def start_binding(
    db: AsyncSession, gw: Gateway, request: BindingRequest, now: datetime
) -> tuple[CardBinding, str]:
    """Returns the binding and the provider's page to open; raises ReturnUrlNotAllowedError."""
    rules = request.rules
    checked = check_return_url(
        request.return_url, rules.web_origins, allow_expo_go=rules.allow_expo_go
    )
    async with db.begin():
        binding = await repository.insert_binding(
            db,
            repository.NewBinding(request.user_id, gw.provider.name, checked, now + BINDING_TTL),
            now,
        )
    page = gw.provider.start_card_binding(
        CardBindingStart(binding.id, checked, request.api_base_url, binding.expires_at)
    )
    logger.info("card_binding_started", binding_id=str(binding.id))
    return binding, page.url


async def get_binding(
    db: AsyncSession, user_id: UUID, binding_id: UUID
) -> tuple[CardBinding, PaymentCard | None]:
    """The binding with the card it added; another client's binding is not found."""
    async with db.begin():
        binding = await repository.get_binding(db, binding_id, user_id=user_id)
        if binding is None:
            raise CardBindingNotFoundError
        cards = await repository.list_cards(db, user_id)
    card = next((card for card in cards if card.id == binding.card_id), None)
    return binding, card


async def remove_card(db: AsyncSession, user_id: UUID, card_id: UUID, now: datetime) -> None:
    """Forgets the card's token; a removed default card hands that role to the newest other one."""
    async with db.begin():
        cards = await repository.lock_cards(db, user_id)
        card = next((card for card in cards if card.id == card_id), None)
        if card is None:
            raise PaymentCardNotFoundError
        await repository.remove_card(db, user_id, card_id, now)
        if card.is_default:
            successor = default_after_removal(cards, card_id, now)
            if successor is not None:
                await repository.set_default(db, user_id, successor.id)
    logger.info("payment_card_removed", card_id=str(card_id))


async def set_default_card(
    db: AsyncSession, user_id: UUID, card_id: UUID, now: datetime
) -> PaymentCard:
    """Raises PaymentCardExpiredError: holds and charges go to the default card only."""
    async with db.begin():
        cards = await repository.lock_cards(db, user_id)
        chosen = next((card for card in cards if card.id == card_id), None)
        if chosen is None:
            raise PaymentCardNotFoundError
        if chosen.details.is_expired(now):
            raise PaymentCardExpiredError
        await repository.set_default(db, user_id, card_id)
        cards = await repository.list_cards(db, user_id)
    return next(card for card in cards if card.id == card_id)


# Webhooks


async def receive_webhook(
    db: AsyncSession, gw: Gateway, webhook: PaymentWebhook, now: datetime
) -> bool:
    """Applies a provider's event; returns False for a repeated, late or unknown one.

    Raises WebhookSignatureInvalidError before anything in the body is read, and ValueError for a
    body that is not a valid event.
    """
    event = gw.provider.parse_webhook(webhook, now)
    if event is None:
        return False
    provider = gw.provider.name
    async with db.begin():
        if not await repository.record_webhook_event(db, provider, event.event_id, now):
            logger.info("payment_webhook_repeated", provider=provider)
            return False
        binding = await repository.get_binding(db, event.binding_id, for_update=True)
        # A binding finishes once: a late event for a finished one changes nothing.
        if binding is None or binding.stored_status is not CardBindingStatus.PENDING:
            logger.warning("payment_webhook_ignored", provider=provider, event_type=event.type)
            return False
        match event.type:
            case WebhookEventType.CARD_BOUND:
                if event.card is None or event.token is None:
                    raise ValueError("a bound card comes with its details and token")
                cards = await repository.lock_cards(db, binding.user_id)
                card_id = uuid.uuid4()
                await repository.insert_card(
                    db,
                    repository.NewCard(
                        id=card_id,
                        user_id=binding.user_id,
                        provider=provider,
                        details=event.card,
                        token_ciphertext=encrypt_card_token(event.token, gw.token_key, card_id),
                        is_default=False,
                    ),
                    now,
                )
                # The first card, or one that replaces an expired default, takes over the payments.
                if not any(card.is_default and not card.details.is_expired(now) for card in cards):
                    await repository.set_default(db, binding.user_id, card_id)
                await repository.complete_binding(
                    db, binding.id, CardBindingStatus.SUCCEEDED, now, card_id=card_id
                )
            case WebhookEventType.CARD_BINDING_DECLINED:
                await repository.complete_binding(
                    db,
                    binding.id,
                    CardBindingStatus.DECLINED,
                    now,
                    decline_reason=event.decline_reason or DeclineReason.DECLINED,
                )
            case WebhookEventType.CARD_BINDING_CANCELLED:
                await repository.complete_binding(db, binding.id, CardBindingStatus.CANCELLED, now)
    logger.info("card_binding_completed", binding_id=str(binding.id), event_type=event.type)
    return True


# Payment operations, for rentals


async def get_operation(db: AsyncSession, operation_id: UUID) -> PaymentOperation:
    async with db.begin():
        operation = await repository.get_operation(db, operation_id)
    if operation is None:
        raise PaymentOperationNotFoundError
    return operation


@dataclass(frozen=True)
class Payment:
    # Kopecks of BYN.
    amount: int
    # One key is one operation: a repeated call with it never moves money twice.
    idempotency_key: str


async def hold(
    db: AsyncSession, gw: Gateway, user_id: UUID, payment: Payment, now: datetime
) -> PaymentOperation:
    """Holds the amount on the client's default card.

    Like every operation here: a repeated call with the same key returns the same operation and
    retries the provider only while the outcome is unknown (`failed` or `pending`). The result is
    `succeeded`, `declined` (see `decline_reason`) or `failed`; raises NoUsableCardError,
    IdempotencyKeyReusedError.
    """
    return await _from_card(db, gw, user_id, payment, now, kind=OperationKind.HOLD)


async def charge(
    db: AsyncSession, gw: Gateway, user_id: UUID, payment: Payment, now: datetime
) -> PaymentOperation:
    """Takes the amount from the default card: what a trip cost above the hold, or a debt."""
    return await _from_card(db, gw, user_id, payment, now, kind=OperationKind.CHARGE)


async def capture(
    db: AsyncSession, gw: Gateway, hold_id: UUID, payment: Payment, now: datetime
) -> PaymentOperation:
    """Takes up to the held amount; the provider releases the rest of the hold."""
    return await _on_parent(
        db,
        gw,
        hold_id,
        now,
        kind=OperationKind.CAPTURE,
        amount=payment.amount,
        idempotency_key=payment.idempotency_key,
    )


async def void(
    db: AsyncSession, gw: Gateway, hold_id: UUID, idempotency_key: str, now: datetime
) -> PaymentOperation:
    """Releases a hold without taking anything."""
    return await _on_parent(
        db, gw, hold_id, now, kind=OperationKind.VOID, amount=None, idempotency_key=idempotency_key
    )


async def refund(
    db: AsyncSession, gw: Gateway, operation_id: UUID, payment: Payment, now: datetime
) -> PaymentOperation:
    """Returns up to what a capture or a charge took, less earlier refunds."""
    return await _on_parent(
        db,
        gw,
        operation_id,
        now,
        kind=OperationKind.REFUND,
        amount=payment.amount,
        idempotency_key=payment.idempotency_key,
    )


def _check_same(
    existing: PaymentOperation,
    kind: OperationKind,
    amount: int,
    user_id: UUID,
    parent_id: UUID | None,
) -> None:
    if (
        existing.kind is not kind
        or existing.amount != amount
        or existing.user_id != user_id
        or existing.parent_id != parent_id
    ):
        raise IdempotencyKeyReusedError


async def _token(db: AsyncSession, gw: Gateway, card_id: UUID) -> str:
    ciphertext = await repository.card_token(db, card_id)
    if ciphertext is None:
        # Removed between a failed attempt and its retry.
        raise NoUsableCardError
    return decrypt_card_token(ciphertext, gw.token_key, card_id)


async def _from_card(  # noqa: PLR0913 - the shared body of hold and charge
    db: AsyncSession,
    gw: Gateway,
    user_id: UUID,
    payment: Payment,
    now: datetime,
    *,
    kind: OperationKind,
) -> PaymentOperation:
    amount, idempotency_key = payment.amount, payment.idempotency_key
    check_amount(amount)
    check_idempotency_key(idempotency_key)
    async with db.begin():

        async def new() -> repository.NewOperation:
            default = await repository.default_card(db, user_id)
            if default is None or default[0].details.is_expired(now):
                raise NoUsableCardError
            card = default[0]
            return repository.NewOperation(
                user_id, card.id, None, gw.provider.name, kind, amount, idempotency_key
            )

        operation = await _claim(db, idempotency_key, (kind, amount, user_id, None), new, now)
        if operation.is_final:
            return operation
        request = OperationRequest(
            idempotency_key, amount, token=await _token(db, gw, operation.card_id)
        )
    call = gw.provider.hold if kind is OperationKind.HOLD else gw.provider.charge
    return await _call(db, operation, call, request, now)


async def _on_parent(  # noqa: PLR0913 - the shared body of capture, void and refund
    db: AsyncSession,
    gw: Gateway,
    parent_id: UUID,
    now: datetime,
    *,
    kind: OperationKind,
    amount: int | None,
    idempotency_key: str,
) -> PaymentOperation:
    if amount is not None:
        check_amount(amount)
    check_idempotency_key(idempotency_key)
    async with db.begin():
        # The parent row lock makes two captures of one hold, or refunds above the total, wait for
        # each other here instead of both passing the checks.
        parent = await repository.get_operation(db, parent_id, for_update=True)
        if parent is None:
            raise PaymentOperationNotFoundError
        wanted = parent.amount if amount is None else amount

        async def new() -> repository.NewOperation:
            check_child(parent, kind, wanted, await repository.children(db, parent_id))
            return repository.NewOperation(
                parent.user_id,
                parent.card_id,
                parent_id,
                gw.provider.name,
                kind,
                wanted,
                idempotency_key,
            )

        operation = await _claim(
            db, idempotency_key, (kind, wanted, parent.user_id, parent_id), new, now
        )
        if operation.is_final:
            return operation
        request = OperationRequest(idempotency_key, wanted, parent_ref=parent.provider_ref)
    calls = {
        OperationKind.CAPTURE: gw.provider.capture,
        OperationKind.VOID: gw.provider.void,
        OperationKind.REFUND: gw.provider.refund,
    }
    return await _call(db, operation, calls[kind], request, now)


async def _claim(
    db: AsyncSession,
    idempotency_key: str,
    expected: tuple[OperationKind, int, UUID, UUID | None],
    new: Callable[[], Awaitable[repository.NewOperation]],
    now: datetime,
) -> PaymentOperation:
    """The operation of the key: the one made before, or a new pending one from `new`."""
    kind, amount, user_id, parent_id = expected
    # Twice at most: a concurrent call with the same key may insert it between the two queries;
    # the insert then waits for it to commit, and the second look finds its row.
    for _ in range(2):
        existing = await repository.get_operation_by_key(db, idempotency_key, for_update=True)
        if existing is not None:
            _check_same(existing, kind, amount, user_id, parent_id)
            return existing
        inserted = await repository.insert_operation(db, await new(), now)
        if inserted is not None:
            return inserted
    raise RuntimeError("the payment operation of the key vanished")


async def _call(
    db: AsyncSession,
    operation: PaymentOperation,
    call: Callable[[OperationRequest], Awaitable[OperationResult]],
    request: OperationRequest,
    now: datetime,
) -> PaymentOperation:
    try:
        result = await call(request)
        outcome = repository.OperationOutcome(
            result.status, result.provider_ref, result.decline_reason, None
        )
    except PaymentProviderError as error:
        outcome = repository.OperationOutcome(
            OperationStatus.FAILED, None, None, type(error).__name__
        )
    async with db.begin():
        if await repository.finish_attempt(db, operation.id, outcome, now):
            await audit.record(
                db,
                AuditEntry(
                    SYSTEM,
                    _AUDIT_ACTIONS[outcome.status],
                    RequestOrigin(),
                    AuditTarget(AuditTargetType.PAYMENT_OPERATION, operation.id),
                    details={
                        "kind": operation.kind.value,
                        "amount": operation.amount,
                        "user_id": operation.user_id,
                    },
                ),
            )
        finished = await repository.get_operation(db, operation.id)
    if finished is None:
        raise RuntimeError("the payment operation vanished")
    logger.info(
        "payment_operation",
        operation_id=str(finished.id),
        kind=finished.kind,
        amount=finished.amount,
        status=finished.status,
    )
    return finished
