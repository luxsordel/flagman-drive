from collections.abc import Sequence
from dataclasses import dataclass, field

import structlog

from flagman.notifications.ports import PushMessage, PushTicketStatus

logger = structlog.get_logger(__name__)


@dataclass
class FakePushProvider:
    """Delivers nothing: for development and tests, where no phone has to buzz.

    Logs only what kind of event went out, never the token or the text.
    """

    # Off in the worker: a long-running process must not pile up tokens and texts in memory.
    record: bool = True
    sent: list[PushMessage] = field(default_factory=list)
    # Tokens that answer as if the app was removed from the phone.
    unregistered_tokens: set[str] = field(default_factory=set)
    # Calls that take nothing, as if the provider were down.
    failures_left: int = 0

    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicketStatus]:
        if self.failures_left > 0:
            self.failures_left -= 1
            return [PushTicketStatus.NOT_SENT] * len(messages)
        statuses: list[PushTicketStatus] = []
        for message in messages:
            if message.token in self.unregistered_tokens:
                statuses.append(PushTicketStatus.DEVICE_NOT_REGISTERED)
                continue
            if self.record:
                self.sent.append(message)
            statuses.append(PushTicketStatus.OK)
            logger.info("push_sent_fake", push_event=message.content.data.get("event"))
        return statuses
