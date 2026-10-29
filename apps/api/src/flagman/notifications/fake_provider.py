from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar
from uuid import uuid4

import structlog

from flagman.notifications.ports import (
    PushMessage,
    PushReceipt,
    PushReceiptStatus,
    PushTicket,
    PushTicketStatus,
)

logger = structlog.get_logger(__name__)

TICKET_PREFIX = "fake-"
NOT_FAKE_TICKET = "NotFakeTicket"


@dataclass
class FakePushProvider:
    """Delivers nothing: for development and tests, where no phone has to buzz.

    Logs only what kind of event went out, never the token or the text.
    """

    # As Expo's, so the same tickets wait for their receipts in development.
    receipt_delay: ClassVar[timedelta] = timedelta(minutes=15)
    receipt_lifetime: ClassVar[timedelta] = timedelta(hours=24)
    # Off in the worker: a long-running process must not pile up tokens and texts in memory.
    record: bool = True
    sent: list[PushMessage] = field(default_factory=list)
    # Tokens that answer as if the app was removed from the phone.
    unregistered_tokens: set[str] = field(default_factory=set)
    # Tokens whose messages are refused for good, as for a message too big.
    rejected_tokens: set[str] = field(default_factory=set)
    # Calls that take nothing, as if the provider were down.
    failures_left: int = 0
    # Tokens whose messages are taken, but whose receipts say the app was removed.
    unregistered_on_receipt: set[str] = field(default_factory=set)
    # Off: no receipt is ready yet, as in the first minutes after sending.
    receipts_ready: bool = True
    # Which token each ticket went to, while recording; an unknown one of its own is OK.
    tickets: dict[str, str] = field(default_factory=dict)

    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicket]:
        if self.failures_left > 0:
            self.failures_left -= 1
            return [PushTicket(PushTicketStatus.NOT_SENT)] * len(messages)
        tickets: list[PushTicket] = []
        for message in messages:
            if message.token in self.unregistered_tokens:
                tickets.append(PushTicket(PushTicketStatus.DEVICE_NOT_REGISTERED))
                continue
            if message.token in self.rejected_tokens:
                tickets.append(PushTicket(PushTicketStatus.REJECTED))
                continue
            # Unique across restarts, as Expo's are: tickets of an earlier run may still be stored.
            ticket_id = f"{TICKET_PREFIX}{uuid4()}"
            if self.record:
                self.sent.append(message)
                self.tickets[ticket_id] = message.token
            tickets.append(PushTicket(PushTicketStatus.OK, ticket_id))
            logger.info("push_sent_fake", push_event=message.content.data.get("event"))
        return tickets

    async def get_receipts(self, ticket_ids: Sequence[str]) -> dict[str, PushReceipt] | None:
        if not self.receipts_ready:
            return {}
        return {ticket_id: self._receipt(ticket_id) for ticket_id in ticket_ids}

    def _receipt(self, ticket_id: str) -> PushReceipt:
        # Expo's tickets are still stored after switching from it to the fake.
        if not ticket_id.startswith(TICKET_PREFIX):
            return PushReceipt(PushReceiptStatus.FAILED, NOT_FAKE_TICKET)
        if self.tickets.get(ticket_id) in self.unregistered_on_receipt:
            return PushReceipt(PushReceiptStatus.DEVICE_NOT_REGISTERED)
        return PushReceipt(PushReceiptStatus.OK)
