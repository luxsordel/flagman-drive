from collections.abc import Sequence
from dataclasses import dataclass, field

import structlog

from flagman.notifications.ports import PushMessage, PushProviderError, PushTicketStatus

logger = structlog.get_logger(__name__)


@dataclass
class FakePushProvider:
    """Delivers nothing: for development and tests, where no phone has to buzz.

    Logs only what kind of event went out, never the token or the text.
    """

    sent: list[PushMessage] = field(default_factory=list)
    # Tokens that answer as if the app was removed from the phone.
    unregistered_tokens: set[str] = field(default_factory=set)
    # Batches that fail as if the provider were down, before any is accepted.
    failures_left: int = 0

    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicketStatus]:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise PushProviderError("fake provider is down")
        statuses: list[PushTicketStatus] = []
        for message in messages:
            if message.token in self.unregistered_tokens:
                statuses.append(PushTicketStatus.DEVICE_NOT_REGISTERED)
                continue
            self.sent.append(message)
            statuses.append(PushTicketStatus.OK)
            logger.info("push_sent_fake", push_event=message.content.data.get("event"))
        return statuses
