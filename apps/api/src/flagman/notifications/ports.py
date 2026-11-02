from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from flagman.notifications.domain import PushContent


@dataclass(frozen=True)
class PushMessage:
    # The device's push token: never logged.
    token: str
    content: PushContent
    # The provider drops the message if it cannot reach the phone by then.
    expires_at: datetime


class PushTicketStatus(StrEnum):
    OK = "ok"
    # The app was removed or its token expired: the token is deleted and never tried again.
    DEVICE_NOT_REGISTERED = "device_not_registered"
    # Anything else the provider refused this one message for; retrying will not help.
    REJECTED = "rejected"
    # The provider did not take the message: down, rate limiting us, rejecting our credentials,
    # or its answer was unreadable. The message may be tried again.
    NOT_SENT = "not_sent"


@dataclass(frozen=True)
class PushTicket:
    status: PushTicketStatus
    # Only for a message the provider took: its receipt, asked for later, tells whether APNs or
    # FCM took it in turn.
    ticket_id: str | None = None


class PushReceiptStatus(StrEnum):
    OK = "ok"
    # Often known only here, not in the ticket: the token is deleted.
    DEVICE_NOT_REGISTERED = "device_not_registered"
    # Anything else that kept the message from the phone; only counted.
    FAILED = "failed"


@dataclass(frozen=True)
class PushReceipt:
    status: PushReceiptStatus
    # The provider's error code for a failed one, e.g. "MessageRateExceeded"; never its message
    # text, which may quote the token.
    error: str | None = None


class PushProvider(Protocol):
    @property
    def receipt_delay(self) -> timedelta:
        """How long after sending a receipt is worth asking for."""
        ...

    @property
    def receipt_lifetime(self) -> timedelta:
        """How long the provider keeps a receipt: an older ticket is never asked about."""
        ...

    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicket]:
        """One ticket per message, in order. Never raises: a provider that sends in several
        requests may get some through and not others, and only the rest may be tried again."""
        ...

    async def get_receipts(self, ticket_ids: Sequence[str]) -> dict[str, PushReceipt] | None:
        """The receipts that are ready, by ticket id; None when the provider gave no usable
        answer, so asking it for more now is no use either. Never raises: a ticket missing from
        the answer is asked for again later. A ticket the provider cannot have issued, such as
        one left by another provider, gets a FAILED receipt at once."""
        ...
