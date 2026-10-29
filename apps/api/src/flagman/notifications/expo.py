"""Expo Push Service, see docs/adr/0020: one HTTPS API for both APNs and FCM."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx2
import structlog

from flagman.notifications.ports import PushMessage, PushTicketStatus

logger = structlog.get_logger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# The app creates it on Android (apps/mobile/src/features/notifications/appNotifications.ts).
ANDROID_CHANNEL = "default"
# Expo takes at most 100 messages in one request.
MAX_BATCH = 100
_DEVICE_NOT_REGISTERED = "DeviceNotRegistered"


@dataclass(frozen=True)
class ExpoPushProvider:
    client: httpx2.AsyncClient
    # Only needed when "enhanced push security" is on for the Expo project; never logged.
    access_token: str | None
    timeout_s: float
    url: str = EXPO_PUSH_URL

    async def send(self, messages: Sequence[PushMessage]) -> list[PushTicketStatus]:
        # Request by request: a failed one leaves the ones before it delivered.
        statuses: list[PushTicketStatus] = []
        for start in range(0, len(messages), MAX_BATCH):
            statuses += await self._send_batch(messages[start : start + MAX_BATCH])
        return statuses

    async def _send_batch(self, messages: Sequence[PushMessage]) -> list[PushTicketStatus]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
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
        try:
            async with asyncio.timeout(self.timeout_s):
                # The client's own default (5 s per phase) would cut a slow Expo short of the
                # configured timeout.
                response = await self.client.post(
                    self.url, json=body, headers=headers, timeout=self.timeout_s
                )
        except (TimeoutError, httpx2.TransportError) as error:
            logger.warning("expo_push_unreachable", error=type(error).__name__)
            return _not_sent(messages)
        if response.status_code != HTTPStatus.OK:
            # 401 means a wrong EXPO_ACCESS_TOKEN, 429 and 5xx a busy or broken Expo.
            logger.error("expo_push_failed", status=response.status_code)
            return _not_sent(messages)
        tickets = _tickets(response)
        if tickets is None or len(tickets) != len(messages):
            # Expo may have taken them: a retry risks a second copy, which beats losing them.
            logger.error("expo_push_answer_invalid")
            return _not_sent(messages)
        return [_status(ticket) for ticket in tickets]


def _not_sent(messages: Sequence[PushMessage]) -> list[PushTicketStatus]:
    return [PushTicketStatus.NOT_SENT] * len(messages)


def _tickets(response: httpx2.Response) -> list[Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, list) else None


def _status(ticket: Any) -> PushTicketStatus:
    if isinstance(ticket, dict) and ticket.get("status") == "ok":
        return PushTicketStatus.OK
    details = ticket.get("details") if isinstance(ticket, dict) else None
    error = details.get("error") if isinstance(details, dict) else None
    if error == _DEVICE_NOT_REGISTERED:
        return PushTicketStatus.DEVICE_NOT_REGISTERED
    # The message text Expo sends back may quote the token; the error code is enough.
    logger.warning("expo_push_rejected", error=error if isinstance(error, str) else None)
    return PushTicketStatus.REJECTED
