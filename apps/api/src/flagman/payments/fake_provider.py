"""The test payment provider of the pilot, see docs/adr/0019. No money moves.

It plays the provider's side: its own card page (flagman.payments.fake_pages), test card numbers
and webhooks signed like the telematics ones (docs/adr/0007). Its state lives in what it hands out:
the page session is signed, and a card token says how the card behaves. So it needs no tables.
"""

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from flagman.core import signing
from flagman.payments.domain import (
    CardBrand,
    CardDetails,
    DeclineReason,
    OperationStatus,
    WebhookSignatureInvalidError,
)
from flagman.payments.ports import (
    CardBindingPage,
    CardBindingStart,
    OperationRequest,
    OperationResult,
    PaymentWebhook,
    WebhookEvent,
    WebhookEventType,
)

NAME = "fake"
PAGE_PATH = "/fake-provider/cards"
WEBHOOK_PATH = "/payments/webhooks/fake"

_SESSION_CONTEXT = b"flagman-fake-provider-session\n"
_TOKEN_PREFIX = "fake_"  # noqa: S105 - a prefix of test tokens, not a secret
_OPERATION_PREFIX = "fake_op_"


class CardBehavior(StrEnum):
    OK = "ok"
    # Binds, but every hold and charge is declined: the "not enough money" case of a trip.
    NO_FUNDS = "nofunds"


@dataclass(frozen=True)
class TestCard:
    brand: CardBrand
    # None: the binding itself is declined.
    behavior: CardBehavior | None


# Numbers known from the providers' own sandboxes; any other valid number is declined.
TEST_CARDS: dict[str, TestCard] = {
    "4242424242424242": TestCard(CardBrand.VISA, CardBehavior.OK),
    "5555555555554444": TestCard(CardBrand.MASTERCARD, CardBehavior.OK),
    "4000000000009995": TestCard(CardBrand.VISA, CardBehavior.NO_FUNDS),
    "4000000000000002": TestCard(CardBrand.VISA, None),
}


class FakeSessionInvalidError(Exception):
    """The page link was forged, damaged or has expired."""


@dataclass(frozen=True)
class FakeSession:
    binding_id: UUID
    return_url: str
    expires_at: datetime


class _CardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand: CardBrand
    last4: str = Field(pattern=r"^[0-9]{4}$")
    exp_month: int = Field(ge=1, le=12)
    exp_year: int = Field(ge=2000, le=2100)


class _EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    type: Literal["card_bound", "card_binding_declined", "card_binding_cancelled"]
    binding_id: UUID
    card: _CardIn | None = None
    token: str | None = Field(default=None, max_length=128)
    decline_reason: DeclineReason | None = None


def luhn_valid(number: str) -> bool:
    total = 0
    for index, digit in enumerate(reversed(number)):
        value = int(digit)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2  # noqa: PLR2004
        total += value
    return total % 10 == 0


def _operation_ref(idempotency_key: str) -> str:
    # The same key gives the same reference, as a real provider answers a repeated request.
    return _OPERATION_PREFIX + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]


def _behavior(token: str | None) -> CardBehavior | None:
    if token is None or not token.startswith(_TOKEN_PREFIX):
        return None
    name = token.removeprefix(_TOKEN_PREFIX).split("_", 1)[0]
    return CardBehavior(name) if name in set(CardBehavior) else None


