"""Cards, card bindings and payment operations, see docs/adr/0019. No I/O here."""

import re
import secrets
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from flagman.core.errors import AppError

# The client has this long to enter the card on the provider's page.
BINDING_TTL = timedelta(minutes=30)
RETURN_URL_MAX_LENGTH = 512
IDEMPOTENCY_KEY_MAX_LENGTH = 128
# The app's own scheme (apps/mobile/app.config.ts) and Expo Go's, which only development uses.
APP_SCHEME = "flagmandrive"
EXPO_GO_SCHEMES = frozenset({"exp", "exps"})

_TOKEN_NONCE_BYTES = 12
_DECEMBER = 12
_MIN_EXP_YEAR = 2000
_MAX_EXP_YEAR = 2100
# [0-9] rather than \d: \d also matches non-ASCII digits.
_LAST4 = re.compile(r"[0-9]{4}")


class CardBrand(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    VISA = "visa"
    MASTERCARD = "mastercard"
    MIR = "mir"
    BELCARD = "belcard"
    OTHER = "other"


class CardBindingStatus(StrEnum):
    """Values are stored as is, except EXPIRED: it is a pending binding past its time."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DeclineReason(StrEnum):
    DECLINED = "declined"
    INSUFFICIENT_FUNDS = "insufficient_funds"


class OperationKind(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    # Pre-authorization when a trip starts: the money is held on the card, not taken.
    HOLD = "hold"
    # Takes held money, at most the held amount; the rest of the hold is released.
    CAPTURE = "capture"
    # A payment straight from the card: what the trip cost above the hold, or a debt.
    CHARGE = "charge"
    # Releases a hold without taking anything.
    VOID = "void"
    REFUND = "refund"


class OperationStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    # The provider did not answer: the outcome is unknown, retrying with the same key is safe.
    FAILED = "failed"


FINAL_STATUSES = frozenset({OperationStatus.SUCCEEDED, OperationStatus.DECLINED})


class PaymentsUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "payments-unavailable"
    title = "Payments are not configured"


class PaymentCardNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "payment-card-not-found"
    title = "Payment card not found"


class CardBindingNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "card-binding-not-found"
    title = "Card binding not found"


class ReturnUrlNotAllowedError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "return-url-not-allowed"
    title = "The provider cannot return the client to this address"


class NoUsableCardError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "no-usable-card"
    title = "Bind a card that has not expired first"


class PaymentOperationNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "payment-operation-not-found"
    title = "Payment operation not found"


class PaymentOperationInvalidError(AppError):
    """The operation does not fit the one it refers to: a capture above the hold, a second void."""

    status = HTTPStatus.CONFLICT
    slug = "payment-operation-invalid"
    title = "The payment operation is not possible"


class IdempotencyKeyReusedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "idempotency-key-reused"
    title = "The idempotency key was used for another operation"


class WebhookSignatureInvalidError(AppError):
    """No reason is given: the log has it, a forger would only learn from it."""

    status = HTTPStatus.UNAUTHORIZED
    slug = "webhook-signature-invalid"
    title = "Webhook signature is invalid"


class CardTokenUnreadableError(Exception):
    """The stored token does not decrypt, e.g. after PAYMENTS_TOKEN_ENCRYPTION_KEY was changed."""


@dataclass(frozen=True)
class CardDetails:
    """What the provider tells about a card; the number itself never reaches the API."""

    brand: CardBrand
    last4: str
    exp_month: int
    exp_year: int

    def __post_init__(self) -> None:
        if not _LAST4.fullmatch(self.last4):
            raise ValueError("last4 must be 4 ASCII digits")
        if not 1 <= self.exp_month <= _DECEMBER:
            raise ValueError("exp_month must be within 1..12")
        if not _MIN_EXP_YEAR <= self.exp_year <= _MAX_EXP_YEAR:
            raise ValueError("exp_year must be a four-digit year")

    def is_expired(self, now: datetime) -> bool:
        """A card works through the last day of its month; the provider has the final word."""
        year, month = self.exp_year, self.exp_month + 1
        if month > _DECEMBER:
            year, month = year + 1, 1
        return now >= datetime(year, month, 1, tzinfo=UTC)


@dataclass(frozen=True)
class PaymentCard:
    id: UUID
    user_id: UUID
    details: CardDetails
    is_default: bool
    created_at: datetime


@dataclass(frozen=True)
class CardBinding:
    id: UUID
    user_id: UUID
    stored_status: CardBindingStatus
    return_url: str
    card_id: UUID | None
    decline_reason: DeclineReason | None
    created_at: datetime
    expires_at: datetime

    def status(self, now: datetime) -> CardBindingStatus:
        if self.stored_status is CardBindingStatus.PENDING and now >= self.expires_at:
            return CardBindingStatus.EXPIRED
        return self.stored_status


@dataclass(frozen=True)
class PaymentOperation:
    id: UUID
    user_id: UUID
    card_id: UUID
    kind: OperationKind
    amount: int
    parent_id: UUID | None
    idempotency_key: str
    status: OperationStatus
    provider_ref: str | None
    decline_reason: DeclineReason | None
    created_at: datetime

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES


def check_return_url(url: str, web_origins: Collection[str], *, allow_expo_go: bool) -> str:
    """Where the provider's page may send the client back: only to our app or our web client.

    Raises ReturnUrlNotAllowedError. Anything else would let a link to the card page send the
    client to a site that only looks like ours.
    """
    if len(url) > RETURN_URL_MAX_LENGTH or not url.isascii():
        raise ReturnUrlNotAllowedError
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        raise ReturnUrlNotAllowedError
    if parts.scheme == APP_SCHEME:
        return url
    if parts.scheme in EXPO_GO_SCHEMES and allow_expo_go:
        return url
    if parts.scheme in ("http", "https") and f"{parts.scheme}://{parts.netloc}" in web_origins:
        return url
    raise ReturnUrlNotAllowedError


def check_amount(amount: int) -> None:
    if type(amount) is not int or amount <= 0:
        raise ValueError("an amount is a positive number of kopecks")


def check_idempotency_key(key: str) -> None:
    if not 0 < len(key) <= IDEMPOTENCY_KEY_MAX_LENGTH or not key.isascii() or not key.isprintable():
        raise ValueError(f"an idempotency key is 1..{IDEMPOTENCY_KEY_MAX_LENGTH} printable ASCII")


def check_child(
    parent: PaymentOperation, kind: OperationKind, amount: int, siblings: Iterable[PaymentOperation]
) -> None:
    """Whether a capture, void or refund fits the operation it refers to.

    `siblings` are the other operations on the same parent. Declined ones do not count; pending and
    failed ones do, as their outcome is not known yet. Raises PaymentOperationInvalidError.
    """
    live = [sibling for sibling in siblings if sibling.status is not OperationStatus.DECLINED]
    if parent.status is not OperationStatus.SUCCEEDED:
        raise PaymentOperationInvalidError("The operation it refers to has not succeeded.")
    match kind:
        case OperationKind.CAPTURE | OperationKind.VOID:
            if parent.kind is not OperationKind.HOLD:
                raise PaymentOperationInvalidError("Only a hold is captured or voided.")
            if any(sibling.kind in (OperationKind.CAPTURE, OperationKind.VOID) for sibling in live):
                raise PaymentOperationInvalidError("The hold is captured or voided already.")
            if amount > parent.amount:
                raise PaymentOperationInvalidError("A capture cannot exceed the hold.")
        case OperationKind.REFUND:
            if parent.kind not in (OperationKind.CAPTURE, OperationKind.CHARGE):
                raise PaymentOperationInvalidError("Only taken money is refunded.")
            refunded = sum(
                sibling.amount for sibling in live if sibling.kind is OperationKind.REFUND
            )
            if refunded + amount > parent.amount:
                raise PaymentOperationInvalidError("A refund cannot exceed what was taken.")
        case _:
            raise ValueError(f"{kind} does not refer to another operation")


def default_after_removal(cards: Iterable[PaymentCard], removed: UUID) -> PaymentCard | None:
    """The card that becomes the default when the default one is removed: the newest other one."""
    others = [card for card in cards if card.id != removed]
    return max(others, key=lambda card: (card.created_at, card.id), default=None)


def encrypt_card_token(token: str, key: bytes, card_id: UUID) -> bytes:
    # The card id is authenticated data: a ciphertext copied to another card does not decrypt.
    nonce = secrets.token_bytes(_TOKEN_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, token.encode(), card_id.bytes)


def decrypt_card_token(ciphertext: bytes, key: bytes, card_id: UUID) -> str:
    nonce, sealed = ciphertext[:_TOKEN_NONCE_BYTES], ciphertext[_TOKEN_NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, sealed, card_id.bytes).decode()
    except Exception as error:
        raise CardTokenUnreadableError from error
