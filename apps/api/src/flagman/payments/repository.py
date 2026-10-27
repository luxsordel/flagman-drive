from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.payments.domain import (
    FINAL_STATUSES,
    CardBinding,
    CardBindingStatus,
    CardBrand,
    CardDetails,
    DeclineReason,
    OperationKind,
    OperationStatus,
    PaymentCard,
    PaymentOperation,
)
from flagman.payments.models import CardBinding as BindingRow
from flagman.payments.models import PaymentAttempt, PaymentWebhookEvent
from flagman.payments.models import PaymentCard as CardRow
from flagman.payments.models import PaymentOperation as OperationRow

# Any fixed number unique among the two-key advisory locks of the database.
_CARDS_LOCK_CLASS = 4_600_101


def _to_card(row: CardRow) -> PaymentCard:
    return PaymentCard(
        id=row.id,
        user_id=row.user_id,
        details=CardDetails(
            brand=CardBrand(row.brand),
            last4=row.last4,
            exp_month=row.exp_month,
            exp_year=row.exp_year,
        ),
        is_default=row.is_default,
        created_at=row.created_at,
    )


def _to_binding(row: BindingRow) -> CardBinding:
    return CardBinding(
        id=row.id,
        user_id=row.user_id,
        stored_status=CardBindingStatus(row.status),
        return_url=row.return_url,
        card_id=row.card_id,
        decline_reason=DeclineReason(row.decline_reason) if row.decline_reason else None,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def _to_operation(row: OperationRow) -> PaymentOperation:
    return PaymentOperation(
        id=row.id,
        user_id=row.user_id,
        card_id=row.card_id,
        kind=OperationKind(row.kind),
        amount=row.amount,
        parent_id=row.parent_id,
        idempotency_key=row.idempotency_key,
        status=OperationStatus(row.status),
        provider_ref=row.provider_ref,
        decline_reason=DeclineReason(row.decline_reason) if row.decline_reason else None,
        created_at=row.created_at,
    )


# Cards. Every query takes the owner: a card id from the client never reaches another's card.


async def lock_cards(db: AsyncSession, user_id: UUID) -> list[PaymentCard]:
    """The client's cards, locked until commit: card changes of one client go one at a time.

    A client without cards has no rows to lock, so a transaction lock on the client comes first:
    two first cards bound at once would otherwise both become the default one.
    """
    # The two-key form keeps clear of the worker's single-key locks; two clients sharing the
    # 32 bits of the id only wait for each other.
    await db.execute(
        select(func.pg_advisory_xact_lock(_CARDS_LOCK_CLASS, (user_id.int >> 96) - 2**31))
    )
    rows = await db.scalars(
        select(CardRow)
        .where(CardRow.user_id == user_id, CardRow.removed_at.is_(None))
        .order_by(CardRow.created_at, CardRow.id)
        .with_for_update()
    )
    return [_to_card(row) for row in rows]


async def list_cards(db: AsyncSession, user_id: UUID) -> list[PaymentCard]:
    rows = await db.scalars(
        select(CardRow)
        .where(CardRow.user_id == user_id, CardRow.removed_at.is_(None))
        .order_by(CardRow.created_at, CardRow.id)
    )
    return [_to_card(row) for row in rows]


async def default_card(db: AsyncSession, user_id: UUID) -> tuple[PaymentCard, bytes] | None:
    """The default card with its encrypted token."""
    row = await db.scalar(
        select(CardRow).where(
            CardRow.user_id == user_id, CardRow.is_default, CardRow.removed_at.is_(None)
        )
    )
    if row is None or row.token_ciphertext is None:
        return None
    return _to_card(row), row.token_ciphertext


@dataclass(frozen=True)
class NewCard:
    id: UUID
    user_id: UUID
    provider: str
    details: CardDetails
    token_ciphertext: bytes
    is_default: bool


async def insert_card(db: AsyncSession, card: NewCard, now: datetime) -> None:
    await db.execute(
        insert(CardRow).values(
            id=card.id,
            user_id=card.user_id,
            provider=card.provider,
            brand=card.details.brand,
            last4=card.details.last4,
            exp_month=card.details.exp_month,
            exp_year=card.details.exp_year,
            token_ciphertext=card.token_ciphertext,
            is_default=card.is_default,
            created_at=now,
        )
    )


async def remove_card(db: AsyncSession, user_id: UUID, card_id: UUID, now: datetime) -> None:
    await db.execute(
        update(CardRow)
        .where(CardRow.id == card_id, CardRow.user_id == user_id)
        .values(removed_at=now, token_ciphertext=None, is_default=False)
    )


async def set_default(db: AsyncSession, user_id: UUID, card_id: UUID) -> None:
    # The old default goes first: the partial unique index allows one default at any moment.
    await db.execute(
        update(CardRow)
        .where(CardRow.user_id == user_id, CardRow.is_default, CardRow.id != card_id)
        .values(is_default=False)
    )
    await db.execute(
        update(CardRow)
        .where(CardRow.user_id == user_id, CardRow.id == card_id, CardRow.removed_at.is_(None))
        .values(is_default=True)
    )


# Bindings


@dataclass(frozen=True)
class NewBinding:
    user_id: UUID
    provider: str
    return_url: str
    expires_at: datetime


async def insert_binding(db: AsyncSession, binding: NewBinding, now: datetime) -> CardBinding:
    row = await db.scalar(
        insert(BindingRow)
        .values(
            user_id=binding.user_id,
            provider=binding.provider,
            status=CardBindingStatus.PENDING,
            return_url=binding.return_url,
            created_at=now,
            expires_at=binding.expires_at,
        )
        .returning(BindingRow)
    )
    if row is None:
        raise RuntimeError("INSERT ... RETURNING gave no row")
    return _to_binding(row)


async def get_binding(
    db: AsyncSession, binding_id: UUID, *, user_id: UUID | None = None, for_update: bool = False
) -> CardBinding | None:
    query = select(BindingRow).where(BindingRow.id == binding_id)
    if user_id is not None:
        query = query.where(BindingRow.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_binding(row) if row else None


async def complete_binding(  # noqa: PLR0913 - the outcome is several columns at once
    db: AsyncSession,
    binding_id: UUID,
    status: CardBindingStatus,
    now: datetime,
    *,
    card_id: UUID | None = None,
    decline_reason: DeclineReason | None = None,
) -> None:
    await db.execute(
        update(BindingRow)
        .where(BindingRow.id == binding_id)
        .values(status=status, card_id=card_id, decline_reason=decline_reason, completed_at=now)
    )


async def record_webhook_event(
    db: AsyncSession, provider: str, event_id: str, now: datetime
) -> bool:
    """False if the event was applied before."""
    recorded = await db.scalar(
        insert(PaymentWebhookEvent)
        .values(provider=provider, event_id=event_id, received_at=now)
        .on_conflict_do_nothing()
        .returning(PaymentWebhookEvent.event_id)
    )
    return recorded is not None


# Operations


@dataclass(frozen=True)
class NewOperation:
    user_id: UUID
    card_id: UUID
    parent_id: UUID | None
    provider: str
    kind: OperationKind
    amount: int
    idempotency_key: str


async def insert_operation(
    db: AsyncSession, operation: NewOperation, now: datetime
) -> PaymentOperation | None:
    """None if an operation with the same idempotency key exists."""
    row = await db.scalar(
        insert(OperationRow)
        .values(
            user_id=operation.user_id,
            card_id=operation.card_id,
            parent_id=operation.parent_id,
            provider=operation.provider,
            kind=operation.kind,
            amount=operation.amount,
            idempotency_key=operation.idempotency_key,
            status=OperationStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[OperationRow.idempotency_key])
        .returning(OperationRow)
    )
    return _to_operation(row) if row else None


async def get_operation(
    db: AsyncSession, operation_id: UUID, *, for_update: bool = False
) -> PaymentOperation | None:
    query = select(OperationRow).where(OperationRow.id == operation_id)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_operation(row) if row else None


async def get_operation_by_key(
    db: AsyncSession, idempotency_key: str, *, for_update: bool = False
) -> PaymentOperation | None:
    query = select(OperationRow).where(OperationRow.idempotency_key == idempotency_key)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_operation(row) if row else None


async def children(db: AsyncSession, parent_id: UUID) -> list[PaymentOperation]:
    rows = await db.scalars(select(OperationRow).where(OperationRow.parent_id == parent_id))
    return [_to_operation(row) for row in rows]


async def card_token(db: AsyncSession, card_id: UUID) -> bytes | None:
    """The encrypted token of a card, also of a removed one: None then."""
    token: bytes | None = await db.scalar(
        select(CardRow.token_ciphertext).where(CardRow.id == card_id)
    )
    return token


@dataclass(frozen=True)
class OperationOutcome:
    status: OperationStatus
    provider_ref: str | None
    decline_reason: DeclineReason | None
    # For the attempts log only: the kind of failure, never the provider's answer.
    error: str | None


async def finish_attempt(
    db: AsyncSession, operation_id: UUID, outcome: OperationOutcome, now: datetime
) -> bool:
    """Logs the attempt and stores its outcome; False if the operation had a final one already.

    Two retries of one operation may run at once: the one that learned the outcome must not be
    overwritten by the other that timed out, or a hold the provider made would be lost to us.
    """
    changed = await db.scalar(
        update(OperationRow)
        .where(OperationRow.id == operation_id, OperationRow.status.not_in(FINAL_STATUSES))
        .values(
            status=outcome.status,
            provider_ref=outcome.provider_ref,
            decline_reason=outcome.decline_reason,
            updated_at=now,
        )
        .returning(OperationRow.id)
    )
    await db.execute(
        insert(PaymentAttempt).values(
            operation_id=operation_id, attempted_at=now, status=outcome.status, error=outcome.error
        )
    )
    return changed is not None