class FakeProvider:
    name = NAME

    def __init__(self, secret: bytes, tolerance_s: int) -> None:
        self.secret = secret
        self.tolerance_s = tolerance_s

    # The page

    def start_card_binding(self, start: CardBindingStart) -> CardBindingPage:
        payload = json.dumps(
            {
                "b": str(start.binding_id),
                "r": start.return_url,
                "e": int(start.expires_at.timestamp()),
            },
            separators=(",", ":"),
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        session = f"{encoded}.{self._session_mac(encoded)}"
        return CardBindingPage(
            url=start.api_base_url.rstrip("/") + PAGE_PATH + "?" + urlencode({"session": session})
        )

    def read_session(self, session: str, now: datetime) -> FakeSession:
        encoded, _, mac = session.partition(".")
        if not hmac.compare_digest(self._session_mac(encoded).encode(), mac.encode()):
            raise FakeSessionInvalidError
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            result = FakeSession(
                binding_id=UUID(payload["b"]),
                return_url=str(payload["r"]),
                expires_at=datetime.fromtimestamp(int(payload["e"]), UTC),
            )
        except (ValueError, KeyError, TypeError) as error:
            raise FakeSessionInvalidError from error
        if now >= result.expires_at:
            raise FakeSessionInvalidError
        return result

    def _session_mac(self, encoded: str) -> str:
        return hmac.new(
            self.secret, _SESSION_CONTEXT + encoded.encode(), hashlib.sha256
        ).hexdigest()

    def card_entered(
        self, session: FakeSession, number: str, exp_month: int, exp_year: int
    ) -> WebhookEvent:
        """What the provider reports once the client entered a valid number on the page."""
        card = TEST_CARDS.get(number)
        event_id = uuid.uuid4().hex
        if card is None or card.behavior is None:
            return WebhookEvent(
                event_id,
                WebhookEventType.CARD_BINDING_DECLINED,
                session.binding_id,
                decline_reason=DeclineReason.DECLINED,
            )
        return WebhookEvent(
            event_id,
            WebhookEventType.CARD_BOUND,
            session.binding_id,
            card=CardDetails(card.brand, number[-4:], exp_month, exp_year),
            token=f"{_TOKEN_PREFIX}{card.behavior}_{secrets.token_urlsafe(18)}",
        )

    def cancelled(self, session: FakeSession) -> WebhookEvent:
        return WebhookEvent(
            uuid.uuid4().hex, WebhookEventType.CARD_BINDING_CANCELLED, session.binding_id
        )

    @staticmethod
    def return_to(session: FakeSession) -> str:
        """The client's way back, with the binding to ask the API about."""
        parts = urlsplit(session.return_url)
        query = "&".join(
            part
            for part in (parts.query, urlencode({"binding_id": str(session.binding_id)}))
            if part
        )
        return urlunsplit(parts._replace(query=query))

    # Webhooks

    def signed_webhook(self, event: WebhookEvent, now: datetime) -> PaymentWebhook:
        body = json.dumps(
            {
                "event_id": event.event_id,
                "type": event.type,
                "binding_id": str(event.binding_id),
                "card": (
                    {
                        "brand": event.card.brand,
                        "last4": event.card.last4,
                        "exp_month": event.card.exp_month,
                        "exp_year": event.card.exp_year,
                    }
                    if event.card
                    else None
                ),
                "token": event.token,
                "decline_reason": event.decline_reason,
            }
        ).encode()
        timestamp = int(now.timestamp())
        target = WEBHOOK_PATH.encode()
        return PaymentWebhook(
            method="POST",
            target=target,
            headers={
                signing.TIMESTAMP_HEADER: str(timestamp),
                signing.SIGNATURE_HEADER: signing.sign(
                    self.secret, timestamp, "POST", target, body
                ),
                "Content-Type": "application/json",
            },
            body=body,
        )

    def parse_webhook(self, webhook: PaymentWebhook, now: datetime) -> WebhookEvent | None:
        try:
            signing.verify(
                self.secret,
                signing.SignedRequest(
                    method=webhook.method,
                    target=webhook.target,
                    body=webhook.body,
                    timestamp_header=webhook.headers.get(signing.TIMESTAMP_HEADER),
                    signature_header=webhook.headers.get(signing.SIGNATURE_HEADER),
                ),
                now=now,
                tolerance_s=self.tolerance_s,
            )
        except signing.SignatureError as error:
            raise WebhookSignatureInvalidError from error
        event = _EventIn.model_validate_json(webhook.body)
        card = (
            CardDetails(
                event.card.brand, event.card.last4, event.card.exp_month, event.card.exp_year
            )
            if event.card
            else None
        )
        kind = WebhookEventType(event.type)
        if kind is WebhookEventType.CARD_BOUND and (card is None or not event.token):
            raise ValueError("a bound card comes with its details and token")
        return WebhookEvent(
            event.event_id, kind, event.binding_id, card, event.token, event.decline_reason
        )

    # Operations

    async def hold(self, request: OperationRequest) -> OperationResult:
        return self._from_card(request)

    async def charge(self, request: OperationRequest) -> OperationResult:
        return self._from_card(request)

    async def capture(self, request: OperationRequest) -> OperationResult:
        return self._on_parent(request)

    async def void(self, request: OperationRequest) -> OperationResult:
        return self._on_parent(request)

    async def refund(self, request: OperationRequest) -> OperationResult:
        return self._on_parent(request)

    @staticmethod
    def _from_card(request: OperationRequest) -> OperationResult:
        match _behavior(request.token):
            case CardBehavior.OK:
                return OperationResult(
                    OperationStatus.SUCCEEDED, _operation_ref(request.idempotency_key)
                )
            case CardBehavior.NO_FUNDS:
                return OperationResult(
                    OperationStatus.DECLINED, decline_reason=DeclineReason.INSUFFICIENT_FUNDS
                )
            case None:
                return OperationResult(
                    OperationStatus.DECLINED, decline_reason=DeclineReason.DECLINED
                )

    @staticmethod
    def _on_parent(request: OperationRequest) -> OperationResult:
        if request.parent_ref is None or not request.parent_ref.startswith(_OPERATION_PREFIX):
            return OperationResult(OperationStatus.DECLINED, decline_reason=DeclineReason.DECLINED)
        return OperationResult(OperationStatus.SUCCEEDED, _operation_ref(request.idempotency_key))
