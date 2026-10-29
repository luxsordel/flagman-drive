from collections.abc import Iterable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.payments.domain import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    RETURN_URL_MAX_LENGTH,
    CardBindingStatus,
    CardBrand,
    DeclineReason,
    OperationKind,
    OperationStatus,
)

ENUM_LENGTH = 32
PROVIDER_LENGTH = 32
PROVIDER_REF_LENGTH = 128
EVENT_ID_LENGTH = 64

_STORED_BINDING_STATUSES = [status for status in CardBindingStatus if status != "expired"]


def _known(column: str, values: Iterable[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class PaymentCard(Base):
    __tablename__ = "payment_cards"
    __table_args__ = (
        CheckConstraint(_known("brand", CardBrand), name="brand_known"),
        CheckConstraint("exp_month BETWEEN 1 AND 12", name="exp_month_range"),
        # A removed card loses its token: nothing can charge it any more.
        CheckConstraint("(removed_at IS NULL) = (token_ciphertext IS NOT NULL)", name="token_live"),
        CheckConstraint("NOT (is_default AND removed_at IS NOT NULL)", name="removed_not_default"),
        # One default card per client: the database settles two requests that race.
        Index(
            "uq_payment_cards_user_id_default",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH))
    brand: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    last4: Mapped[str] = mapped_column(String(4))
    exp_month: Mapped[int] = mapped_column(SmallInteger)
    exp_year: Mapped[int] = mapped_column(SmallInteger)
    # The provider's token, encrypted with PAYMENTS_TOKEN_ENCRYPTION_KEY and the card id.
    token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_default: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Removed cards stay: payment operations keep pointing at them.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CardBinding(Base):
    __tablename__ = "card_bindings"
    __table_args__ = (
        CheckConstraint(_known("status", _STORED_BINDING_STATUSES), name="status_known"),
        CheckConstraint(
            "(status = 'succeeded') = (card_id IS NOT NULL)", name="card_only_when_succeeded"
        ),
        CheckConstraint(
            "decline_reason IS NULL OR " + _known("decline_reason", DeclineReason),
            name="decline_reason_known",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH))
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    return_url: Mapped[str] = mapped_column(String(RETURN_URL_MAX_LENGTH))
    card_id: Mapped[UUID | None] = mapped_column(ForeignKey("payment_cards.id"))
    decline_reason: Mapped[str | None] = mapped_column(String(ENUM_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentWebhookEvent(Base):
    """Events already applied: a repeated webhook finds its id here and changes nothing."""

    __tablename__ = "payment_webhook_events"

    provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(EVENT_ID_LENGTH), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaymentOperation(Base):
    __tablename__ = "payment_operations"
    __table_args__ = (
        CheckConstraint(_known("kind", OperationKind), name="kind_known"),
        CheckConstraint(_known("status", OperationStatus), name="status_known"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "(kind IN ('hold', 'charge')) = (parent_id IS NULL)", name="parent_matches_kind"
        ),
        CheckConstraint(
            "decline_reason IS NULL OR " + _known("decline_reason", DeclineReason),
            name="decline_reason_known",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    card_id: Mapped[UUID] = mapped_column(ForeignKey("payment_cards.id"))
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("payment_operations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(PROVIDER_LENGTH))
    kind: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    # Kopecks of BYN.
    amount: Mapped[int]
    # The caller's key, also sent to the provider: one key is one operation, whatever the retries.
    idempotency_key: Mapped[str] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH), unique=True)
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    provider_ref: Mapped[str | None] = mapped_column(String(PROVIDER_REF_LENGTH))
    decline_reason: Mapped[str | None] = mapped_column(String(ENUM_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaymentAttempt(Base):
    """Every call to the provider for an operation, with what came of it."""

    __tablename__ = "payment_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_operations.id", ondelete="CASCADE"), index=True
    )
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    # The kind of failure, e.g. a timeout; never the provider's raw answer, which may hold data.
    error: Mapped[str | None] = mapped_column(String(PROVIDER_REF_LENGTH))
