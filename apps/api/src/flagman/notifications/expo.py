"""Expo Push Service, see docs/adr/0020: one HTTPS API for both APNs and FCM."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx2
import structlog

from flagman.notifications.domain import PUSH_TICKET_ID_MAX_LENGTH
from flagman.notifications.ports import (
    PushMessage,
    PushReceipt,
    PushReceiptStatus,
    PushTicket,
    PushTicketStatus,
)

logger = structlog.get_logger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
# The app creates it on Android (apps/mobile/src/features/notifications/appNotifications.ts).
ANDROID_CHANNEL = "default"
# Expo takes at most 100 messages in one request.
MAX_BATCH = 100
# And at most 1000 receipt ids.
MAX_RECEIPTS_BATCH = 1000
_DEVICE_NOT_REGISTERED = "DeviceNotRegistered"


@dataclass(frozen=True)
class ExpoPushProvider:
    client: httpx2.AsyncClient
    # Only needed when "enhanced push security" is on for the Expo project; never logged.
    access_token: str | None
    timeout_s: float
    url: str = EXPO_PUSH_URL
    receipts_url: str = EXPO_RECEIPTS_URL

    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicket]:
        # Request by request: a failed one leaves the ones before it delivered.
        tickets: list[PushTicket] = []
        for start in range(0, len(messages), MAX_BATCH):
            tickets += await self._send_batch(messages[start : start + MAX_BATCH])
        return tickets

    async def get_receipts(self, ticket_ids: Sequence[str]) -> dict[str, PushReceipt]:
        receipts: dict[str, PushReceipt] = {}
        for start in range(0, len(ticket_ids), MAX_RECEIPTS_BATCH):
            receipts |= await self._get_receipts_batch(
                ticket_ids[start : start + MAX_RECEIPTS_BATCH]
            )
        return receipts

    async def _send_batch(self, messages: Sequence[PushMessage]) -> list[PushTicket]:
        body = [
            {
                "to": message.token,
                "title": message.content.title,
                "body": message.content.body,
                "data": dict(message.content.data),
                "sound": "default",
                # Without it Android gets FCM's normal priority, which a dozing phone may hold
                # back for minutes: too late for a reservation about to end.
                "priority": "high",
                "channelId": ANDROID_CHANNEL,
                "expiration": int(message.expires_at.timestamp()),
            }
            for message in messages
        ]
        tickets = await self._post(self.url, body, "expo_push", list)
        if tickets is None:
            return _not_sent(messages)
        if len(tickets) != len(messages):
            # Expo may have taken them: a retry risks a second copy, which beats losing them.
            logger.error("expo_push_answer_invalid")
            return _not_sent(messages)
        return [_ticket(ticket) for ticket in tickets]

    async def _get_receipts_batch(self, ticket_ids: Sequence[str]) -> dict[str, PushReceipt]:
        receipts = await self._post(
            self.receipts_url, {"ids": list(ticket_ids)}, "expo_receipts", dict
        )
        if receipts is None:
            return {}
        # Expo answers only for the receipts it has ready.
        return {
            ticket_id: _receipt(receipt)
            for ticket_id, receipt in receipts.items()
            if isinstance(ticket_id, str)
        }

    async def _post[T: (list[Any], dict[Any, Any])](
        self, url: str, body: Any, event: str, expected: type[T]
    ) -> T | None:
        """The answer's "data"; None, logged, when there is no usable one."""
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            async with asyncio.timeout(self.timeout_s):
                # The client's own default (5 s per phase) would cut a slow Expo short of the
                # configured timeout.
                response = await self.client.post(
                    url, json=body, headers=headers, timeout=self.timeout_s
                )
        except (TimeoutError, httpx2.TransportError) as error:
            logger.warning(f"{event}_unreachable", error=type(error).__name__)
            return None
        if response.status_code != HTTPStatus.OK:
            # 401 means a wrong EXPO_ACCESS_TOKEN, 429 and 5xx a busy or broken Expo.
            logger.error(f"{event}_failed", status=response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, expected):
            logger.error(f"{event}_answer_invalid")
            return None
        return data


def _not_sent(messages: Sequence[PushMessage]) -> list[PushTicket]:
    return [PushTicket(PushTicketStatus.NOT_SENT)] * len(messages)


def _ticket(ticket: Any) -> PushTicket:
    if isinstance(ticket, dict) and ticket.get("status") == "ok":
        ticket_id = ticket.get("id")
        usable = isinstance(ticket_id, str) and 0 < len(ticket_id) <= PUSH_TICKET_ID_MAX_LENGTH
        return PushTicket(PushTicketStatus.OK, ticket_id if usable else None)
    error = _error(ticket)
    if error == _DEVICE_NOT_REGISTERED:
        return PushTicket(PushTicketStatus.DEVICE_NOT_REGISTERED)
    logger.warning("expo_push_rejected", error=error)
    return PushTicket(PushTicketStatus.REJECTED)


def _receipt(receipt: Any) -> PushReceipt:
    if isinstance(receipt, dict) and receipt.get("status") == "ok":
        return PushReceipt(PushReceiptStatus.OK)
    error = _error(receipt)
    if error == _DEVICE_NOT_REGISTERED:
        return PushReceipt(PushReceiptStatus.DEVICE_NOT_REGISTERED)
    return PushReceipt(PushReceiptStatus.FAILED, error)


def _error(answer: Any) -> str | None:
    # The message text Expo sends back may quote the token; the error code is enough.
    details = answer.get("details") if isinstance(answer, dict) else None
    error = details.get("error") if isinstance(details, dict) else None
    return error if isinstance(error, str) else None
