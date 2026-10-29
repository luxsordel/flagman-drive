from collections.abc import Sequence
from dataclasses import dataclass, field

import structlog

from flagman.notifications.ports import (
    PushMessage,
    PushReceipt,
    PushReceiptStatus,
    PushTicket,
    PushTicketStatus,
)

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
    # Tokens whose messages are refused for good, as for a message too big.
    rejected_tokens: set[str] = field(default_factory=set)
    # Calls that take nothing, as if the provider were down.
    failures_left: int = 0
    # Tokens whose messages are taken, but whose receipts say the app was removed.
    unregistered_on_receipt: set[str] = field(default_factory=set)
    # Off: no receipt is ready yet, as in the first minutes after sending.
    receipts_ready: bool = True
    # Which token each ticket went to, while recording; an unknown ticket gets an OK receipt.
    tickets: dict[str, str] = field(default_factory=dict)
    issued: int = 0

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
            self.issued += 1
            ticket_id = f"fake-{self.issued}"
            if self.record:
                self.sent.append(message)
                self.tickets[ticket_id] = message.token
            tickets.append(PushTicket(PushTicketStatus.OK, ticket_id))
            logger.info("push_sent_fake", push_event=message.content.data.get("event"))
        return tickets

    async def get_receipts(self, ticket_ids: Sequence[str]) -> dict[str, PushReceipt]:
        if not self.receipts_ready:
            return {}
        return {
            ticket_id: PushReceipt(PushReceiptStatus.DEVICE_NOT_REGISTERED)
            if self.tickets.get(ticket_id) in self.unregistered_on_receipt
            else PushReceipt(PushReceiptStatus.OK)
            for ticket_id in ticket_ids
        }
