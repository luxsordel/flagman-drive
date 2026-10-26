from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from flagman.payments.domain import (
    RETURN_URL_MAX_LENGTH,
    CardBinding,
    CardBindingStatus,
    CardBrand,
    DeclineReason,
    PaymentCard,
)


class PaymentCardOut(BaseModel):
    """What the client sees of a card; the provider's token never leaves the API."""

    id: UUID
    brand: CardBrand
    last4: str
    exp_month: int
    exp_year: int
    is_default: bool = Field(description="Holds and charges of rentals go to this card.")
    expired: bool

    @classmethod
    def of(cls, card: PaymentCard, now: datetime) -> "PaymentCardOut":
        details = card.details
        return cls(
            id=card.id,
            brand=details.brand,
            last4=details.last4,
            exp_month=details.exp_month,
            exp_year=details.exp_year,
            is_default=card.is_default,
            expired=details.is_expired(now),
        )


class PaymentCardList(BaseModel):
    items: list[PaymentCardOut]


class CardBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_url: str = Field(
        max_length=RETURN_URL_MAX_LENGTH,
        description=(
            "Where the provider's page sends the client back, with `binding_id` added to the "
            "query: the app's `flagmandrive://` link, Expo Go's `exp://` outside prod, or a page "
            "of an origin from `API_CORS_ORIGINS`."
        ),
        examples=["flagmandrive://payment-methods"],
    )


class CardBindingStarted(BaseModel):
    id: UUID
    page_url: str = Field(description="The provider's page: open it in the browser.")
    expires_at: datetime


class CardBindingOut(BaseModel):
    id: UUID
    status: CardBindingStatus
    card: PaymentCardOut | None = Field(description="With `succeeded`: the card that was bound.")
    decline_reason: DeclineReason | None = Field(description="With `declined`.")

    @classmethod
    def of(cls, binding: CardBinding, card: PaymentCard | None, now: datetime) -> "CardBindingOut":
        return cls(
            id=binding.id,
            status=binding.status(now),
            card=PaymentCardOut.of(card, now) if card else None,
            decline_reason=binding.decline_reason,
        )
