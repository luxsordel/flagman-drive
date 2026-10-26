from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from flagman.payments.domain import CardDetails, DeclineReason, OperationStatus


@dataclass(frozen=True)
class CardBindingStart:
    binding_id: UUID
    # Where the provider's page sends the client when they are done; checked by the service.
    return_url: str
    # The API's address as the client reached it: the fake provider's page lives there too.
    api_base_url: str
    expires_at: datetime


@dataclass(frozen=True)
class CardBindingPage:
    """The provider's page the client enters the card on: our API never sees the number."""

    url: str


@dataclass(frozen=True)
class PaymentWebhook:
    """A webhook as it came, before anything in it is trusted."""

    method: str
    # Path and query string, bytes as sent.
    target: bytes
    # Looked up case-insensitively, as Starlette's request headers are.
    headers: Mapping[str, str]
    body: bytes


class WebhookEventType(StrEnum):
    CARD_BOUND = "card_bound"
    CARD_BINDING_DECLINED = "card_binding_declined"
    CARD_BINDING_CANCELLED = "card_binding_cancelled"


@dataclass(frozen=True)
class WebhookEvent:
    # The provider's id of the event: a repeated webhook carries the same one.
    event_id: str
    type: WebhookEventType
    binding_id: UUID
    # Only with CARD_BOUND. The token is what the provider charges the card by: it is encrypted
    # before it is stored and never logged or sent to the client.
    card: CardDetails | None = None
    token: str | None = None
    decline_reason: DeclineReason | None = None


@dataclass(frozen=True)
class OperationRequest:
    # Sent to the provider as is: a retry with the same key never moves money twice.
    idempotency_key: str
    amount: int
    # For a hold and a charge: the card's token.
    token: str | None = None
    # For a capture, a void and a refund: the provider's reference of the operation it refers to.
    parent_ref: str | None = None


@dataclass(frozen=True)
class OperationResult:
    status: OperationStatus
    provider_ref: str | None = None
    decline_reason: DeclineReason | None = None


class PaymentProviderError(Exception):
    """The provider did not answer or answered nonsense: the outcome of the call is unknown."""


class PaymentProvider(Protocol):
    """One card payment provider (docs/02-architecture.md, docs/adr/0019).

    The pilot runs on flagman.payments.fake_provider; a real one (bePaid or the like) gets an
    adapter of its own with its own page, signature and body format, and the rest stays as it is.
    """

    name: str

    def start_card_binding(self, start: CardBindingStart) -> CardBindingPage: ...

    def parse_webhook(self, webhook: PaymentWebhook, now: datetime) -> WebhookEvent | None:
        """Checks that the provider sent the request, then reads the event from the body.

        Raises WebhookSignatureInvalidError before the body is parsed, and ValueError for a body
        that is not a valid event. None for an event the API does not handle.
        """
        ...

    async def hold(self, request: OperationRequest) -> OperationResult:
        """Each call raises PaymentProviderError when the outcome is unknown."""
        ...

    async def capture(self, request: OperationRequest) -> OperationResult: ...

    async def charge(self, request: OperationRequest) -> OperationResult: ...

    async def void(self, request: OperationRequest) -> OperationResult: ...

    async def refund(self, request: OperationRequest) -> OperationResult: ...
