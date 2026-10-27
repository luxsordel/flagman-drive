from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from flagman.notifications.domain import PushContent


@dataclass(frozen=True)
class PushMessage:
    # The device's push token: never logged.
    token: str
    content: PushContent


class PushTicketStatus(StrEnum):
    OK = "ok"
    # The app was removed or its token expired: the token is deleted and never tried again.
    DEVICE_NOT_REGISTERED = "device_not_registered"
    # Anything else the provider refused this one message for; retrying will not help.
    REJECTED = "rejected"


class PushProviderError(Exception):
    """The provider did not take the batch: down, rate limiting us, or rejecting our credentials.
    Nothing in it was delivered, so the whole batch may be tried again."""


class PushProvider(Protocol):
    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicketStatus]:
        """One status per message, in order. Raises PushProviderError."""
        ...
